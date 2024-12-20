import websockets
import json
from firebase_admin import firestore, credentials
from datetime import datetime
import firebase_admin
from firebase_functions import scheduler_fn
import asyncio
import logging
import os
from dotenv import load_dotenv
import pandas as pd
from prophet import Prophet

# Initialize Firebase
if not firebase_admin._apps:
    load_dotenv()
    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        cred = credentials.Certificate(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
        firebase_admin.initialize_app(cred)
    else:
        firebase_admin.initialize_app()

# Ensure Firestore client is initialized
db = firestore.client()

# Configure logging
logging.basicConfig(level=logging.INFO)


def store_in_firestore(total_capacity: int, usage: int, freespace: int):
    try:
        logging.info("Storing data in Firestore...")
        # Cap the total capacity at 200
        total_capacity = min(total_capacity, 200)
        # Create a new document in the historical_data subcollection with an auto-generated ID
        collection_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("historical_data")
        )
        collection_ref.add(
            {
                "total_capacity": int(total_capacity),
                "usage": int(usage),
                "freespace": int(freespace),
                "freespace_percentage": float((freespace / total_capacity) * 100)
                if total_capacity > 0
                else 0,
                "timestamp": datetime.utcnow(),
            }
        )
        logging.info("Data stored successfully.")
    except Exception as e:
        logging.error(f"Error storing data in Firestore: {e}")


async def websocket_info(uri: str) -> int:
    try:
        logging.info("Connecting to WebSocket...")
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
        logging.error(f"Error fetching data: {e}")
    return None


def fetch_freespace():
    uri = "wss://badi-public.crowdmonitor.ch:9591/api"
    freespace = asyncio.run(websocket_info(uri))
    return freespace


def fetch_historical_data():
    try:
        logging.info("Fetching historical data from Firestore...")
        collection_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("historical_data")
        )
        docs = collection_ref.stream()
        data = []
        for doc in docs:
            doc_dict = doc.to_dict()
            # Remove timezone information from the timestamp
            timestamp = doc_dict["timestamp"].replace(tzinfo=None)
            data.append({"ds": timestamp, "y": doc_dict["freespace_percentage"]})
        logging.info("Historical data fetched successfully.")
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"Error fetching historical data: {e}")
        return pd.DataFrame()


def train_time_series_model(data):
    try:
        logging.info("Training time series model...")
        model = Prophet(
            weekly_seasonality=True,  # Enable weekly patterns
            daily_seasonality=True,  # Enable daily patterns
            yearly_seasonality=False,  # Disable yearly patterns as we don't have enough data
            seasonality_mode="multiplicative",  # Use multiplicative seasonality for percentage data
        )

        # Add Swiss holidays as special days
        model.add_country_holidays(country_name="CH")

        # Fit the model
        model.fit(data)
        logging.info("Model trained successfully.")
        return model
    except Exception as e:
        logging.error(f"Error training time series model: {e}")
        return None


def make_predictions(model, days=5):
    try:
        logging.info("Making predictions...")
        now = pd.Timestamp.now()
        end_date = now + pd.Timedelta(days=days)

        # Generate future dates for the remaining hours of the current day
        current_day_end = now.normalize() + pd.Timedelta(hours=22)
        # Start at the next half hour
        start_time = now.ceil("30min")
        future_dates = pd.date_range(
            start=start_time, end=current_day_end, freq="30min"
        )

        # Generate future dates for the specified number of future days
        for day in range(1, days + 1):
            day_start = (now + pd.Timedelta(days=day)).normalize() + pd.Timedelta(
                hours=6
            )
            day_end = (now + pd.Timedelta(days=day)).normalize() + pd.Timedelta(
                hours=22
            )
            future_dates = future_dates.append(
                pd.date_range(start=day_start, end=day_end, freq="30min")
            )

        # Filter to keep only XX:30 timestamps
        # future_dates = future_dates[future_dates.minute == 30]
        future = pd.DataFrame({"ds": future_dates})

        forecast = model.predict(future)
        logging.info("Predictions made successfully.")

        # Clip the predicted values to the range 0-100%
        forecast["yhat"] = forecast["yhat"].clip(lower=0, upper=100)
        forecast["yhat_lower"] = forecast["yhat_lower"].clip(lower=0, upper=100)
        forecast["yhat_upper"] = forecast["yhat_upper"].clip(lower=0, upper=100)

        return forecast
    except Exception as e:
        logging.error(f"Error making predictions: {e}")
        return pd.DataFrame()


def store_predictions(forecast):
    try:
        logging.info("Storing predictions in Firestore...")
        collection_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("predictions")
        )
        for _, row in forecast.iterrows():
            # Use the timestamp as the document ID
            doc_id = row["ds"].isoformat()
            doc_ref = collection_ref.document(doc_id)
            doc_ref.set(
                {
                    "timestamp": row["ds"],
                    "predicted_freespace_percentage": row["yhat"],
                    "lower_bound": row["yhat_lower"],
                    "upper_bound": row["yhat_upper"],
                },
                merge=True,
            )
        logging.info("Predictions stored successfully.")
    except Exception as e:
        logging.error(f"Error storing predictions in Firestore: {e}")


@scheduler_fn.on_schedule(schedule="every 3 hours from 06:00 to 22:00")
def scheduled_run_predictions(event: scheduler_fn.ScheduledEvent):
    historical_data = fetch_historical_data()
    if not historical_data.empty:
        model = train_time_series_model(historical_data)
        if model:
            forecast = make_predictions(model, days=5)
            store_predictions(forecast)
            logging.info("Predictions updated.")


@scheduler_fn.on_schedule(
    schedule="every 10 minutes from 06:00 to 22:00", timezone="Europe/Zurich"
)
def scheduled_fetch_freespace(event: scheduler_fn.ScheduledEvent):
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")


if __name__ == "__main__":
    # For local testing only
    freespace = fetch_freespace()
    logging.info(f"Hallenbad City freespace: {freespace}")

    # Test model training
    historical_data = fetch_historical_data()
    if not historical_data.empty:
        model = train_time_series_model(historical_data)
        if model:
            forecast = make_predictions(model)
            print(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]])
            store_predictions(forecast)
