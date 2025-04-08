import websockets
import json
from firebase_admin import firestore, initialize_app
from datetime import datetime
import firebase_admin
from firebase_functions import scheduler_fn
from google.cloud.firestore import SERVER_TIMESTAMP
import asyncio
import logging
import os
from zoneinfo import ZoneInfo
import time
import requests
import pandas as pd
from dateutil.relativedelta import relativedelta

# Simplify Firebase initialization
if not firebase_admin._apps:
    initialize_app()

db = firestore.client()
logging.basicConfig(level=logging.INFO)


def store_in_firestore(total_capacity: int, usage: int, freespace: int):
    try:
        logging.info("Storing data in Firestore...")
        # Cap the total capacity at 200
        total_capacity = min(total_capacity, 200)

        # Use timezone-aware datetime
        timestamp = datetime.now(ZoneInfo("Europe/Zurich"))
        # Create document ID in format: YYYY-MM-DD-HH-mm-ss
        doc_id = timestamp.strftime("%Y-%m-%d-%H-%M-%S")

        # Get reference to the document with timestamp-based ID
        doc_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("historical_data")
            .document(doc_id)
        )

        # Set the document data
        doc_ref.set(
            {
                "total_capacity": int(total_capacity),
                "usage": int(usage),
                "freespace": int(freespace),
                "freespace_percentage": float((freespace / total_capacity) * 100)
                if total_capacity > 0
                else 0,
                "timestamp": timestamp,
            }
        )

        logging.info(f"Data stored successfully with ID: {doc_id}")
    except Exception as e:
        logging.error(f"Error storing data in Firestore: {e}")


async def websocket_info(uri: str) -> int:
    for attempt in range(3):
        try:
            logging.info(f"Connecting to WebSocket... attempt {attempt + 1}")
            async with websockets.connect(uri) as websocket:
                await websocket.send("all")
                message = await websocket.recv()
                data = json.loads(message)
                for item in data:
                    if item["name"] == "Hallenbad City":
                        total_capacity = item["maxspace"]
                        usage = item["currentfill"]
                        freespace = item["freespace"]
                        logging.info(
                            f"Fetched data - Total Capacity: {total_capacity}, Usage: {usage}, Freespace: {freespace}"
                        )
                        store_in_firestore(total_capacity, usage, freespace)
                        return freespace
        except Exception as e:
            logging.error(f"Error fetching data (attempt {attempt + 1}): {e}")
            if attempt < 2:
                time.sleep(2)  # wait before retry
    logging.error("All retry attempts failed.")
    return None


def fetch_freespace():
    uri = "wss://badi-public.crowdmonitor.ch:9591/api"
    freespace = asyncio.run(websocket_info(uri))
    return freespace


@scheduler_fn.on_schedule(
    schedule="every 30 minutes from 06:00 to 22:00", timezone="Europe/Zurich"
)
def scheduled_fetch_freespace(event: scheduler_fn.ScheduledEvent):
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")


# Prediction related functions
def fetch_historical_data(full_history: bool = False) -> pd.DataFrame:
    """
    Fetch historical data from Firestore for prediction input.

    Args:
        full_history: If True, fetches all historical data. If False, fetches only
                     today's data (since midnight).

    Returns:
        DataFrame with columns:
            - ds: datetime with timezone (Europe/Zurich)
            - y: freespace percentage values (0-100)

    If an error occurs or no data is found, returns an empty DataFrame.
    """
    try:
        # Get reference to collection
        collection_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("historical_data")
        )

        # Apply time filter if not requesting full history
        if not full_history:
            # Get start of today
            today_start = datetime.now(ZoneInfo("Europe/Zurich")).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            collection_ref = collection_ref.where("timestamp", ">=", today_start)

        # Get documents
        docs = collection_ref.stream()
        data = []
        for doc in docs:
            doc_dict = doc.to_dict()
            timestamp = doc_dict["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=ZoneInfo("Europe/Zurich"))
            else:
                timestamp = timestamp.astimezone(ZoneInfo("Europe/Zurich"))
            data.append({"ds": timestamp, "y": doc_dict["freespace_percentage"]})
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"Error fetching historical data: {e}")
        return pd.DataFrame()


def store_predictions(predictions_response: dict):
    """
    Store predictions in Firestore with proper timestamp handling.

    Args:
        predictions_response: Dictionary containing prediction response from DBOS API:
            {
                "message": str,
                "predictions": {
                    "YYYY-MM-DD": {
                        "last_updated": datetime string,
                        "predictions": List[DetailedPrediction],
                        "periods": {
                            "early_morning": {"predicted_freespace_percentage": float, ...},
                            ...
                        }
                    }
                }
            }
    """
    try:
        predictions_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("predictions")
        )

        # Extract predictions from the response
        predictions_data = predictions_response.get("predictions", {})
        for day, prediction in predictions_data.items():
            # Convert last_updated to datetime
            last_updated = datetime.fromisoformat(prediction["last_updated"])
            last_updated_ts = SERVER_TIMESTAMP if last_updated is None else last_updated

            # Convert all prediction timestamps to datetime objects
            processed_predictions = []
            for pred in prediction["predictions"]:
                pred_timestamp = datetime.fromisoformat(pred["timestamp"])
                processed_predictions.append({**pred, "timestamp": pred_timestamp})

            # Store predictions with datetime objects
            predictions_ref.document(day).set(
                {
                    "last_updated": last_updated_ts,
                    "predictions": processed_predictions,
                    "periods": prediction["periods"],
                }
            )

        logging.info(f"Stored predictions for {len(predictions_data)} days")
    except Exception as e:
        logging.error(f"Error storing predictions: {e}")


@scheduler_fn.on_schedule(
    schedule="every 2 hours from 07:01 to 22:00", timezone="Europe/Zurich"
)
def scheduled_run_dbos_predictions(event: scheduler_fn.ScheduledEvent):
    """
    Scheduled function to fetch recent historical data and get predictions from DBOS.

    Fetches today's historical data from Firestore, sends it to the DBOS
    prediction endpoint for incremental model updates, and stores the returned predictions.
    """
    try:
        # Get recent historical data only
        df = fetch_historical_data(full_history=False)
        if df.empty:
            logging.error("No historical data available")
            return

        dbos_url = os.getenv("DBOS_URL")
        if not dbos_url:
            logging.error("DBOS_URL not configured")
            return

        payload = {
            "timestamps": [ts.isoformat() for ts in df["ds"]],
            "values": df["y"].tolist(),
            "days": 5,
        }

        # Send request to DBOS endpoint
        response = requests.post(dbos_url + "/predict", json=payload)
        response.raise_for_status()
        prediction_response = response.json()

        # Store predictions in Firestore
        store_predictions(prediction_response)
        logging.info("DBOS predictions completed and stored")

    except Exception as e:
        logging.error(f"Error in DBOS prediction workflow: {e}")


@scheduler_fn.on_schedule(
    schedule="0 4 * * *",
    timezone="Europe/Zurich",  # Run at 4 AM daily
)
def scheduled_full_model_fit(event: scheduler_fn.ScheduledEvent):
    """
    Daily scheduled task to fit a full model using complete historical data.

    Fetches all historical data and sends it to the DBOS full model training endpoint.
    This ensures the model maintains long-term patterns while avoiding concept drift.
    """
    try:
        # Get complete historical data
        df = fetch_historical_data(full_history=True)
        if df.empty:
            logging.error("No historical data available")
            return

        dbos_url = os.getenv("DBOS_URL")
        if not dbos_url:
            logging.error("DBOS_URL not configured")
            return

        payload = {
            "timestamps": [ts.isoformat() for ts in df["ds"]],
            "values": df["y"].tolist(),
            "days": 5,
            "is_full_history": True,
        }

        # Send request to DBOS endpoint
        response = requests.post(dbos_url + "/fit_full_model", json=payload)
        response.raise_for_status()
        prediction_response = response.json()

        # Store predictions in Firestore
        store_predictions(prediction_response)
        logging.info("Full model training completed and new predictions stored")

    except Exception as e:
        logging.error(f"Error in full model training workflow: {e}")


@scheduler_fn.on_schedule(
    schedule="0 1 * * 1",  # Run at 1 AM every Monday
    timezone="Europe/Zurich",
)
def scheduled_cleanup_old_predictions(event: scheduler_fn.ScheduledEvent):
    """
    Weekly scheduled task to clean up old prediction data.
    
    Deletes prediction documents that have a last_updated timestamp older than one month.
    This helps keep the database size manageable and removes outdated predictions.
    """
    try:
        # Calculate cutoff date (1 month ago)
        cutoff_date = datetime.now(ZoneInfo("Europe/Zurich")) - relativedelta(months=1)
        logging.info(f"Cleaning up predictions older than: {cutoff_date.isoformat()}")
        
        # Get reference to predictions collection
        predictions_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("predictions")
        )
        
        # Get all prediction documents
        docs = predictions_ref.stream()
        deleted_count = 0
        
        for doc in docs:
            doc_data = doc.to_dict()
            # Check if last_updated exists and is older than cutoff date
            if "last_updated" in doc_data:
                last_updated = doc_data["last_updated"]
                
                # Convert to datetime if it's a timestamp
                if not isinstance(last_updated, datetime):
                    try:
                        last_updated = datetime.fromisoformat(str(last_updated))
                    except (ValueError, TypeError):
                        logging.warning(f"Invalid timestamp format in document: {doc.id}")
                        continue
                
                # Add timezone info if missing
                if last_updated.tzinfo is None:
                    last_updated = last_updated.replace(tzinfo=ZoneInfo("Europe/Zurich"))
                
                # Delete if older than cutoff date
                if last_updated < cutoff_date:
                    doc.reference.delete()
                    deleted_count += 1
        
        logging.info(f"Deleted {deleted_count} outdated prediction documents")
        
    except Exception as e:
        logging.error(f"Error cleaning up old predictions: {e}")


if __name__ == "__main__":
    # For local testing
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")
