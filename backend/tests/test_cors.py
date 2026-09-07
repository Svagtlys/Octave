import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_cors_headers_present() -> None:
    from octave.app import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/api/health",
            headers={"Origin": "http://localhost:5173"},
        )

    assert "access-control-allow-origin" in response.headers
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
