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
