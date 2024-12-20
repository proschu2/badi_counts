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
            n_changepoints=30,
            daily_seasonality=False,  # Disable default daily seasonality
            weekly_seasonality=True,
            changepoint_prior_scale=0.1,
            seasonality_mode="multiplicative"
        )
        
        # Add Swiss holidays
        model.add_country_holidays(country_name="CH")
        
        # Add custom daily seasonality with 4 main periods
        model.add_seasonality(
            name='daily_periods',
            period=1,  # 1 day
            fourier_order=5,  # Higher order to capture more complex patterns
            condition_name=None
        )
        
        # Add time-of-day segments as additional regressors
        times = pd.DataFrame({'ds': data['ds']})
        hour = times['ds'].dt.hour
        
        # Early morning period (6-11)
        times['morning'] = ((hour >= 6) & (hour < 11)).astype(int)
        # Lunch period (11-13)
        times['lunch'] = ((hour >= 11) & (hour < 13)).astype(int)
        # Afternoon period (13-16)
        times['afternoon'] = ((hour >= 13) & (hour < 16)).astype(int)
        # Evening period (16-22)
        times['evening'] = ((hour >= 16) & (hour < 22)).astype(int)
        
        # Add the time periods to the model
        for period in ['morning', 'lunch', 'afternoon', 'evening']:
            model.add_regressor(period)
            data[period] = times[period]
        
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

        # Create future DataFrame
        future = pd.DataFrame({"ds": future_dates})
        
        # Add time period regressors
        hour = future['ds'].dt.hour
        future['morning'] = ((hour >= 6) & (hour < 11)).astype(int)
        future['lunch'] = ((hour >= 11) & (hour < 13)).astype(int)
        future['afternoon'] = ((hour >= 13) & (hour < 16)).astype(int)
        future['evening'] = ((hour >= 16) & (hour < 22)).astype(int)

        forecast = model.predict(future)
        logging.info("Predictions made successfully.")

        # Clip the predicted values
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

        # Group predictions by day
        forecasts_by_day = {}
        for _, row in forecast.iterrows():
            day = row["ds"].strftime("%Y-%m-%d")
            hour = row["ds"].hour
            
            # Determine time period
            time_period = None
            if 6 <= hour < 11:
                time_period = "morning"
            elif 11 <= hour < 13:
                time_period = "lunch"
            elif 13 <= hour < 16:
                time_period = "afternoon"
            elif 16 <= hour < 22:
                time_period = "evening"
            
            if day not in forecasts_by_day:
                forecasts_by_day[day] = {
                    "predictions": [],
                    "periods": {
                        "morning": {"value": 0, "count": 0},
                        "lunch": {"value": 0, "count": 0},
                        "afternoon": {"value": 0, "count": 0},
                        "evening": {"value": 0, "count": 0}
                    }
                }

            # Add to main predictions list
            forecasts_by_day[day]["predictions"].append({
                "timestamp": row["ds"],
                "predicted_freespace_percentage": float(row["yhat"]),
                "lower_bound": float(row["yhat_lower"]),
                "upper_bound": float(row["yhat_upper"]),
                "time_period": time_period
            })
            
            # Accumulate values for time period averages
            if time_period:
                forecasts_by_day[day]["periods"][time_period]["value"] += float(row["yhat"])
                forecasts_by_day[day]["periods"][time_period]["count"] += 1

        # Calculate averages and prepare final data
        predictions_ref = (
            db.collection("freespace_data")
            .document("Hallenbad_City")
            .collection("predictions")
        )

        for day, data in forecasts_by_day.items():
            # Calculate final average for each period
            period_predictions = {}
            for period, values in data["periods"].items():
                if values["count"] > 0:
                    period_predictions[period] = {
                        "predicted_freespace_percentage": round(values["value"] / values["count"], 1),
                        "period": period
                    }
                else:
                    period_predictions[period] = {
                        "predicted_freespace_percentage": 0,
                        "period": period
                    }

            # Sort the main predictions list
            data["predictions"] = sorted(data["predictions"], key=lambda x: x["timestamp"])
            
            # Store in Firestore
            predictions_ref.document(day).set({
                "last_updated": datetime.utcnow(),
                "predictions": data["predictions"],
                "periods": period_predictions
            })

        logging.info(f"Stored predictions for {len(forecasts_by_day)} days")
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
