from fastapi import FastAPI

from app.api.files import router as files_router
from app.api.jobs import router as jobs_router


app = FastAPI(title="Reswip Lead Acquisition")
app.include_router(files_router)
app.include_router(jobs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
