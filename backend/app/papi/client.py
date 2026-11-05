"""Akamai PAPI client using EdgeGrid (.edgerc) auth.

Adapted from prod-check-mate-ab's property_utils.py, but as a reusable class
that raises PapiError instead of calling sys.exit() — this runs inside a web
server process, so a bad response must not kill it.
"""
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from akamai.edgegrid import EdgeGridAuth, EdgeRc

from app.config import settings


class PapiError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class ActivationResult:
    def __init__(self, activation_id: Optional[str], status: str, errors: list[dict]):
        self.activation_id = activation_id
        self.status = status
        self.errors = errors

    @property
    def succeeded(self) -> bool:
        return self.status == "ACTIVE"


class PapiClient:
    """One client per environment section (e.g. 'production' or 'shared')."""

    def __init__(self, section: str):
        self.edgerc = EdgeRc(settings.resolved_edgerc_path)
        self.section = section
        self.base_url = f"https://{self.edgerc.get(section, 'host')}"
        self.session = requests.Session()
        self.session.auth = EdgeGridAuth.from_edgerc(self.edgerc, section)

    # -- low level -----------------------------------------------------

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {"Accept": "application/json", "PAPI-Use-Prefixes": "true"}
        if extra:
            headers.update(extra)
        return headers

    def _check(self, resp: requests.Response) -> requests.Response:
        if resp.status_code >= 400:
            try:
                payload = resp.json()
            except ValueError:
                payload = {"raw": resp.text}
            detail = payload.get("detail") if isinstance(payload, dict) else None
            request_id = payload.get("requestId") if isinstance(payload, dict) else None
            request_time = payload.get("requestTime") if isinstance(payload, dict) else None
            correlation = ""
            if request_id or request_time:
                correlation = f" [requestId={request_id or 'unknown'}, requestTime={request_time or 'unknown'}]"
            raise PapiError(
                f"PAPI {resp.request.method} {resp.request.url} -> {resp.status_code}"
                + (f": {detail}" if detail else "")
                + correlation,
                resp.status_code,
                payload,
            )
        return resp

    def _get(self, path: str, params: dict) -> dict:
        resp = self.session.get(urljoin(self.base_url, path), headers=self._headers(), params=params)
        return self._check(resp).json()

    def _post(self, path: str, params: dict, body: dict) -> requests.Response:
        resp = self.session.post(
            urljoin(self.base_url, path),
            headers=self._headers({"Content-Type": "application/json"}),
            params=params,
            json=body,
        )
        return self._check(resp)

    def _put(self, path: str, params: dict, body: Any) -> requests.Response:
        resp = self.session.put(
            urljoin(self.base_url, path),
            headers=self._headers({"Content-Type": "application/json"}),
            params=params,
            json=body,
        )
        return self._check(resp)

    # -- lookups ---------------------------------------------------------

    def search_switch_keys(self, account_name: str, account_type: Optional[str] = None) -> list[dict]:
        """Returns all account-switch-key candidates for account_name, optionally
        filtered by an account_type substring. Akamai account names can be
        ambiguous (e.g. two accounts for the same customer both containing
        'Akamai Internal' in their accountName) — callers that also know the
        target property name should use resolve_property() instead of guessing
        a single candidate here.
        """
        data = self._get(
            "/identity-management/v3/api-clients/self/account-switch-keys",
            {"search": account_name},
        )
        if account_type:
            filtered = [
                item for item in data if account_type.lower() in item.get("accountName", "").lower()
            ]
            return filtered or data
        return data

    def get_switch_key(self, account_name: str, account_type: str) -> Optional[str]:
        """Best-effort single switch key guess (first match). Ambiguous when
        multiple accounts share the account_type substring — prefer
        resolve_property() when the target property name is known, since that
        disambiguates by checking which account actually has the property.
        """
        candidates = self.search_switch_keys(account_name, account_type)
        return candidates[0]["accountSwitchKey"] if candidates else None

    def resolve_property(
        self, property_name: str, contract_id: str, account_name: str, account_type: str,
        group_id: Optional[str] = None,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Finds a property by trying every candidate account-switch-key
        matching account_name/account_type, in order, until one actually has
        the property — avoids silently resolving to the wrong account when
        multiple accounts share similar names. A candidate the API client
        can't access (e.g. 403 — this account isn't in scope for these
        credentials) is skipped rather than aborting the whole lookup.
        Returns (property_match, switch_key_used), or (None, None) if no
        candidate account has the property.
        """
        candidates = self.search_switch_keys(account_name, account_type)
        usable_switch_key = None
        last_lookup_error = None
        for candidate in candidates:
            switch_key = candidate["accountSwitchKey"]
            try:
                match = self.find_property(property_name, contract_id, switch_key, group_id)
            except PapiError as error:
                if error.status_code not in (401, 403, 404):
                    raise
                last_lookup_error = error
                continue
            if match:
                return match, switch_key
            if usable_switch_key is None:
                usable_switch_key = switch_key
        if last_lookup_error and usable_switch_key is None:
            raise PapiError(
                "PAPI property lookup was denied for every candidate shared account: "
                f"{last_lookup_error}",
                status_code=last_lookup_error.status_code,
                payload=last_lookup_error.payload,
            ) from last_lookup_error
        if usable_switch_key:
            return None, usable_switch_key
        # Fall back to a switch-key-less search (covers the client's own account).
        try:
            return self.find_property(property_name, contract_id, None, group_id), None
        except PapiError as error:
            if error.status_code not in (401, 403, 404):
                raise
            return None, None

    def find_property(
        self, property_name: str, contract_id: str, account_switch_key: Optional[str],
        group_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Returns {'propertyId', 'groupId', 'latestVersion', ...} or None if not found."""
        body = {"propertyName": property_name}
        params = {}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        resp = self._post("/papi/v1/search/find-by-value", params, body)
        items = resp.json().get("versions", {}).get("items", [])
        matches = [
            item for item in items
            if item.get("contractId", "").removeprefix("ctr_") == contract_id.removeprefix("ctr_")
            and item.get("propertyName") == property_name
            and (not group_id or item.get("groupId", "").removeprefix("grp_") == group_id.removeprefix("grp_"))
        ]
        if matches:
            return max(matches, key=lambda item: item.get("propertyVersion", 0))
        if group_id:
            params.update(contractId=contract_id, groupId=group_id)
            data = self._get("/papi/v1/properties", params)
            matches = [
                item for item in data.get("properties", {}).get("items", [])
                if item.get("propertyName") == property_name
                and item.get("contractId", "").removeprefix("ctr_") == contract_id.removeprefix("ctr_")
                and item.get("groupId", "").removeprefix("grp_") == group_id.removeprefix("grp_")
            ]
            if len(matches) > 1:
                raise PapiError(f"Multiple properties named '{property_name}' found in the target scope")
            return matches[0] if matches else None
        return None

    def get_property_details(
        self,
        property_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> dict:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(f"/papi/v1/properties/{property_id}", params)
        items = data.get("properties", {}).get("items", [])
        if not items:
            raise PapiError(f"Property '{property_id}' not found", payload=data)
        return items[0]

    def get_latest_version(
        self,
        property_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        activated_on: Optional[str] = "PRODUCTION",
    ) -> Optional[int]:
        params = {
            "contractId": contract_id,
            "groupId": group_id,
        }
        if activated_on:
            params["activatedOn"] = activated_on
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(f"/papi/v1/properties/{property_id}/versions/latest", params)
        items = data.get("versions", {}).get("items", [])
        return items[0]["propertyVersion"] if items else None

    def get_version_details(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> dict:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(f"/papi/v1/properties/{property_id}/versions/{version}", params)
        items = data.get("versions", {}).get("items", [])
        if not items:
            raise PapiError(f"Property '{property_id}' version {version} not found", payload=data)
        return items[0]

    def get_activations(
        self,
        property_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> list[dict]:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(f"/papi/v1/properties/{property_id}/activations", params)
        return data.get("activations", {}).get("items", [])

    # -- rule tree ---------------------------------------------------------

    def get_rule_tree(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> dict:
        params = {
            "contractId": contract_id,
            "groupId": group_id,
            "validateRules": "true",
            "validateMode": "full",
        }
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        return self._get(
            f"/papi/v1/properties/{property_id}/versions/{version}/rules", params
        )

    def upload_rule_tree(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        rule_tree: dict,
    ) -> requests.Response:
        params = {
            "contractId": contract_id,
            "groupId": group_id,
            "validateRules": "true",
            "validateMode": "full",
        }
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        return self._put(
            f"/papi/v1/properties/{property_id}/versions/{version}/rules",
            params,
            rule_tree,
        )

    def get_hostnames(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> list[dict]:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(
            f"/papi/v1/properties/{property_id}/versions/{version}/hostnames", params
        )
        return data.get("hostnames", {}).get("items", [])

    def update_hostnames(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        hostnames: list[dict],
    ) -> requests.Response:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        return self._put(
            f"/papi/v1/properties/{property_id}/versions/{version}/hostnames",
            params,
            hostnames,
        )

    def get_edge_hostname_details(
        self,
        edge_hostname_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> dict:
        """Looks up an edge hostname's details (notably `useCases`), needed to
        build a valid replacement hostname entry when cloning to a different
        target edge hostname."""
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        data = self._get(f"/papi/v1/edgehostnames/{edge_hostname_id}", params)
        items = data.get("edgeHostnames", {}).get("items", [])
        if not items:
            raise PapiError(f"Edge hostname '{edge_hostname_id}' not found", payload=data)
        return items[0]

    # -- property / version lifecycle ---------------------------------------

    def create_property(
        self,
        property_name: str,
        product_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
    ) -> str:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        body = {"productId": product_id, "propertyName": property_name, "ruleFormat": "latest"}
        try:
            resp = self._post("/papi/v1/properties", params, body)
        except PapiError as error:
            detail = error.payload.get("detail", "") if isinstance(error.payload, dict) else ""
            if error.status_code in (400, 409) and "already being used" in detail:
                raise PapiError(
                    f"PAPI reports that property name '{property_name}' is already in use, "
                    "but lookup did not find an accessible property in the target scope. "
                    "A recently deleted property may still reserve the name, or the property "
                    "may belong to another group. Choose a new name or confirm the property's "
                    "group and deletion status in Property Manager before retrying.",
                    status_code=error.status_code, payload=error.payload,
                ) from error
            raise
        link = resp.json().get("propertyLink", "")
        match = re.search(r"(prp_\d+)", link)
        if not match:
            raise PapiError("Property created but couldn't parse propertyId from link", payload=resp.json())
        return match.group(1)

    def create_new_version(
        self,
        property_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        create_from_version: int,
    ) -> int:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        body = {"createFromVersion": create_from_version}
        resp = self._post(f"/papi/v1/properties/{property_id}/versions", params, body)
        link = resp.json()["versionLink"]
        return int(link.split("/versions/")[1].split("?")[0])

    def activate(
        self,
        property_id: str,
        version: int,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        network: str,
        notify_emails: list[str],
        note: str = "Automated clone activation",
    ) -> ActivationResult:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        body = {
            "propertyVersion": version,
            "network": network,
            "complianceRecord": {"noncomplianceReason": "NO_PRODUCTION_TRAFFIC"},
            "note": note,
            "notifyEmails": notify_emails or [],
            "acknowledgeAllWarnings": True,
            "useFastFallback": False,
        }
        try:
            resp = self._post(f"/papi/v1/properties/{property_id}/activations", params, body)
        except PapiError as e:
            errors = (e.payload or {}).get("errors", []) if isinstance(e.payload, dict) else []
            return ActivationResult(None, "FAILED", errors or [{"detail": str(e)}])

        link = resp.json().get("activationLink", "")
        match = re.search(r"(atv_\d+)", link)
        if not match:
            return ActivationResult(None, "FAILED", [{"detail": "Could not parse activation id"}])
        activation_id = match.group(1)
        return self.wait_for_activation(
            property_id, activation_id, contract_id, group_id, account_switch_key
        )

    def wait_for_activation(
        self,
        property_id: str,
        activation_id: str,
        contract_id: str,
        group_id: str,
        account_switch_key: Optional[str],
        max_checks: int = 20,
        poll_interval_seconds: int = 60,
    ) -> ActivationResult:
        params = {"contractId": contract_id, "groupId": group_id}
        if account_switch_key:
            params["accountSwitchKey"] = account_switch_key
        for _ in range(max_checks):
            data = self._get(
                f"/papi/v1/properties/{property_id}/activations/{activation_id}", params
            )
            items = data.get("activations", {}).get("items", [])
            status = items[0]["status"] if items else "UNKNOWN"
            if status == "ACTIVE":
                return ActivationResult(activation_id, status, [])
            if status in ("FAILED", "ABORTED", "DEACTIVATED"):
                return ActivationResult(activation_id, status, items[0].get("errors", []) if items else [])
            time.sleep(poll_interval_seconds)
        return ActivationResult(activation_id, "TIMED_OUT", [])
