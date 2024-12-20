import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
from fbprophet import Prophet
import logging

# Initialize Firebase
cred = credentials.Certificate("serviceAccountKey.json")
firebase_admin.initialize_app(cred)

# Ensure Firestore client is initialized
db = firestore.client()

# Configure logging
logging.basicConfig(level=logging.INFO)

def fetch_historical_data():
    try:
        logging.info("Fetching historical data from Firestore...")
        collection_ref = db.collection("freespace_data").document("Hallenbad_City").collection("historical_data")
        docs = collection_ref.stream()
        data = []
        for doc in docs:
            doc_dict = doc.to_dict()
            # Remove timezone information from the timestamp
            timestamp = doc_dict["timestamp"].replace(tzinfo=None)
            data.append({
                "ds": timestamp,
                "y": doc_dict["freespace"]
            })
        logging.info("Historical data fetched successfully.")
        return pd.DataFrame(data)
    except Exception as e:
        logging.error(f"Error fetching historical data: {e}")
        return pd.DataFrame()

def train_time_series_model(data):
    try:
        logging.info("Training time series model...")
        model = Prophet()
        model.fit(data)
        logging.info("Model trained successfully.")
        return model
    except Exception as e:
        logging.error(f"Error training time series model: {e}")
        return None

def make_predictions(model, periods=168):
    try:
        logging.info("Making predictions...")
        future = model.make_future_dataframe(periods=periods, freq='H')
        forecast = model.predict(future)
        logging.info("Predictions made successfully.")
        return forecast
    except Exception as e:
        logging.error(f"Error making predictions: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    # Fetch historical data
    historical_data = fetch_historical_data()
    if not historical_data.empty:
        # Train the time series model
        model = train_time_series_model(historical_data)
        if model:
            # Make predictions for the next week (168 hours)
            forecast = make_predictions(model, periods=168)
            print(forecast[['ds', 'yhat', 'yhat_lower', 'yhat_upper']])
