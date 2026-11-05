"""Recurring-schedule abstraction. `APSchedulerBackend` is the v1 implementation
(in-process, no extra infra). To move to Celery beat later, implement a
`SchedulerBackend` with the same three methods and swap it in `main.py` —
`clone_service` never talks to the scheduler directly, so it doesn't change.
"""
from typing import Callable, Protocol

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger


class SchedulerBackend(Protocol):
    def start(self) -> None: ...
    def upsert_job(self, job_id: str, cron_expression: str, func: Callable, args: tuple) -> None: ...
    def remove_job(self, job_id: str) -> None: ...


class APSchedulerBackend:
    def __init__(self) -> None:
        self._scheduler = BackgroundScheduler()

    def start(self) -> None:
        if not self._scheduler.running:
            self._scheduler.start()

    def upsert_job(self, job_id: str, cron_expression: str, func: Callable, args: tuple) -> None:
        trigger = CronTrigger.from_crontab(cron_expression)
        self._scheduler.add_job(func, trigger=trigger, id=job_id, args=args, replace_existing=True)

    def remove_job(self, job_id: str) -> None:
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)


scheduler_backend = APSchedulerBackend()


def job_id_for_profile(profile_id: int) -> str:
    return f"clone-profile-{profile_id}"
