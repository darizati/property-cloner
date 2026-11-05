"""Set/clear a CloneProfile's recurring schedule."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.schemas import ScheduleUpdate
from app.services import clone_service
from app.services.scheduler_service import job_id_for_profile, scheduler_backend

router = APIRouter(prefix="/api/profiles", tags=["schedules"])


@router.put("/{profile_id}/schedule")
def update_schedule(profile_id: int, payload: ScheduleUpdate, db: Session = Depends(get_db)) -> dict:
    profile = db.get(models.CloneProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.schedule_cron = payload.schedule_cron
    db.commit()

    job_id = job_id_for_profile(profile.id)
    if payload.schedule_cron:
        scheduler_backend.upsert_job(job_id, payload.schedule_cron, clone_service.rerun_scheduled, (profile.id,))
    else:
        scheduler_backend.remove_job(job_id)
    return {"profile_id": profile.id, "schedule_cron": profile.schedule_cron}
