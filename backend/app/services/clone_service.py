"""Core clone orchestration: merge rule trees per policy, create-or-version the
SQA property, reuse editable drafts, activate configured properties, and notify.

Entry points:
- execute_job(job_id): runs a manual (already-created, PENDING) CloneJob.
- rerun_scheduled(profile_id): called by the scheduler on each cron tick; checks
  for a newer production version and relevant rule changes before cloning.
"""
import datetime
import copy
import json
import traceback
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.db import SessionLocal
from app.papi.client import PapiClient, PapiError
from app.papi.rule_tree import (
    apply_policies,
    diff_rule_sets,
    replace_hostname_strings,
    summarize_rule_changes,
)
from app.services.notify_service import capture_run_context, notify_job_outcome
from app.services.diff_service import fetch_sqa_source_tree


class _Log:
    """Accumulates a job's log lines and flushes each line for live polling."""

    def __init__(self, db: Session, job: models.CloneJob) -> None:
        self.db = db
        self.job = job
        self.lines: list[str] = [line for line in (job.log or "").splitlines() if line]

    def __call__(self, line: str) -> None:
        stamp = datetime.datetime.utcnow().strftime("%H:%M:%S")
        self.lines.append(f"[{stamp}] {line}")
        self.job.log = self.text
        self.db.commit()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _schema_error_details(payload: object, rule_tree: dict) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("errors"), list):
        return []

    details = []
    for error in payload["errors"]:
        if not isinstance(error, dict):
            continue
        location, schema_location = error.get("location"), error.get("schemaLocation")
        allowed = error.get("allowedValues")
        if not isinstance(location, str) or not location.startswith("/") or not isinstance(schema_location, str):
            continue
        if not isinstance(allowed, list) or not allowed or "foundValue" not in error:
            continue
        parts = [part.replace("~1", "/").replace("~0", "~") for part in location[1:].split("/")]
        feature_index = next(
            (i for i, part in enumerate(parts) if part in ("behaviors", "criteria") and i + 1 < len(parts)),
            None,
        )
        if feature_index is None or "options" not in parts[feature_index + 2:]:
            continue
        node = rule_tree
        try:
            for part in parts[:feature_index + 2]:
                node = node[int(part)] if isinstance(node, list) else node[part]
        except (KeyError, IndexError, ValueError, TypeError):
            continue
        if not isinstance(node, dict) or not isinstance(node.get("name"), str):
            continue
        feature_name = node["name"]
        encoded_name = feature_name.replace("~", "~0").replace("/", "~1")
        if not schema_location.startswith(f"/definitions/catalog/{parts[feature_index]}/{encoded_name}/"):
            continue

        found = error["foundValue"]
        if any(type(value) is type(found) and value == found for value in allowed):
            continue
        found_type = (
            "boolean" if isinstance(found, bool) else
            "string" if isinstance(found, str) else
            "number" if isinstance(found, (int, float)) else
            "null" if found is None else type(found).__name__
        )
        found_description = found_type
        if isinstance(found, str) and found in ("true", "false"):
            found_description += f" {json.dumps(found)}"
        if all(isinstance(value, bool) for value in allowed):
            expected = "boolean " + json.dumps(allowed)
        else:
            expected = "one of " + json.dumps(allowed)
        message = (
            f"{feature_name}.{parts[-1]}: received {found_description}; expected {expected}. "
            f"Location: {location}"
        )
        if message not in details:
            details.append(message)
    return details


def execute_job(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(models.CloneJob, job_id)
        if not job:
            return
        profile = job.profile
        job.status = models.JobStatus.RUNNING
        db.commit()
        _run_clone(db, job, profile)
    except Exception as e:  # noqa: BLE001 - top-level job guard, must not crash the worker
        job = db.get(models.CloneJob, job_id)
        if job:
            error_text = str(e) if isinstance(e, PapiError) else f"{e}\n{traceback.format_exc()}"
            job.status = models.JobStatus.FAILED
            job.error_summary = str(e)
            job.log = "\n".join(filter(None, [job.log, "[ERROR] " + error_text]))
            job.finished_at = datetime.datetime.utcnow()
            db.commit()
            notify_job_outcome(job.profile.name, job)
    finally:
        db.close()


def rerun_scheduled(profile_id: int) -> None:
    db = SessionLocal()
    try:
        profile = db.get(models.CloneProfile, profile_id)
        if not profile:
            return

        job = models.CloneJob(
            profile_id=profile.id, trigger="scheduled", status=models.JobStatus.RUNNING,
            change_summary={"context": capture_run_context(profile)},
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        prod_client = PapiClient(settings.edgerc_section_production)
        prod_match, prod_switch_key = prod_client.resolve_property(
            profile.production_property_name, profile.production_contract_id,
            profile.production_account_name, profile.production_account_type,
        )
        if not prod_match:
            raise PapiError(f"Production property '{profile.production_property_name}' not found")

        latest_prod_version = prod_client.get_latest_version(
            prod_match["propertyId"], profile.production_contract_id,
            prod_match["groupId"], prod_switch_key,
        ) or prod_match.get("propertyVersion")

        if profile.last_known_prod_version and latest_prod_version <= profile.last_known_prod_version:
            job.status = models.JobStatus.SKIPPED_NO_NEWER_VERSION
            job.prod_version = latest_prod_version
            job.finished_at = datetime.datetime.utcnow()
            job.log = f"Latest production version ({latest_prod_version}) is not newer than last cloned ({profile.last_known_prod_version}). Skipping."
            db.commit()
            notify_job_outcome(profile.name, job)
            return

        # Newer production version exists — check whether it actually touches any
        # of the rules this profile cares about before doing a full re-clone.
        prod_tree = prod_client.get_rule_tree(
            prod_match["propertyId"], latest_prod_version,
            profile.production_contract_id, prod_match["groupId"], prod_switch_key,
        )

        sqa_client = PapiClient(settings.edgerc_section_shared)
        sqa_match, sqa_switch_key = sqa_client.resolve_property(
            profile.sqa_property_name, profile.sqa_contract_id,
            profile.sqa_account_name, profile.sqa_account_type,
            group_id=profile.sqa_group_id or None,
        )
        sqa_tree = None
        if sqa_match:
            sqa_version = sqa_client.get_latest_version(
                sqa_match["propertyId"], profile.sqa_contract_id, sqa_match["groupId"], sqa_switch_key,
                activated_on=None,
            ) or sqa_match.get("latestVersion") or sqa_match.get("propertyVersion")
            sqa_tree, _ = fetch_sqa_source_tree(
                sqa_client,
                sqa_match["propertyId"], sqa_version, profile.sqa_contract_id,
                sqa_match["groupId"], sqa_switch_key,
            )

        tracked_signatures = {sig for sig, policy in profile.policies.items() if policy != "remove"}
        diffed = diff_rule_sets(prod_tree, sqa_tree)
        relevant_changes = [
            n for n in diffed
            if n["signature"] in tracked_signatures and n["changed"]
        ]
        if not relevant_changes:
            job.status = models.JobStatus.SKIPPED_NO_RULE_CHANGE
            job.prod_version = latest_prod_version
            job.finished_at = datetime.datetime.utcnow()
            job.log = f"Production version {latest_prod_version} is newer, but none of the tracked rules changed. Skipping."
            db.commit()
            notify_job_outcome(profile.name, job)
            return

        _run_clone(db, job, profile)
    except Exception as e:  # noqa: BLE001
        job = locals().get("job")
        if job is not None:
            job.status = models.JobStatus.FAILED
            job.error_summary = str(e)
            error_text = str(e) if isinstance(e, PapiError) else f"{e}\n{traceback.format_exc()}"
            job.log = "\n".join(filter(None, [job.log, "[ERROR] " + error_text]))
            job.finished_at = datetime.datetime.utcnow()
            db.commit()
            notify_job_outcome(profile.name, job)
    finally:
        db.close()


def _run_clone(db: Session, job: models.CloneJob, profile: models.CloneProfile) -> None:
    if not job.change_summary:
        job.change_summary = {"context": capture_run_context(profile)}
    log = _Log(db, job)
    prod_client = PapiClient(settings.edgerc_section_production)
    sqa_client = PapiClient(settings.edgerc_section_shared)

    prod_match, prod_switch_key = prod_client.resolve_property(
        profile.production_property_name, profile.production_contract_id,
        profile.production_account_name, profile.production_account_type,
    )
    if not prod_match:
        raise PapiError(f"Production property '{profile.production_property_name}' not found")

    prod_property_id = prod_match["propertyId"]
    prod_group_id = prod_match["groupId"]
    prod_version = prod_client.get_latest_version(
        prod_property_id, profile.production_contract_id, prod_group_id, prod_switch_key
    ) or prod_match.get("propertyVersion")
    log(f"Fetching production rule tree v{prod_version}")
    prod_tree = prod_client.get_rule_tree(
        prod_property_id, prod_version, profile.production_contract_id, prod_group_id, prod_switch_key
    )
    prod_hostnames = prod_client.get_hostnames(
        prod_property_id, prod_version, profile.production_contract_id, prod_group_id, prod_switch_key
    )

    sqa_match, sqa_switch_key = sqa_client.resolve_property(
        profile.sqa_property_name, profile.sqa_contract_id,
        profile.sqa_account_name, profile.sqa_account_type,
        group_id=profile.sqa_group_id or None,
    )
    if not sqa_match and not sqa_switch_key:
        raise PapiError(
            "Unable to access any shared account through PAPI. "
            "The shared EdgeGrid client needs the Property Manager grant "
            "for property lookup and creation."
        )

    sqa_tree = None
    sqa_before = None
    sqa_version_source = None
    sqa_exists = sqa_match is not None
    reuse_draft = False
    activation_network = None
    if sqa_exists:
        log(f"SQA property '{profile.sqa_property_name}' exists - checking latest version")
        sqa_property_id = sqa_match["propertyId"]
        sqa_version_source = sqa_client.get_latest_version(
            sqa_property_id, profile.sqa_contract_id, sqa_match["groupId"], sqa_switch_key,
            activated_on=None,
        ) or sqa_match.get("latestVersion") or sqa_match.get("propertyVersion")
        version_details = sqa_client.get_version_details(
            sqa_property_id, sqa_version_source, profile.sqa_contract_id,
            sqa_match["groupId"], sqa_switch_key,
        )
        activations = sqa_client.get_activations(
            sqa_property_id, profile.sqa_contract_id, sqa_match["groupId"], sqa_switch_key,
        )
        if any(activation.get("status") not in {"ACTIVE", "DEACTIVATED", "FAILED", "ABORTED"}
               for activation in activations):
            raise PapiError("SQA property has an activation in progress; wait for it to finish before retrying")
        reuse_draft = all(
            version_details.get(field) == "INACTIVE" for field in ("productionStatus", "stagingStatus")
        ) and not any(
            activation.get("propertyVersion") == sqa_version_source
            and activation.get("status") not in {"FAILED", "ABORTED"}
            for activation in activations
        )
        activated_networks = {
            activation.get("network") for activation in activations
            if activation.get("activationType") == "ACTIVATE"
            and activation.get("status") in {"ACTIVE", "DEACTIVATED"}
        }
        for network, field in (("PRODUCTION", "productionStatus"), ("STAGING", "stagingStatus")):
            if version_details.get(field) == "ACTIVE":
                activated_networks.add(network)
        if "PRODUCTION" in activated_networks:
            activation_network = "PRODUCTION"
        elif "STAGING" in activated_networks:
            activation_network = "STAGING"
        sqa_tree, variable_sources = fetch_sqa_source_tree(
            sqa_client,
            sqa_property_id, sqa_version_source, profile.sqa_contract_id,
            sqa_match["groupId"], sqa_switch_key,
        )
        sqa_before = copy.deepcopy(sqa_tree)
        if variable_sources:
            baseline_root = sqa_before.get("rules", sqa_before)
            baseline_root["variables"] = [
                variable for variable in baseline_root.get("variables", [])
                if variable["name"] not in variable_sources
            ]
        for variable_name, source_version in variable_sources.items():
            log(f"SQA variable {variable_name} available from active v{source_version}")
    else:
        log(f"SQA property '{profile.sqa_property_name}' does not exist — will create it")

    log("Merging rule trees per configured policies")
    merged = apply_policies(prod_tree, sqa_tree, profile.policies, profile.field_overrides)

    org_hostnames = [h["cnameFrom"] for h in prod_hostnames if "cnameFrom" in h]
    if replace_hostname_strings(merged, org_hostnames):
        log("Rewrote production hostname references to sqa- prefixed equivalents")

    job.change_summary = {
        **summarize_rule_changes(sqa_before, merged),
        "context": job.change_summary["context"],
        "baseline_version": sqa_version_source,
        "state": "planned",
    }

    if not sqa_exists:
        log(f"Creating new SQA property '{profile.sqa_property_name}'")
        sqa_property_id = sqa_client.create_property(
            profile.sqa_property_name, profile.product_id, profile.sqa_contract_id,
            profile.sqa_group_id, sqa_switch_key,
        )
        target_version = 1
        job.created_new_property = True
        log("Leaving new SQA property hostnames unassigned")
    elif reuse_draft:
        target_version = sqa_version_source
        job.created_new_property = False
        log(f"Reusing editable SQA draft v{target_version}")
    else:
        log(f"Creating new version of SQA property '{profile.sqa_property_name}'")
        target_version = sqa_client.create_new_version(
            sqa_property_id, profile.sqa_contract_id, sqa_match["groupId"], sqa_switch_key,
            create_from_version=sqa_version_source,
        )
        job.created_new_property = False

    sqa_group_id = profile.sqa_group_id if not sqa_exists else sqa_match["groupId"]

    job.prod_version = prod_version
    job.sqa_version = target_version
    property_details = sqa_match if sqa_match and sqa_match.get("assetId") else sqa_client.get_property_details(
        sqa_property_id, profile.sqa_contract_id, sqa_group_id, sqa_switch_key,
    )
    asset_id = property_details.get("assetId")
    if not asset_id:
        raise PapiError(f"Property '{sqa_property_id}' has no assetId for its Property Manager link")
    portal_params = {"gid": sqa_group_id.removeprefix("grp_")}
    job.sqa_property_url = (
        f"{settings.property_manager_url.rstrip('/')}/#/property/{asset_id.removeprefix('aid_')}"
        f"?{urlencode(portal_params)}"
    )
    log(f"Uploading rule tree to version {target_version}")
    try:
        upload_response = sqa_client.upload_rule_tree(
            sqa_property_id, target_version, profile.sqa_contract_id, sqa_group_id,
            sqa_switch_key, merged,
        )
    except PapiError as error:
        schema_details = _schema_error_details(error.payload, merged)
        if schema_details:
            papi_message = error.payload.get("detail", "PAPI rejected the rule tree")
            failure = (
                f"PAPI HTTP {error.status_code}: {papi_message}\n"
                + "\n".join(schema_details)
                + "\nChoose a compatible SQA value or set a fixed override with the expected type."
            )
        else:
            failure = str(error)
        raise PapiError(
            f"Rule tree upload failed. SQA version {target_version} may be incomplete; "
            f"review the property before retrying.\n{failure}",
            status_code=error.status_code, payload=error.payload,
        ) from error
    try:
        validation = upload_response.json() if upload_response.content else {}
    except (ValueError, AttributeError):
        validation = {}
    issues = {
        kind: [
            {"location": issue.get("errorLocation", ""),
             "detail": issue.get("detail") or issue.get("title") or "Validation issue"}
            for issue in (validation.get(kind) or []) if isinstance(issue, dict)
        ] if isinstance(validation, dict) else []
        for kind in ("errors", "warnings")
    }
    job.change_summary = {**job.change_summary, "state": "saved", "validation": issues}
    log(f"Review property: {job.sqa_property_url}")
    log(f"Saved SQA rule tree v{target_version}")
    attempt_activation = getattr(profile, "attempt_activation", False)
    if attempt_activation and sqa_exists and activation_network and sqa_client.get_hostnames(
        sqa_property_id, target_version, profile.sqa_contract_id, sqa_group_id, sqa_switch_key,
    ):
        job.activation_network = activation_network
        job.activation_status = "PENDING"
        log(f"Activating SQA v{target_version} on {activation_network}")
        result = sqa_client.activate(
            sqa_property_id, target_version, profile.sqa_contract_id, sqa_group_id, sqa_switch_key,
            network=activation_network, notify_emails=profile.notify_emails,
        )
        job.activation_status = result.status
        job.activation_id = result.activation_id
        log(f"SQA v{target_version} activation status: {result.status}")
        if result.status != "ACTIVE":
            raise PapiError(
                f"Rule tree saved in SQA v{target_version}, but {activation_network} activation "
                f"{result.status}: {json.dumps(result.errors)}"
            )
    else:
        reason = "activation disabled" if not attempt_activation else "no prior activation or no hostnames"
        log(f"Saved SQA draft v{target_version}; manual activation required ({reason})")
    job.removed_rules = []
    job.log = log.text
    job.finished_at = datetime.datetime.utcnow()
    job.status = models.JobStatus.SUCCESS
    profile.last_known_prod_version = prod_version

    db.commit()
    notify_job_outcome(profile.name, job)
