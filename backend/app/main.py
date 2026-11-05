"""FastAPI app entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from app import models  # noqa: F401 - ensures models are registered before create_all
from app.api import clones, properties, schedules
from app.config import settings
from app.db import SessionLocal, initialize_database
from app.services import clone_service
from app.services.scheduler_service import job_id_for_profile, scheduler_backend


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()

    # Re-register any persisted schedules — APScheduler's job store is in-memory,
    # so this must run on every process start.
    scheduler_backend.start()
    db = SessionLocal()
    try:
        for profile in db.query(models.CloneProfile).filter(
            models.CloneProfile.schedule_cron.isnot(None)
        ):
            scheduler_backend.upsert_job(
                job_id_for_profile(profile.id), profile.schedule_cron,
                clone_service.rerun_scheduled, (profile.id,),
            )
    finally:
        db.close()

    yield


app = FastAPI(title="Property Cloner", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties.router)
app.include_router(clones.router)
app.include_router(schedules.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


class FrontendFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or "." in Path(path).name:
                raise
            return await super().get_response("index.html", scope)


frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", FrontendFiles(directory=frontend_dist, html=True), name="frontend")
