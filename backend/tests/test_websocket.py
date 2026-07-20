import json

from starlette.testclient import TestClient


def test_websocket_echo() -> None:
    from octave.app import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text(json.dumps({"type": "ping", "payload": "hello"}))
            data = ws.receive_text()
            response = json.loads(data)

    assert response["type"] == "pong"
    assert response["payload"] == "hello"
