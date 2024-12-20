import asyncio
from main import websocket_info


# Test the websocket_info function
async def test_websocket_info():
    uri = "wss://badi-public.crowdmonitor.ch:9591/api"  # Updated WebSocket URL
    freespace = await websocket_info(uri)
    print(f"Hallenbad City freespace: {freespace}")


# Run the test
asyncio.run(test_websocket_info())
