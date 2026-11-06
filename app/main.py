from fastapi import FastAPI

from app.settings import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
