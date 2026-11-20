import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octave.db.errors import OwnerParticipantMissingError
from octave.middleware import add_error_handlers


@pytest.mark.asyncio
async def test_404_returns_json_error() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/nonexistent")

    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert "detail" in data


@pytest.mark.asyncio
async def test_db_error_maps_to_500_json() -> None:
    """DbError is the db-plane's single map point: invariant failures -> 500
    JSON with the error name (issue #121)."""
    app = FastAPI()
    add_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise OwnerParticipantMissingError("u_ghost")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "OwnerParticipantMissingError"
    assert "u_ghost" in data["detail"]
