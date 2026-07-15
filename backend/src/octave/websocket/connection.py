import json
from fastapi import APIRouter, WebSocket

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            response = {
                "type": "pong",
                "payload": message.get("payload", ""),
            }
            await websocket.send_text(json.dumps(response))
    except Exception:
        await websocket.close()
