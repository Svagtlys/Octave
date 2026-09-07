import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_version_endpoint_returns_version() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/version")

    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "env" in data
    assert isinstance(data["version"], str)
    assert isinstance(data["env"], str)
