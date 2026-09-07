import pytest
from httpx import ASGITransport, AsyncClient


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
