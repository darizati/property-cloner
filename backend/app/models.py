"""ORM models. Kept intentionally simple (JSON columns for policies/overrides)
so the schema doesn't need to anticipate every future field-override shape."""
import datetime
import enum

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"
    SKIPPED_NO_NEWER_VERSION = "SKIPPED_NO_NEWER_VERSION"
    SKIPPED_NO_RULE_CHANGE = "SKIPPED_NO_RULE_CHANGE"


class CloneProfile(Base):
    """A saved clone configuration: which prod/SQA properties, which per-rule
    policies/overrides to apply, and (optionally) a recurring schedule."""

    __tablename__ = "clone_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)

    production_property_name: Mapped[str] = mapped_column(String(200))
    production_contract_id: Mapped[str] = mapped_column(String(50))
    production_account_name: Mapped[str] = mapped_column(String(100))
    production_account_type: Mapped[str] = mapped_column(String(100))

    sqa_property_name: Mapped[str] = mapped_column(String(200))
    sqa_contract_id: Mapped[str] = mapped_column(String(50))
    sqa_account_name: Mapped[str] = mapped_column(String(100))
    sqa_account_type: Mapped[str] = mapped_column(String(100))
    sqa_group_id: Mapped[str] = mapped_column(String(50))

    # Target edge hostname new SQA hostnames should CNAME to (only used when
    # creating a brand-new SQA property); which one applies is chosen per
    # hostname based on that property version's IS_SECURE rule option.
    sqa_edge_hostname_secure: Mapped[str] = mapped_column(String(200), default="")
    sqa_edge_hostname_nonsecure: Mapped[str] = mapped_column(String(200), default="")

    product_id: Mapped[str] = mapped_column(String(100))

    # {signature: "prod" | "sqa" | "remove"}
    policies: Mapped[dict] = mapped_column(JSON, default=dict)
    # {signature: {option_key: fixed_value}}
    field_overrides: Mapped[dict] = mapped_column(JSON, default=dict)

    notify_emails: Mapped[list] = mapped_column(JSON, default=list)
    attempt_activation: Mapped[bool] = mapped_column(default=False)

    schedule_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_known_prod_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    jobs: Mapped[list["CloneJob"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class CloneJob(Base):
    """One execution of a CloneProfile (manual run or scheduled re-clone)."""

    __tablename__ = "clone_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("clone_profiles.id"))
    profile: Mapped[CloneProfile] = relationship(back_populates="jobs")

    trigger: Mapped[str] = mapped_column(String(20), default="manual")  # manual|scheduled
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.PENDING
    )

    prod_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sqa_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sqa_property_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    activation_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    activation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    activation_network: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_new_property: Mapped[bool] = mapped_column(default=False)

    # [{"location": "#/rules/behaviors/2", "detail": "...", "removed": {...}}]
    removed_rules: Mapped[list] = mapped_column(JSON, default=list)

    log: Mapped[str] = mapped_column(Text, default="")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )
