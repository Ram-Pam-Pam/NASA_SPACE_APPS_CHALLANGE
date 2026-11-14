from fastapi import FastAPI

app = FastAPI(
    title="SAR Infrastructure Risk API",
    version="0.1.0",
    description="Ocena zagrożenia infrastruktury liniowej osuwiskami na podstawie danych SAR.",
)


@app.get("/health")
def health():
    return {"status": "ok"}