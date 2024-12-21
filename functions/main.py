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
from zoneinfo import ZoneInfo  # Add this import
from prophet.diagnostics import cross_validation, performance_metrics
from sklearn.metrics import mean_absolute_error, mean_squared_error
import numpy as np

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
        # Use timezone-aware datetime
        timestamp = datetime.now(ZoneInfo("Europe/Zurich"))
        collection_ref.add(
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
            # Ensure timestamp is timezone-aware
            timestamp = doc_dict["timestamp"]
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=ZoneInfo("Europe/Zurich"))
            else:
                # Convert to Europe/Zurich timezone if it's not already
                timestamp = timestamp.astimezone(ZoneInfo("Europe/Zurich"))
            data.append({"ds": timestamp, "y": doc_dict["freespace_percentage"]})
        logging.info("Historical data fetched successfully.")
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"Error fetching historical data: {e}")
        return pd.DataFrame()


def train_time_series_model(data):
    try:
        logging.info("Training time series model...")
        data["ds"] = pd.to_datetime(data["ds"]).dt.tz_localize(None)
        
        # Calculate training data size in days
        days_of_data = (data['ds'].max() - data['ds'].min()).days
        logging.info(f"Training with {days_of_data} days of data")

        """
        TODO: When you have 1-2 weeks of data, update the model configuration to:
        
        if days_of_data >= 7:
            model = Prophet(
                daily_seasonality=True,     # Keep daily patterns
                weekly_seasonality=True,    # Enable weekly patterns
                yearly_seasonality=False,   # Still too early for yearly patterns
                changepoint_prior_scale=0.1,  # Increase flexibility
                seasonality_mode="multiplicative",  # Switch to multiplicative
                seasonality_prior_scale=10.0,  # Increase seasonal patterns strength
                holidays_prior_scale=10.0,   # Increase holiday effects
            )
            
            # Add more detailed seasonality
            model.add_seasonality(
                name='weekly',
                period=7,
                fourier_order=3,  # More harmonics for weekly pattern
                prior_scale=10
            )
            
            # Cross-validation parameters can be increased
            df_cv = cross_validation(
                model,
                initial='7 days',    # Use first week as training
                period='1 day',      # Test on each day
                horizon='2 days'     # Predict 2 days ahead
            )
        """

        # Current configuration for limited data
        model = Prophet(
            daily_seasonality=True,     
            weekly_seasonality=False,    
            yearly_seasonality=False,    
            changepoint_prior_scale=0.05,
            seasonality_mode="additive",   
            seasonality_prior_scale=5.0,   
            holidays_prior_scale=0.1,      
        )

        # Only add holidays if we have more than 3 days of data
        if days_of_data > 3:
            model.add_country_holidays(country_name="CH")
            logging.info("Added holiday effects to model")
        else:
            logging.info("Skipping holiday effects due to limited data")

        # Add more granular time features
        data["hour_sin"] = np.sin(2 * np.pi * data["ds"].dt.hour / 24)
        data["hour_cos"] = np.cos(2 * np.pi * data["ds"].dt.hour / 24)
        data["weekday"] = data["ds"].dt.weekday

        # Time periods with more granular divisions
        times = pd.DataFrame({"ds": data["ds"]})
        hour = times["ds"].dt.hour

        periods = {
            "early_morning": (6, 9),
            "late_morning": (9, 11),
            "lunch": (11, 13),
            "afternoon": (13, 16),
            "after_work": (16, 19),
            "evening": (19, 22),
            "peak_hours": (17, 19),  # New: specific peak hours
            "weekend_day": (10, 18),  # New: weekend specific period
        }

        # Add time periods and special conditions
        for period_name, (start, end) in periods.items():
            if period_name == "weekend_day":
                times[period_name] = (
                    (hour >= start) & (hour < end) & (times["ds"].dt.weekday >= 5)
                ).astype(int)
            else:
                times[period_name] = ((hour >= start) & (hour < end)).astype(int)
            model.add_regressor(period_name)
            data[period_name] = times[period_name]

        # Fit the model
        model.fit(data)

        # Perform cross-validation
        try:
            df_cv = cross_validation(
                model, initial="2 days", period="3 days", horizon="3 days"
            )
            metrics = performance_metrics(df_cv)
            logging.info(f"Cross-validation metrics: {metrics}")
        except Exception as e:
            logging.warning(f"Cross-validation failed: {e}")
            metrics = None

        logging.info("Model trained successfully.")
        return model, metrics
    except Exception as e:
        logging.error(f"Error training time series model: {e}")
        return None, None


def make_predictions(model, days=5):
    try:
        logging.info("Making predictions...")
        now = pd.Timestamp.now(tz=ZoneInfo("Europe/Zurich"))

        # Calculate number of 30-minute intervals for the prediction period
        periods = days * 24 * 2  # days * hours * 2 (30-min intervals)

        # Create future dataframe
        future = model.make_future_dataframe(
            periods=periods, freq="30min", include_history=False
        )

        # Add the same features as in training
        hour = future["ds"].dt.hour
        future["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        future["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        future["weekday"] = future["ds"].dt.weekday

        # Add time periods
        periods = {
            "early_morning": (6, 9),
            "late_morning": (9, 11),
            "lunch": (11, 13),
            "afternoon": (13, 16),
            "after_work": (16, 19),
            "evening": (19, 22),
            "peak_hours": (17, 19),
            "weekend_day": (10, 18),
        }

        for period_name, (start, end) in periods.items():
            if period_name == "weekend_day":
                future[period_name] = (
                    (hour >= start) & (hour < end) & (future["ds"].dt.weekday >= 5)
                ).astype(int)
            else:
                future[period_name] = ((hour >= start) & (hour < end)).astype(int)

        # Filter out non-operating hours (before 6 AM and after 10 PM)
        future = future[(future["ds"].dt.hour >= 6) & (future["ds"].dt.hour < 22)]

        # Generate predictions
        forecast = model.predict(future)
        logging.info(f"Forecast generated with {len(forecast)} entries.")

        # Clip predictions to valid range
        forecast["yhat"] = forecast["yhat"].clip(lower=0, upper=100)
        forecast["yhat_lower"] = forecast["yhat_lower"].clip(lower=0, upper=100)
        forecast["yhat_upper"] = forecast["yhat_upper"].clip(lower=0, upper=100)

        return forecast
    except Exception as e:
        logging.error(f"Error making predictions: {e}")
        return pd.DataFrame()


def evaluate_model(y_true, y_pred):
    try:
        metrics = {
            "MAE": mean_absolute_error(y_true, y_pred),
            "RMSE": np.sqrt(mean_squared_error(y_true, y_pred)),
            "MAPE": np.mean(np.abs((y_true - y_pred) / y_true)) * 100,
        }
        logging.info(f"Model evaluation metrics: {metrics}")
        return metrics
    except Exception as e:
        logging.error(f"Error evaluating model: {e}")
        return None


def store_predictions(forecast):
    try:
        logging.info("Storing predictions in Firestore...")

        if (forecast.empty):
            logging.warning("Forecast DataFrame is empty. No predictions to store.")
            return

        # Group predictions by day
        forecasts_by_day = {}
        for _, row in forecast.iterrows():
            # Directly localize timestamp to Europe/Zurich
            timestamp = pd.Timestamp(row["ds"]).tz_localize("Europe/Zurich")
            day = timestamp.strftime("%Y-%m-%d")
            hour = timestamp.hour

            # Updated time period determination
            time_period = None
            if 6 <= hour < 9:
                time_period = "early_morning"
            elif 9 <= hour < 11:
                time_period = "late_morning"
            elif 11 <= hour < 13:
                time_period = "lunch"
            elif 13 <= hour < 16:
                time_period = "afternoon"
            elif 16 <= hour < 19:
                time_period = "after_work"
            elif 19 <= hour < 22:
                time_period = "evening"

            if day not in forecasts_by_day:
                forecasts_by_day[day] = {
                    "predictions": [],
                    "periods": {
                        "early_morning": {"value": 0, "count": 0},
                        "late_morning": {"value": 0, "count": 0},
                        "lunch": {"value": 0, "count": 0},
                        "afternoon": {"value": 0, "count": 0},
                        "after_work": {"value": 0, "count": 0},
                        "evening": {"value": 0, "count": 0},
                    },
                }

            # Add to main predictions list with timezone-aware timestamp
            forecasts_by_day[day]["predictions"].append(
                {
                    "timestamp": timestamp.to_pydatetime(),
                    "predicted_freespace_percentage": float(row["yhat"]),
                    "lower_bound": float(row["yhat_lower"]),
                    "upper_bound": float(row["yhat_upper"]),
                    "time_period": time_period,
                }
            )

            # Accumulate values for time period averages
            if time_period:
                forecasts_by_day[day]["periods"][time_period]["value"] += float(
                    row["yhat"]
                )
                forecasts_by_day[day]["periods"][time_period]["count"] += 1

        if not forecasts_by_day:
            logging.warning("No forecasts grouped by day. Nothing to store.")
            return

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
                        "predicted_freespace_percentage": round(
                            values["value"] / values["count"], 1
                        ),
                        "period": period,
                    }
                else:
                    period_predictions[period] = {
                        "predicted_freespace_percentage": 0,
                        "period": period,
                    }

            # Sort the main predictions list
            data["predictions"] = sorted(
                data["predictions"], key=lambda x: x["timestamp"]
            )

            # Store in Firestore with timezone-aware timestamps
            predictions_ref.document(day).set(
                {
                    "last_updated": datetime.now(ZoneInfo("Europe/Zurich")),
                    "predictions": data["predictions"],
                    "periods": period_predictions,
                }
            )
            logging.info(f"Stored predictions for {day}.")

        logging.info(f"Stored predictions for {len(forecasts_by_day)} days.")
    except Exception as e:
        logging.error(f"Error storing predictions in Firestore: {e}")


@scheduler_fn.on_schedule(schedule="every 3 hours from 06:00 to 22:00")
def scheduled_run_predictions(event: scheduler_fn.ScheduledEvent):
    historical_data = fetch_historical_data()
    if not historical_data.empty:
        model, metrics = train_time_series_model(historical_data)
        if model:
            forecast = make_predictions(model, days=5)
            store_predictions(forecast)
            logging.info("Predictions updated.")
            if metrics:
                logging.info(f"Model performance metrics: {metrics}")


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
        model, metrics = train_time_series_model(historical_data)
        if model:
            forecast = make_predictions(model)
            print(forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]])
            store_predictions(forecast)
