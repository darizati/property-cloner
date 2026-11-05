"""Powers the setup-wizard step: fetch prod (+ SQA if it exists) rule trees and
return a diffed, policy-annotated node list for the UI to render."""
import copy
from typing import Optional

from app.config import settings
from app.papi.client import PapiClient, PapiError
from app.papi.rule_tree import diff_rule_sets, merge_variable_declarations
from app.schemas import FetchDiffRequest, FetchDiffResponse, RuleNodeOut


def fetch_sqa_source_tree(
    client: PapiClient,
    property_id: str,
    version: int,
    contract_id: str,
    group_id: str,
    account_switch_key: Optional[str],
) -> tuple[dict, dict[str, int]]:
    tree = copy.deepcopy(client.get_rule_tree(property_id, version, contract_id, group_id, account_switch_key))
    active_version = None
    for network in ("PRODUCTION", "STAGING"):
        try:
            active_version = client.get_latest_version(
                property_id, contract_id, group_id, account_switch_key, activated_on=network,
            )
        except PapiError as error:
            if error.status_code != 404:
                raise
        if active_version:
            break
    if not active_version or active_version == version:
        return tree, {}
    active_tree = client.get_rule_tree(property_id, active_version, contract_id, group_id, account_switch_key)
    root = tree.get("rules", tree)
    active_root = active_tree.get("rules", active_tree)
    original_names = {variable["name"] for variable in root.get("variables", [])}
    recovered = {
        variable["name"]: active_version for variable in active_root.get("variables", [])
        if variable["name"] not in original_names
    }
    if recovered:
        root["variables"] = merge_variable_declarations(root.get("variables", []), active_root["variables"])
    return tree, recovered


def fetch_diff(req: FetchDiffRequest) -> FetchDiffResponse:
    prod_client = PapiClient(settings.edgerc_section_production)
    sqa_client = PapiClient(settings.edgerc_section_shared)

    prod_match, prod_switch_key = prod_client.resolve_property(
        req.production.property_name, req.production.contract_id,
        req.production.account_name, req.production.account_type,
    )
    if not prod_match:
        raise ValueError(f"Production property '{req.production.property_name}' not found")

    prod_group_id = prod_match["groupId"]
    prod_property_id = prod_match["propertyId"]
    prod_version = req.production.property_version or prod_match.get("propertyVersion")
    prod_tree = prod_client.get_rule_tree(
        prod_property_id, prod_version, req.production.contract_id, prod_group_id, prod_switch_key
    )

    sqa_match, sqa_switch_key = sqa_client.resolve_property(
        req.shared.property_name, req.shared.contract_id,
        req.shared.account_name, req.shared.account_type,
        group_id=req.sqa_group_id or None,
    )

    sqa_tree: Optional[dict] = None
    sqa_version: Optional[int] = None
    sqa_exists = sqa_match is not None
    variable_sources: dict[str, int] = {}
    if sqa_exists:
        sqa_group_id = sqa_match["groupId"]
        sqa_property_id = sqa_match["propertyId"]
        sqa_version = sqa_client.get_latest_version(
            sqa_property_id, req.shared.contract_id, sqa_group_id, sqa_switch_key,
            activated_on=None,
        ) or sqa_match.get("latestVersion") or sqa_match.get("propertyVersion")
        sqa_tree, variable_sources = fetch_sqa_source_tree(
            sqa_client,
            sqa_property_id, sqa_version, req.shared.contract_id, sqa_group_id, sqa_switch_key
        )

    nodes = diff_rule_sets(prod_tree, sqa_tree)
    for node in nodes:
        if node["kind"] == "variable" and node["name"] in variable_sources:
            node["sqa_source_version"] = variable_sources[node["name"]]
    return FetchDiffResponse(
        sqa_property_exists=sqa_exists,
        prod_version=prod_version,
        sqa_version=sqa_version,
        nodes=[RuleNodeOut(**n) for n in nodes],
    )
