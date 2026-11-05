"""Pydantic request/response schemas for the API layer."""
from typing import Any, Literal, Optional

from pydantic import BaseModel

Policy = Literal["prod", "sqa", "remove"]


class PropertyRef(BaseModel):
    account_name: str
    account_type: str
    contract_id: str
    property_name: str
    property_version: Optional[int] = None  # None -> latest active version


class FetchDiffRequest(BaseModel):
    product_id: str
    production: PropertyRef
    shared: PropertyRef  # property_version ignored; SQA side always uses latest
    sqa_group_id: Optional[str] = None


class RuleNodeOut(BaseModel):
    signature: str
    rule_path: str
    kind: Literal["behavior", "criterion", "variable"]
    name: str
    in_prod: bool
    in_sqa: bool
    changed: bool
    prod_options: Optional[dict[str, Any]] = None
    sqa_options: Optional[dict[str, Any]] = None
    default_policy: Policy
    sqa_source_version: Optional[int] = None


class FetchDiffResponse(BaseModel):
    sqa_property_exists: bool
    prod_version: int
    sqa_version: Optional[int] = None
    nodes: list[RuleNodeOut]


class CloneProfileCreate(BaseModel):
    name: str
    product_id: str
    production: PropertyRef
    shared: PropertyRef
    sqa_group_id: str
    # Only used when the SQA property doesn't exist yet and must be created.
    sqa_edge_hostname_secure: str = ""
    sqa_edge_hostname_nonsecure: str = ""
    policies: dict[str, Policy] = {}
    field_overrides: dict[str, dict[str, Any]] = {}
    notify_emails: list[str] = []
    attempt_activation: bool = False
    schedule_cron: Optional[str] = None


class CloneProfileOut(BaseModel):
    id: int
    name: str
    product_id: str
    production_account_name: str
    production_account_type: str
    production_contract_id: str
    production_property_name: str
    sqa_account_name: str
    sqa_account_type: str
    sqa_contract_id: str
    sqa_property_name: str
    sqa_group_id: str
    sqa_edge_hostname_secure: str
    sqa_edge_hostname_nonsecure: str
    policies: dict[str, Policy]
    field_overrides: dict[str, dict[str, Any]]
    notify_emails: list[str]
    attempt_activation: bool
    schedule_cron: Optional[str]
    last_known_prod_version: Optional[int]

    model_config = {"from_attributes": True}


class CloneJobOut(BaseModel):
    id: int
    profile_id: int
    trigger: str
    status: str
    prod_version: Optional[int]
    sqa_version: Optional[int]
    sqa_property_url: Optional[str] = None
    activation_status: Optional[str] = None
    activation_id: Optional[str] = None
    activation_network: Optional[str] = None
    created_new_property: bool
    removed_rules: list[dict[str, Any]]
    log: str
    error_summary: Optional[str]
    change_summary: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class ScheduleUpdate(BaseModel):
    schedule_cron: Optional[str]  # None clears the schedule
