"""CloneProfile CRUD + run-now + job history endpoints."""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models
from app.db import get_db
from app.schemas import CloneJobOut, CloneProfileCreate, CloneProfileOut
from app.services import clone_service

router = APIRouter(prefix="/api", tags=["clones"])


@router.post("/profiles", response_model=CloneProfileOut)
def create_profile(payload: CloneProfileCreate, db: Session = Depends(get_db)) -> CloneProfileOut:
    profile = models.CloneProfile(
        name=payload.name,
        product_id=payload.product_id,
        production_property_name=payload.production.property_name,
        production_contract_id=payload.production.contract_id,
        production_account_name=payload.production.account_name,
        production_account_type=payload.production.account_type,
        sqa_property_name=payload.shared.property_name,
        sqa_contract_id=payload.shared.contract_id,
        sqa_account_name=payload.shared.account_name,
        sqa_account_type=payload.shared.account_type,
        sqa_group_id=payload.sqa_group_id,
        sqa_edge_hostname_secure=payload.sqa_edge_hostname_secure,
        sqa_edge_hostname_nonsecure=payload.sqa_edge_hostname_nonsecure,
        policies=payload.policies,
        field_overrides=payload.field_overrides,
        notify_emails=payload.notify_emails,
        attempt_activation=payload.attempt_activation,
        schedule_cron=payload.schedule_cron,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    if profile.schedule_cron:
        from app.services.scheduler_service import job_id_for_profile, scheduler_backend
        scheduler_backend.upsert_job(
            job_id_for_profile(profile.id), profile.schedule_cron,
            clone_service.rerun_scheduled, (profile.id,),
        )
    return CloneProfileOut.model_validate(profile)


@router.get("/profiles", response_model=list[CloneProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[CloneProfileOut]:
    profiles = db.query(models.CloneProfile).order_by(models.CloneProfile.id.desc()).all()
    return [CloneProfileOut.model_validate(p) for p in profiles]


@router.get("/profiles/{profile_id}", response_model=CloneProfileOut)
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> CloneProfileOut:
    profile = db.get(models.CloneProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return CloneProfileOut.model_validate(profile)


@router.put("/profiles/{profile_id}", response_model=CloneProfileOut)
def update_profile(
    profile_id: int, payload: CloneProfileCreate, db: Session = Depends(get_db)
) -> CloneProfileOut:
    profile = db.get(models.CloneProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    profile.name = payload.name
    profile.product_id = payload.product_id
    profile.production_property_name = payload.production.property_name
    profile.production_contract_id = payload.production.contract_id
    profile.production_account_name = payload.production.account_name
    profile.production_account_type = payload.production.account_type
    profile.sqa_property_name = payload.shared.property_name
    profile.sqa_contract_id = payload.shared.contract_id
    profile.sqa_account_name = payload.shared.account_name
    profile.sqa_account_type = payload.shared.account_type
    profile.sqa_group_id = payload.sqa_group_id
    profile.sqa_edge_hostname_secure = payload.sqa_edge_hostname_secure
    profile.sqa_edge_hostname_nonsecure = payload.sqa_edge_hostname_nonsecure
    profile.policies = payload.policies
    profile.field_overrides = payload.field_overrides
    profile.notify_emails = payload.notify_emails
    profile.attempt_activation = payload.attempt_activation
    profile.schedule_cron = payload.schedule_cron
    db.commit()
    db.refresh(profile)

    from app.services.scheduler_service import job_id_for_profile, scheduler_backend

    job_id = job_id_for_profile(profile.id)
    if profile.schedule_cron:
        scheduler_backend.upsert_job(
            job_id, profile.schedule_cron, clone_service.rerun_scheduled, (profile.id,)
        )
    else:
        scheduler_backend.remove_job(job_id)
    return CloneProfileOut.model_validate(profile)


@router.delete("/profiles/{profile_id}", status_code=204)
def delete_profile(profile_id: int, db: Session = Depends(get_db)) -> None:
    profile = db.get(models.CloneProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    active_statuses = {models.JobStatus.PENDING, models.JobStatus.RUNNING}
    if any(job.status in active_statuses for job in profile.jobs):
        raise HTTPException(
            status_code=409,
            detail="Profile has a running job. Wait for it to finish before deleting it.",
        )

    from app.services.scheduler_service import job_id_for_profile, scheduler_backend

    scheduler_backend.remove_job(job_id_for_profile(profile.id))
    db.delete(profile)
    db.commit()


@router.post("/profiles/{profile_id}/run", response_model=CloneJobOut)
def run_profile(
    profile_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
) -> CloneJobOut:
    profile = db.get(models.CloneProfile, profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    job = models.CloneJob(profile_id=profile.id, trigger="manual", status=models.JobStatus.PENDING)
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(clone_service.execute_job, job.id)
    return CloneJobOut.model_validate(job)


@router.get("/jobs", response_model=list[CloneJobOut])
def list_jobs(profile_id: int | None = None, db: Session = Depends(get_db)) -> list[CloneJobOut]:
    query = db.query(models.CloneJob)
    if profile_id is not None:
        query = query.filter(models.CloneJob.profile_id == profile_id)
    jobs = query.order_by(models.CloneJob.id.desc()).all()
    return [CloneJobOut.model_validate(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=CloneJobOut)
def get_job(job_id: int, db: Session = Depends(get_db)) -> CloneJobOut:
    job = db.get(models.CloneJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return CloneJobOut.model_validate(job)
