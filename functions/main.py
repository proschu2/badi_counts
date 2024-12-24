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
    schedule="every 10 minutes from 06:00 to 22:00", timezone="Europe/Zurich"
)
def scheduled_fetch_freespace(event: scheduler_fn.ScheduledEvent):
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")


# Prediction related functions
def fetch_historical_data():
    """Fetch historical data from Firestore"""
    try:
        collection_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("historical_data")
        )
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


def store_predictions(predictions_data: dict):
    """Store predictions in Firestore with proper timestamp handling"""
    try:
        predictions_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("predictions")
        )

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
    schedule="every 2 hours from 06:00 to 22:00", timezone="Europe/Zurich"
)
def scheduled_run_dbos_predictions(event: scheduler_fn.ScheduledEvent):
    """Fetch data and send to DBOS endpoint for predictions"""
    try:
        # Get historical data
        df = fetch_historical_data()
        if df.empty:
            logging.error("No historical data available")
            return

        dbos_url = os.getenv("DBOS_PREDICT_URL")
        if not dbos_url:
            logging.error("DBOS_PREDICT_URL not configured")
            return

        payload = {
            "timestamps": [ts.isoformat() for ts in df["ds"]],
            "values": df["y"].tolist(),
            "days": 5,
        }

        # Send request to DBOS endpoint
        response = requests.post(dbos_url, json=payload)
        response.raise_for_status()
        predictions = response.json()

        # Store predictions in Firestore
        store_predictions(predictions)
        logging.info("DBOS predictions completed and stored")

    except Exception as e:
        logging.error(f"Error in DBOS prediction workflow: {e}")


if __name__ == "__main__":
    # For local testing
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")
