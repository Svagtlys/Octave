from fastapi import FastAPI

app = FastAPI(title="Octave Backend")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
