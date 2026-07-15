import os
from importlib import metadata
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict[str, str]:
    try:
        v = metadata.version("octave-backend")
    except metadata.PackageNotFoundError:
        v = "unknown"
    env = os.getenv("OCTAVE_ENV", "development")
    return {"version": v, "env": env}
