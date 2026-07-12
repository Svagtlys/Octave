from fastapi import FastAPI

app = FastAPI(title="Octave Backend")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
