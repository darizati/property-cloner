import copy
import datetime
import asyncio
import tempfile
import unittest
from email import message_from_string
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app import db, models
from app.api import clones
from app.config import Settings
from app.schemas import CloneJobOut, CloneProfileCreate, FetchDiffRequest
from app.papi.client import PapiClient, PapiError
from app.papi.rule_tree import apply_policies, diff_rule_sets, flatten_rule_tree, summarize_rule_changes
from app.services import clone_service, diff_service, notify_service
from app.main import FrontendFiles


class FrontendHostingTests(unittest.TestCase):
    def test_frontend_serves_files_and_browser_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<html>Property Cloner</html>")
            (root / "app.js").write_text("export default {};")
            frontend = FrontendFiles(directory=root, html=True)
            for path, filename in ((".", "index.html"), ("profiles", "index.html"),
                                   ("profiles/1/edit", "index.html"), ("jobs/33", "index.html"),
                                   ("app.js", "app.js")):
                with self.subTest(path=path):
                    scope = {"method": "GET", "path": "/" if path == "." else f"/{path}", "headers": []}
                    response = asyncio.run(frontend.get_response(path, scope))
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(Path(response.path).resolve(), (root / filename).resolve())

    def test_frontend_does_not_mask_missing_api_or_asset_routes(self):
        from starlette.exceptions import HTTPException
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "index.html").write_text("<html>Property Cloner</html>")
            frontend = FrontendFiles(directory=directory, html=True)
            for path in ("api", "api/unknown", "assets/missing.js"):
                with self.subTest(path=path), self.assertRaises(HTTPException) as raised:
                    asyncio.run(frontend.get_response(path, {"method": "GET", "path": f"/{path}", "headers": []}))
                self.assertEqual(raised.exception.status_code, 404)


class GraphEmailTests(unittest.TestCase):
    def graph_settings(self, **overrides):
        return Settings(_env_file=None, **{
            "mail_provider": "graph", "graph_tenant_id": "test-tenant",
            "graph_client_id": "test-app", "graph_client_secret": "test-secret",
            "graph_sender": "sender@example.com", **overrides,
        })

    def test_graph_authenticates_and_submits_email_without_smtp(self):
        token_response = MagicMock(status_code=200)
        token_response.json.return_value = {"access_token": "test-token"}
        with patch.object(notify_service, "settings", self.graph_settings()), \
             patch.object(notify_service.requests, "post", side_effect=[token_response, MagicMock(status_code=202)]) as post, \
             patch.object(notify_service.smtplib, "SMTP") as smtp:
            self.assertTrue(notify_service.send_email(["recipient@example.com"], "Test", "Body"))
        smtp.assert_not_called()
        self.assertEqual(post.call_count, 2)
        token_call, mail_call = post.call_args_list
        self.assertEqual(token_call.args[0], "https://login.microsoftonline.com/test-tenant/oauth2/v2.0/token")
        self.assertEqual(token_call.kwargs["data"], {
            "client_id": "test-app", "client_secret": "test-secret",
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
        })
        self.assertEqual(mail_call.args[0], "https://graph.microsoft.com/v1.0/users/sender%40example.com/sendMail")
        self.assertEqual(mail_call.kwargs["headers"], {"Authorization": "Bearer test-token"})
        self.assertEqual(mail_call.kwargs["json"], {
            "message": {"subject": "Test", "body": {"contentType": "Text", "content": "Body"},
                        "toRecipients": [{"emailAddress": {"address": "recipient@example.com"}}]},
            "saveToSentItems": True,
        })
        for request_call in post.call_args_list:
            self.assertEqual(request_call.kwargs["timeout"], 30)
            self.assertFalse(request_call.kwargs["allow_redirects"])

    def test_html_report_is_sent_as_multipart_with_plain_text_fallback(self):
        with patch.object(notify_service, "settings", self.graph_settings(mail_provider="smtp")), \
             patch.object(notify_service.smtplib, "SMTP") as smtp:
            self.assertTrue(notify_service.send_email(["recipient@example.com"], "Report", "Plain report", html_body="<h1>Report</h1>"))
        message = message_from_string(smtp.return_value.__enter__.return_value.sendmail.call_args.args[2])
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        parts = message.get_payload()
        self.assertEqual([part.get_content_type() for part in parts], ["text/plain", "text/html"])
        self.assertEqual(parts[0].get_payload(decode=True).decode("utf-8"), "Plain report")
        self.assertEqual(parts[1].get_payload(decode=True).decode("utf-8"), "<h1>Report</h1>")

    def test_graph_supports_html_reports(self):
        token = MagicMock(status_code=200)
        token.json.return_value = {"access_token": "test-token"}
        with patch.object(notify_service, "settings", self.graph_settings()), \
             patch.object(notify_service.requests, "post", side_effect=[token, MagicMock(status_code=202)]) as post:
            self.assertTrue(notify_service.send_email(["recipient@example.com"], "Report", "Plain", html_body="<h1>Report</h1>"))
        self.assertEqual(post.call_args.kwargs["json"]["message"]["body"], {"contentType": "HTML", "content": "<h1>Report</h1>"})

    def test_graph_requires_all_credentials_and_sender_without_smtp_fallback(self):
        for field in ("graph_tenant_id", "graph_client_id", "graph_client_secret", "graph_sender"):
            with self.subTest(field=field), \
                 patch.object(notify_service, "settings", self.graph_settings(**{field: ""})), \
                 patch.object(notify_service.requests, "post") as post, \
                 patch.object(notify_service.smtplib, "SMTP") as smtp, \
                 self.assertLogs(notify_service.logger, level="ERROR"):
                self.assertFalse(notify_service.send_email(["recipient@example.com"], "Test", "Body"))
                post.assert_not_called()
                smtp.assert_not_called()

    def test_graph_failures_do_not_log_secrets_or_retry(self):
        valid_token = MagicMock(status_code=200)
        valid_token.json.return_value = {"access_token": "test-token"}
        invalid_json = MagicMock(status_code=200)
        invalid_json.json.side_effect = ValueError("test-secret test-token")
        invalid_payload = MagicMock(status_code=200)
        invalid_payload.json.return_value = []
        missing_token = MagicMock(status_code=200)
        missing_token.json.return_value = {}
        cases = {
            "authentication rejected": [MagicMock(status_code=401)],
            "authentication redirect": [MagicMock(status_code=302)],
            "invalid JSON": [invalid_json],
            "invalid payload": [invalid_payload],
            "missing token": [missing_token],
            "authentication timeout": [notify_service.requests.Timeout("test-secret test-token")],
            "mail permission denied": [valid_token, MagicMock(status_code=403)],
            "mail rate limited": [valid_token, MagicMock(status_code=429)],
            "mail redirect": [valid_token, MagicMock(status_code=302)],
            "mail timeout": [valid_token, notify_service.requests.Timeout("test-secret test-token")],
        }
        for name, responses in cases.items():
            with self.subTest(name=name), \
                 patch.object(notify_service, "settings", self.graph_settings()), \
                 patch.object(notify_service.requests, "post", side_effect=responses) as post, \
                 patch.object(notify_service.smtplib, "SMTP") as smtp, \
                 self.assertLogs(notify_service.logger, level="ERROR") as captured:
                self.assertFalse(notify_service.send_email(["recipient@example.com"], "Test", "Body"))
                self.assertEqual(post.call_count, len(responses))
                smtp.assert_not_called()
                self.assertNotIn("test-secret", " ".join(captured.output))
                self.assertNotIn("test-token", " ".join(captured.output))

    def test_no_recipients_make_no_network_requests(self):
        with patch.object(notify_service, "settings", self.graph_settings()), \
             patch.object(notify_service.requests, "post") as post, \
             patch.object(notify_service.smtplib, "SMTP") as smtp:
            self.assertTrue(notify_service.send_email([], "Test", "Body"))
        post.assert_not_called()
        smtp.assert_not_called()

    def test_smtp_remains_available_and_is_the_default(self):
        self.assertEqual(Settings.model_fields["mail_provider"].default, "smtp")
        with patch.object(notify_service, "settings", self.graph_settings(
            mail_provider="smtp", smtp_host="relay.example.com", smtp_port=587,
            smtp_use_tls=True, smtp_username="smtp-user", smtp_password="smtp-secret",
        )), patch.object(notify_service.requests, "post") as post, \
             patch.object(notify_service.smtplib, "SMTP") as smtp:
            self.assertTrue(notify_service.send_email(["recipient@example.com"], "Test", "Body"))
        post.assert_not_called()
        smtp.assert_called_once_with("relay.example.com", 587, timeout=30)
        server = smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once_with()
        server.login.assert_called_once_with("smtp-user", "smtp-secret")
        server.sendmail.assert_called_once()

    def test_graph_secret_is_redacted_and_unknown_providers_are_rejected(self):
        self.assertNotIn("test-secret", repr(self.graph_settings()))
        with self.assertRaises(ValueError):
            self.graph_settings(mail_provider="unknown")


class CloneDraftTests(unittest.TestCase):
    def existing_clone(self):
        prod, sqa = MagicMock(), MagicMock()
        prod.resolve_property.return_value = (
            {"propertyId": "prp_10", "groupId": "grp_20"}, "prod_key"
        )
        prod.get_latest_version.return_value = 2
        tree = {"rules": {"name": "default", "behaviors": [], "children": []}}
        prod.get_rule_tree.return_value = tree
        prod.get_hostnames.return_value = []
        sqa.resolve_property.return_value = (
            {"propertyId": "prp_30", "groupId": "grp_40", "assetId": "aid_99"}, "key"
        )
        sqa.get_latest_version.return_value = 21
        sqa.get_version_details.return_value = {
            "propertyVersion": 21, "productionStatus": "INACTIVE", "stagingStatus": "INACTIVE"
        }
        sqa.get_activations.return_value = [
            {"propertyVersion": 20, "activationType": "ACTIVATE", "network": "PRODUCTION", "status": "ACTIVE"}
        ]
        sqa.get_hostnames.return_value = [{"cnameFrom": "sqa.example.com"}]
        sqa.get_rule_tree.return_value = tree
        sqa.activate.return_value = SimpleNamespace(status="ACTIVE", activation_id="atv_1", errors=[])
        profile = SimpleNamespace(
            name="test", production_property_name="prod", production_contract_id="prod_contract",
            production_account_name="prod_account", production_account_type="internal",
            sqa_property_name="sqa", sqa_contract_id="sqa_contract", sqa_account_name="sqa_account",
            sqa_account_type="internal", sqa_group_id="grp_40", product_id="prd_test",
            policies={}, field_overrides={}, notify_emails=[], attempt_activation=True,
        )
        job = models.CloneJob(id=1, profile_id=1, trigger="manual", log="", removed_rules=[])
        return prod, sqa, job, profile

    def run_clone(self, prod, sqa, job, profile):
        with patch.object(clone_service, "PapiClient", side_effect=[prod, sqa]), \
             patch.object(clone_service, "notify_job_outcome"):
            clone_service._run_clone(MagicMock(), job, profile)

    def test_existing_editable_draft_is_saved_and_activated_without_new_version(self):
        prod, sqa, job, profile = self.existing_clone()
        self.run_clone(prod, sqa, job, profile)
        sqa.create_new_version.assert_not_called()
        sqa.upload_rule_tree.assert_called_once_with(
            "prp_30", 21, "sqa_contract", "grp_40", "key", prod.get_rule_tree.return_value
        )
        sqa.activate.assert_called_once_with(
            "prp_30", 21, "sqa_contract", "grp_40", "key",
            network="PRODUCTION", notify_emails=[],
        )
        self.assertEqual(job.sqa_version, 21)
        self.assertEqual(job.activation_status, "ACTIVE")
        self.assertEqual(job.status, models.JobStatus.SUCCESS)
        self.assertEqual(job.activation_id, "atv_1")
        self.assertEqual(CloneJobOut.model_validate(job).activation_network, "PRODUCTION")
        self.assertLess(
            [call[0] for call in sqa.mock_calls].index("upload_rule_tree"),
            [call[0] for call in sqa.mock_calls].index("activate"),
        )

    def test_activation_is_opt_in_even_for_an_eligible_existing_property(self):
        prod, sqa, job, profile = self.existing_clone()
        profile.attempt_activation = False
        self.run_clone(prod, sqa, job, profile)
        sqa.upload_rule_tree.assert_called_once()
        sqa.create_new_version.assert_not_called()
        sqa.activate.assert_not_called()
        self.assertIn("activation disabled", job.log)
        self.assertEqual(job.status, models.JobStatus.SUCCESS)

    def test_active_sqa_variables_are_available_and_follow_explicit_decisions(self):
        for policy in ("sqa", "remove"):
            with self.subTest(policy=policy):
                prod, sqa, job, profile = self.existing_clone()
                profile.attempt_activation = False
                profile.policies = {"default::variable::PMUSER_HOST_HEADER#1": policy}
                declaration = {"name": "PMUSER_HOST_HEADER", "value": "sqa.example.com", "hidden": False}
                draft = {"rules": {"name": "default", "variables": [], "behaviors": []}}
                active = {"rules": {"name": "default", "variables": [declaration], "behaviors": [
                    {"name": "origin", "options": {"hostname": "old.example.com"}},
                ]}}
                sqa.get_latest_version.side_effect = [21, 20]
                sqa.get_rule_tree.side_effect = [draft, active]
                self.run_clone(prod, sqa, job, profile)
                uploaded = sqa.upload_rule_tree.call_args.args[-1]
                self.assertEqual(uploaded["rules"]["variables"], [declaration] if policy == "sqa" else [])
                self.assertEqual(uploaded["rules"]["behaviors"], [])
                self.assertEqual(draft["rules"]["variables"], [])
                sqa.create_new_version.assert_not_called()
                sqa.activate.assert_not_called()

    def test_variable_diff_identifies_the_active_sqa_source(self):
        prod, sqa, job, profile = self.existing_clone()
        prod.resolve_property.return_value[0]["propertyVersion"] = 2
        sqa.get_latest_version.side_effect = [21, 20]
        declaration = {"name": "PMUSER_HOST_HEADER", "value": "sqa.example.com", "hidden": False}
        sqa.get_rule_tree.side_effect = [
            {"rules": {"name": "default", "variables": [], "behaviors": []}},
            {"rules": {"name": "default", "variables": [declaration], "behaviors": []}},
        ]
        reference = {"property_name": "test", "account_name": "test", "account_type": "internal", "contract_id": "test"}
        request = FetchDiffRequest(product_id="prd_test", production=reference, shared=reference, sqa_group_id="grp_40")
        with patch.object(diff_service, "PapiClient", side_effect=[prod, sqa]):
            response = diff_service.fetch_diff(request)
        self.assertEqual(response.sqa_version, 21)
        variable = next(node for node in response.nodes if node.kind == "variable")
        self.assertEqual(variable.name, "PMUSER_HOST_HEADER")
        self.assertEqual(variable.sqa_source_version, 20)
        self.assertEqual(variable.sqa_options, {"value": "sqa.example.com", "hidden": False})
        self.assertEqual(variable.default_policy, "sqa")

    def test_variable_sources_allow_never_activated_properties_and_propagate_errors(self):
        client = MagicMock()
        draft = {"rules": {"name": "default", "variables": []}}
        client.get_rule_tree.return_value = draft
        client.get_latest_version.side_effect = [PapiError("No active version", 404), None]
        tree, recovered = diff_service.fetch_sqa_source_tree(client, "prp_30", 21, "contract", "group", "key")
        self.assertEqual(tree, draft)
        self.assertEqual(recovered, {})
        client.get_latest_version.side_effect = PapiError("Permission denied", 403)
        with self.assertRaises(PapiError):
            diff_service.fetch_sqa_source_tree(client, "prp_30", 21, "contract", "group", "key")

    def test_activated_versions_require_a_new_version(self):
        for network, status in (("PRODUCTION", "ACTIVE"), ("PRODUCTION", "DEACTIVATED"), ("STAGING", "ACTIVE")):
            with self.subTest(network=network, status=status):
                prod, sqa, job, profile = self.existing_clone()
                sqa.get_activations.return_value = [{
                    "propertyVersion": 21, "activationType": "ACTIVATE", "network": network, "status": status,
                }]
                if status == "ACTIVE":
                    field = "productionStatus" if network == "PRODUCTION" else "stagingStatus"
                    sqa.get_version_details.return_value[field] = "ACTIVE"
                sqa.create_new_version.return_value = 22
                self.run_clone(prod, sqa, job, profile)
                sqa.create_new_version.assert_called_once_with(
                    "prp_30", "sqa_contract", "grp_40", "key", create_from_version=21,
                )
                self.assertEqual(sqa.upload_rule_tree.call_args.args[1], 22)
                self.assertEqual(sqa.activate.call_args.kwargs["network"], network)

    def test_existing_properties_need_hostnames_and_prior_activation(self):
        for missing in ("hostnames", "activation_history"):
            with self.subTest(missing=missing):
                prod, sqa, job, profile = self.existing_clone()
                if missing == "hostnames":
                    sqa.get_hostnames.return_value = []
                else:
                    sqa.get_activations.return_value = []
                self.run_clone(prod, sqa, job, profile)
                sqa.activate.assert_not_called()
                sqa.create_new_version.assert_not_called()
                self.assertEqual(job.sqa_version, 21)
                self.assertIn("manual activation required", job.log)

    def test_activation_failure_keeps_saved_tree_and_reuses_version_on_retry(self):
        prod, sqa, job, profile = self.existing_clone()
        tree = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "production.example.com"}}
        ], "children": []}}
        prod.get_rule_tree.return_value = tree
        profile.policies = {"default::behavior::origin#1": "prod"}
        profile.field_overrides = {"default::behavior::origin#1": {"hostname": "sqa.example.com"}}
        sqa.activate.return_value = SimpleNamespace(
            status="FAILED", activation_id="atv_1", errors=[{"detail": "Invalid rule configuration"}],
        )
        with self.assertRaisesRegex(PapiError, "Rule tree saved in SQA v21.*Invalid rule configuration"):
            self.run_clone(prod, sqa, job, profile)
        saved = sqa.upload_rule_tree.call_args.args[-1]
        self.assertEqual(saved["rules"]["behaviors"][0]["options"]["hostname"], "sqa.example.com")
        self.assertEqual(job.activation_status, "FAILED")
        self.assertEqual(job.removed_rules, [])
        self.assertIsNone(profile.__dict__.get("last_known_prod_version"))
        sqa.get_activations.return_value.append({
            "propertyVersion": 21, "activationType": "ACTIVATE", "network": "PRODUCTION", "status": "FAILED",
        })
        sqa.activate.return_value = SimpleNamespace(status="ACTIVE", activation_id="atv_2", errors=[])
        self.run_clone(prod, sqa, job, profile)
        sqa.create_new_version.assert_not_called()
        self.assertEqual(sqa.upload_rule_tree.call_count, 2)
        self.assertEqual(sqa.upload_rule_tree.call_args.args[1], 21)
        self.assertEqual(sqa.upload_rule_tree.call_args.args[-1], saved)

    def test_pending_activation_does_not_create_version_or_overwrite_rules(self):
        prod, sqa, job, profile = self.existing_clone()
        sqa.get_activations.return_value.append({
            "propertyVersion": 21, "activationType": "ACTIVATE", "network": "PRODUCTION", "status": "PENDING",
        })
        with self.assertRaisesRegex(PapiError, "activation in progress"):
            self.run_clone(prod, sqa, job, profile)
        sqa.create_new_version.assert_not_called()
        sqa.upload_rule_tree.assert_not_called()
        sqa.activate.assert_not_called()

    def test_missing_sqa_choices_prevent_all_property_writes(self):
        prod, sqa, job, profile = self.existing_clone()
        profile.policies = {"default/Conditional Origin Group/SQA::behavior::origin#1": "sqa"}
        with self.assertRaisesRegex(ValueError, "Saved SQA choices are missing"):
            self.run_clone(prod, sqa, job, profile)
        sqa.create_new_version.assert_not_called()
        sqa.create_property.assert_not_called()
        sqa.upload_rule_tree.assert_not_called()
        sqa.activate.assert_not_called()

    def test_clones_save_without_activation_or_hostname_writes(self):
        for exists in (False, True):
            with self.subTest(existing_property=exists):
                prod, sqa = MagicMock(), MagicMock()
                prod.resolve_property.return_value = (
                    {"propertyId": "prp_10", "groupId": "grp_20"}, "prod_key"
                )
                prod.get_latest_version.return_value = 2
                tree = {"rules": {"name": "default", "behaviors": [], "children": []}}
                prod.get_rule_tree.return_value = tree
                prod.get_hostnames.return_value = []
                sqa.resolve_property.return_value = (
                    {"propertyId": "prp_30", "groupId": "grp_40", "propertyVersion": 1}
                    if exists else None,
                    "B-123:client",
                )
                sqa.get_latest_version.return_value = 1
                sqa.get_version_details.return_value = {
                    "propertyVersion": 1, "productionStatus": "INACTIVE", "stagingStatus": "INACTIVE",
                }
                sqa.get_activations.return_value = []
                sqa.get_rule_tree.return_value = tree
                sqa.get_property_details.return_value = {"assetId": "aid_99"}
                sqa.create_property.return_value = "prp_30"
                sqa.create_new_version.return_value = 3
                profile = SimpleNamespace(
                    name="test", production_property_name="prod",
                    production_contract_id="prod_contract", production_account_name="prod_account",
                    production_account_type="internal", sqa_property_name="sqa",
                    sqa_contract_id="sqa_contract", sqa_account_name="sqa_account",
                    sqa_account_type="internal", sqa_group_id="grp_40", product_id="prd_test",
                    policies={}, field_overrides={}, notify_emails=[],
                )
                job = models.CloneJob(id=1, profile_id=1, trigger="manual", log="")
                with patch.object(clone_service, "PapiClient", side_effect=[prod, sqa]), \
                     patch.object(clone_service, "notify_job_outcome"):
                    clone_service._run_clone(MagicMock(), job, profile)
                sqa.activate.assert_not_called()
                sqa.update_hostnames.assert_not_called()
                prod.get_edge_hostname_details.assert_not_called()
                sqa.upload_rule_tree.assert_called_once()
                if exists:
                    sqa.create_property.assert_not_called()
                    sqa.create_new_version.assert_not_called()
                    self.assertEqual(job.sqa_version, 1)
                else:
                    sqa.create_property.assert_called_once()
                    sqa.create_new_version.assert_not_called()
                self.assertEqual(job.status, models.JobStatus.SUCCESS)
                self.assertEqual(job.removed_rules, [])
                self.assertEqual(job.created_new_property, not exists)
                sqa.resolve_property.assert_called_once_with(
                    "sqa", "sqa_contract", "sqa_account", "internal", group_id="grp_40"
                )
                if exists:
                    sqa.get_latest_version.assert_any_call(
                        "prp_30", "sqa_contract", "grp_40", "B-123:client", activated_on=None
                    )
                self.assertIn("manual activation required", job.log)
                self.assertEqual(
                    job.sqa_property_url,
                    clone_service.settings.property_manager_url.rstrip('/') + "/#/property/99?gid=40",
                )
                sqa.get_property_details.assert_called_once_with(
                    "prp_30", "sqa_contract", "grp_40", "B-123:client"
                )
                self.assertEqual(CloneJobOut.model_validate(job).sqa_property_url, job.sqa_property_url)

    def test_database_update_preserves_history_and_is_repeatable(self):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE clone_jobs (id INTEGER PRIMARY KEY, log TEXT)"))
            connection.execute(text("INSERT INTO clone_jobs VALUES (1, 'existing history')"))
            connection.execute(text("CREATE TABLE clone_profiles (id INTEGER PRIMARY KEY, name TEXT)"))
            connection.execute(text("INSERT INTO clone_profiles VALUES (1, 'existing profile')"))
        with patch.object(db, "engine", engine):
            db.initialize_database()
            db.initialize_database()
        with engine.connect() as connection:
            self.assertEqual(
                connection.execute(text("SELECT log, sqa_property_url, change_summary FROM clone_jobs")).one(),
                ("existing history", None, None),
            )
            self.assertEqual(
                connection.execute(text("SELECT name, attempt_activation FROM clone_profiles")).one(),
                ("existing profile", False),
            )
        engine.dispose()

    def test_profile_activation_preference_is_saved_and_loaded(self):
        engine = create_engine("sqlite:///:memory:")
        db.Base.metadata.create_all(bind=engine)
        reference = {"property_name": "test", "account_name": "test", "account_type": "internal", "contract_id": "test"}
        payload = CloneProfileCreate(
            name="test", product_id="prd_test", production=reference, shared=reference,
            sqa_group_id="grp_40", policies={"default::variable::PMUSER_HOST_HEADER#1": "sqa"},
            field_overrides={"default::variable::PMUSER_HOST_HEADER#1": {"value": "fixed", "hidden": False}},
        )
        self.assertFalse(payload.attempt_activation)
        with Session(engine) as session:
            saved = clones.create_profile(payload, session)
            self.assertFalse(saved.attempt_activation)
            with patch("app.services.scheduler_service.scheduler_backend"):
                for selected in (True, False):
                    updated = clones.update_profile(saved.id, payload.model_copy(update={"attempt_activation": selected}), session)
                    self.assertEqual(updated.attempt_activation, selected)
                    session.expire_all()
                    loaded = clones.get_profile(saved.id, session)
                    self.assertEqual(loaded.attempt_activation, selected)
                    self.assertEqual(loaded.policies, payload.policies)
                    self.assertEqual(loaded.field_overrides, payload.field_overrides)
        engine.dispose()

    def test_fresh_database_has_review_link(self):
        engine = create_engine("sqlite:///:memory:")
        with patch.object(db, "engine", engine):
            db.initialize_database()
        self.assertIn(
            "sqa_property_url", {column["name"] for column in inspect(engine).get_columns("clone_jobs")}
        )
        self.assertTrue({"activation_status", "activation_id", "activation_network"}.issubset(
            {column["name"] for column in inspect(engine).get_columns("clone_jobs")}
        ))
        engine.dispose()

    def test_clone_records_saved_change_summary_against_existing_sqa(self):
        prod, sqa, job, profile = self.existing_clone()
        profile.schedule_cron = "0 9 * * 1-5"
        profile.attempt_activation = False
        sqa.upload_rule_tree.return_value = MagicMock(content=b"{}")
        sqa.upload_rule_tree.return_value.json.return_value = {
            "warnings": [{"errorLocation": "#/rules", "detail": "Deprecated setting"}],
            "errors": [{"errorLocation": "#/rules", "detail": "Invalid rule setting"}],
        }
        prod.get_rule_tree.return_value = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "prod.example.com"}},
        ]}}
        sqa.get_rule_tree.return_value = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "sqa.example.com"}},
        ]}}
        self.run_clone(prod, sqa, job, profile)
        self.assertEqual(job.change_summary["state"], "saved")
        self.assertEqual(job.change_summary["baseline_version"], 21)
        self.assertEqual(job.change_summary["counts"]["updated"], 1)
        self.assertEqual(job.change_summary["changes"][0]["fields"], ["hostname"])
        self.assertEqual(job.change_summary["context"]["schedule_cron"], profile.schedule_cron)
        self.assertNotIn("prod.example.com", str(job.change_summary))
        self.assertEqual(job.change_summary["validation"]["warnings"][0]["detail"], "Deprecated setting")
        self.assertEqual(job.change_summary["validation"]["errors"][0]["detail"], "Invalid rule setting")
        engine = create_engine("sqlite:///:memory:")
        db.Base.metadata.create_all(bind=engine)
        with Session(engine) as session:
            stored_profile = models.CloneProfile(**vars(profile))
            session.add(stored_profile)
            session.flush()
            job.profile = stored_profile
            session.add(job)
            session.commit()
            session.expire_all()
            loaded = session.get(models.CloneJob, job.id)
            self.assertEqual(CloneJobOut.model_validate(loaded).change_summary["state"], "saved")
        engine.dispose()

    def test_rule_upload_failure_reports_incomplete_draft_and_validation_details(self):
        prod, sqa, job, profile = self.existing_clone()
        response = MagicMock(status_code=400)
        response.request.method = "PUT"
        response.request.url = "https://example.test/papi/rules"
        response.json.return_value = {
            "detail": "Your input has a syntax problem.",
            "errors": [{"errorLocation": "#/rules/behaviors/0", "detail": "Invalid option"}],
        }
        client = PapiClient.__new__(PapiClient)
        try:
            client._check(response)
        except PapiError as error:
            sqa.upload_rule_tree.side_effect = error

        with self.assertRaises(PapiError) as raised:
            self.run_clone(prod, sqa, job, profile)

        message = str(raised.exception)
        self.assertIn("version 21", message)
        self.assertIn("incomplete", message)
        self.assertIn("#/rules/behaviors/0", message)
        self.assertIn("Invalid option", message)
        self.assertEqual(job.change_summary["state"], "planned")
        sqa.activate.assert_not_called()

    def test_upload_schema_error_identifies_actual_feature_and_expected_type(self):
        prod, sqa, job, profile = self.existing_clone()
        prod.get_rule_tree.return_value = {"rules": {"name": "default", "behaviors": [
            {"name": "cacheRedirect", "options": {"enabled": "false"}},
        ]}}
        location = "/rules/behaviors/0/options/enabled"
        issue = {
            "location": location,
            "schemaLocation": "/definitions/catalog/behaviors/cacheRedirect/properties/options/properties/enabled",
            "detail": "does not have a value in the enumeration [false, true]",
            "foundValue": "false", "allowedValues": [False, True],
        }
        unrelated = {
            **issue, "schemaLocation": "/definitions/catalog/behaviors/other/properties/options/properties/enabled",
        }
        payload = {"detail": "Schema error", "errors": [unrelated, issue, issue]}
        sqa.upload_rule_tree.side_effect = PapiError("generic API error", 400, payload)

        with self.assertRaises(PapiError) as raised:
            self.run_clone(prod, sqa, job, profile)

        message = str(raised.exception)
        self.assertIn('cacheRedirect.enabled: received string "false"; expected boolean [false, true]', message)
        self.assertIn(location, message)
        self.assertIn("fixed override", message)
        self.assertEqual(message.count("cacheRedirect.enabled:"), 1)
        self.assertNotIn("other.enabled", message)
        self.assertEqual(sqa.upload_rule_tree.call_args.args[-1]["rules"]["behaviors"][0]["options"]["enabled"], "false")
        sqa.activate.assert_not_called()

    def test_schema_errors_without_identifiable_feature_fall_back_to_papi_message(self):
        prod, sqa, job, profile = self.existing_clone()
        sqa.upload_rule_tree.side_effect = PapiError(
            "Schema rejected by PAPI", 400, {"detail": "Rejected", "errors": [{"detail": "Unknown"}]},
        )
        with self.assertRaises(PapiError) as raised:
            self.run_clone(prod, sqa, job, profile)
        self.assertIn("Schema rejected by PAPI", str(raised.exception))

    def test_papi_job_failure_is_recorded_without_python_traceback(self):
        job = SimpleNamespace(id=1, profile=SimpleNamespace(name="test"), log="")
        session = MagicMock()
        session.get.return_value = job
        with patch.object(clone_service, "SessionLocal", return_value=session), \
             patch.object(clone_service, "_run_clone", side_effect=PapiError("Upload rejected", 400)), \
             patch.object(clone_service, "notify_job_outcome"):
            clone_service.execute_job(job.id)
        self.assertEqual(job.status, models.JobStatus.FAILED)
        self.assertEqual(job.error_summary, "Upload rejected")
        self.assertIn("Upload rejected", job.log)
        self.assertNotIn("Traceback", job.log)

    def test_unexpected_job_failure_keeps_python_traceback(self):
        job = SimpleNamespace(id=1, profile=SimpleNamespace(name="test"), log="")
        session = MagicMock()
        session.get.return_value = job
        with patch.object(clone_service, "SessionLocal", return_value=session), \
             patch.object(clone_service, "_run_clone", side_effect=RuntimeError("Unexpected")), \
             patch.object(clone_service, "notify_job_outcome"):
            clone_service.execute_job(job.id)
        self.assertIn("Traceback", job.log)

    def test_scheduled_lookup_failure_is_recorded_and_notified(self):
        prod, _, _, profile = self.existing_clone()
        profile.id = 1
        profile.schedule_cron = "0 9 * * *"
        prod.resolve_property.side_effect = PapiError("Lookup unavailable")
        session = MagicMock()
        session.get.return_value = profile
        with patch.object(clone_service, "SessionLocal", return_value=session), \
             patch.object(clone_service, "PapiClient", return_value=prod), \
             patch.object(clone_service, "notify_job_outcome") as notify:
            clone_service.rerun_scheduled(profile.id)
        job = notify.call_args.args[1]
        self.assertEqual(job.status, models.JobStatus.FAILED)
        self.assertEqual(job.trigger, "scheduled")
        self.assertEqual(job.error_summary, "Lookup unavailable")
        self.assertIn("Lookup unavailable", job.log)
        self.assertNotIn("Traceback", job.log)
        self.assertEqual(job.change_summary["context"]["schedule_cron"], profile.schedule_cron)

    def test_scheduled_checks_record_skip_reasons_without_uploads(self):
        for last_version in (1, 2):
            with self.subTest(last_version=last_version):
                prod, sqa, _, profile = self.existing_clone()
                profile.id = 1
                profile.last_known_prod_version = last_version
                profile.schedule_cron = "0 9 * * *"
                session = MagicMock()
                session.get.return_value = profile
                with patch.object(clone_service, "SessionLocal", return_value=session), \
                     patch.object(clone_service, "PapiClient", side_effect=[prod, sqa]), \
                     patch.object(clone_service, "notify_job_outcome") as notify:
                    clone_service.rerun_scheduled(profile.id)
                job = notify.call_args.args[1]
                expected = models.JobStatus.SKIPPED_NO_NEWER_VERSION if last_version == 2 else models.JobStatus.SKIPPED_NO_RULE_CHANGE
                self.assertEqual(job.status, expected)
                self.assertIn("Skipping", job.log)
                sqa.upload_rule_tree.assert_not_called()
                sqa.activate.assert_not_called()

    def test_report_escapes_content_and_uses_recorded_schedule(self):
        profile = SimpleNamespace(
            notify_emails=["test@example.com"], schedule_cron=None,
            production_property_name="new profile source", sqa_property_name="new profile target",
        )
        job = SimpleNamespace(
            id=34, status=models.JobStatus.FAILED, trigger="scheduled", profile=profile,
            activation_status="FAILED", activation_network="PRODUCTION",
            sqa_property_url="https://example.com/property/30", prod_version=68,
            sqa_version=22, created_new_property=False, removed_rules=[],
            error_summary="Activation rejected <script>alert(1)</script>", log="<b>log</b>",
            started_at=datetime.datetime(2026, 11, 4, 9),
            finished_at=datetime.datetime(2026, 11, 4, 9, 2, tzinfo=datetime.UTC),
            change_summary={
                "state": "saved", "baseline_version": 21,
                "context": {"schedule_cron": "0 9 * * *", "schedule_timezone": "UTC",
                            "production_property_name": "original source", "sqa_property_name": "original target"},
                "counts": {"added": 0, "updated": 1, "removed": 0},
                "validation": {"errors": [], "warnings": [{"location": "#/rules", "detail": "Deprecated setting"}]},
                "changes": [{"action": "updated", "kind": "behavior", "name": "origin",
                             "rule_path": "default/<SQA>", "fields": ["hostname"]}],
            },
        )
        with patch.object(notify_service, "send_email") as send:
            notify_service.notify_job_outcome("<Trial & test>", job)
        body, html = send.call_args.args[2], send.call_args.kwargs["html_body"]
        self.assertIn("Duration: 2m 0s", body)
        self.assertIn("Schedule at run: 0 9 * * *", body)
        self.assertIn("original source", body)
        self.assertNotIn("new profile source", body)
        self.assertIn("Uploaded changes", body)
        self.assertIn("Deprecated setting", body)
        self.assertIn("UPDATED | default/<SQA> | behavior: origin | Fields: hostname", body)
        self.assertIn("&lt;Trial &amp; test&gt;", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("default/&lt;SQA&gt;", html)
        self.assertIn("&lt;b&gt;log&lt;/b&gt;", html)
        self.assertLess(html.index("Property changes"), html.index("Execution log"))
        job.change_summary["state"] = "planned"
        with patch.object(notify_service, "send_email") as send:
            notify_service.notify_job_outcome("test", job)
        self.assertIn("Planned changes only; upload not confirmed", send.call_args.args[2])
        self.assertNotIn("Uploaded changes", send.call_args.args[2])

    def test_skipped_and_historical_reports_do_not_invent_changes(self):
        for status in (models.JobStatus.SKIPPED_NO_NEWER_VERSION, models.JobStatus.SUCCESS):
            with self.subTest(status=status):
                job = SimpleNamespace(
                    status=status, trigger="scheduled", prod_version=67, sqa_version=None,
                    sqa_property_url=None, created_new_property=False, removed_rules=[],
                    error_summary=None, log="No newer Production version", change_summary=None,
                    profile=SimpleNamespace(notify_emails=["test@example.com"], schedule_cron=None),
                )
                with patch.object(notify_service, "send_email") as send:
                    notify_service.notify_job_outcome("test", job)
                body = send.call_args.args[2]
                self.assertIn("Historical run: no schedule snapshot", body)
                if status == models.JobStatus.SUCCESS:
                    self.assertIn("Change details were not captured", body)
                else:
                    self.assertIn("No property upload or activation performed", body)
                    self.assertIn("Skip reason: No newer Production version", body)

    def test_upload_failure_does_not_report_planned_changes_as_saved(self):
        prod, sqa, job, profile = self.existing_clone()
        sqa.upload_rule_tree.side_effect = PapiError("Upload rejected")
        with self.assertRaisesRegex(PapiError, "Upload rejected"):
            self.run_clone(prod, sqa, job, profile)
        self.assertEqual(job.change_summary["state"], "planned")
        sqa.activate.assert_not_called()

    def test_change_report_includes_recovered_variable_as_an_addition(self):
        prod, sqa, job, profile = self.existing_clone()
        profile.attempt_activation = False
        source = {"rules": {"name": "default", "variables": [
            {"name": "PMUSER_HOST_HEADER", "value": "private-value"},
        ], "behaviors": [], "children": []}}
        with patch.object(clone_service, "fetch_sqa_source_tree", return_value=(source, {"PMUSER_HOST_HEADER": 20})):
            self.run_clone(prod, sqa, job, profile)
        added = [entry for entry in job.change_summary["changes"] if entry["kind"] == "variable"]
        self.assertEqual(added[0]["name"], "PMUSER_HOST_HEADER")
        self.assertEqual(added[0]["action"], "added")
        self.assertNotIn("private-value", str(job.change_summary))
        self.assertEqual(source["rules"]["variables"][0]["value"], "private-value")

    def test_notification_includes_schedule_and_readable_status(self):
        job = SimpleNamespace(
            id=34, status=models.JobStatus.SUCCESS, trigger="scheduled",
            activation_status="ACTIVE", activation_network="PRODUCTION",
            sqa_property_url="https://example.com/property/30", prod_version=68,
            sqa_version=22, created_new_property=False, removed_rules=[],
            error_summary=None, log="Rule tree saved", started_at=None, finished_at=None,
            profile=SimpleNamespace(
                id=1, notify_emails=["test@example.com"], schedule_cron="0 0 * * 0",
                production_property_name="source.example.com", sqa_property_name="target.example.com",
            ),
        )
        with patch.object(notify_service, "send_email") as send:
            notify_service.notify_job_outcome("test", job)
        body = send.call_args.args[2]
        self.assertIn("Status: SUCCESS", body)
        self.assertNotIn("JobStatus.SUCCESS", body)
        self.assertIn("Run type: Scheduled", body)
        self.assertIn("Schedule (current profile): 0 0 * * 0", body)
        self.assertIn("Schedule timezone:", body)
        self.assertIn("Next scheduled check:", body)
        self.assertIn("Errors: None recorded", body)
        self.assertIn("source.example.com", body)
        self.assertIn("target.example.com", body)
        html = send.call_args.kwargs["html_body"]
        metadata_html = html.split('>Property changes</h2>', 1)[0]
        tables = [part.split('</table>', 1)[0] for part in metadata_html.split('<table')[1:]]
        self.assertEqual(len(tables), 3)
        for table in tables:
            self.assertEqual(table.count('<tr>'), 2)
            self.assertIn('border="1"', table)
            self.assertIn('<thead>', table)
            self.assertIn('<tbody>', table)
            self.assertEqual(table.count('scope="col"'), table.count('<td '))
            self.assertNotIn(' | ', table)
        self.assertIn('>Status</th>', tables[0])
        self.assertIn('>SUCCESS</td>', tables[0])
        self.assertIn('>source.example.com</td>', tables[1])
        self.assertIn('>0 0 * * 0</td>', tables[2])
        self.assertNotIn("Georgia", html)
        for title in ("Run details:", "Properties:", "Schedule:"):
            self.assertEqual(sum(line.startswith(title) for line in body.splitlines()), 1)

    def test_notification_identifies_unactivated_draft(self):
        job = SimpleNamespace(
            status="SUCCESS", sqa_property_url="https://example.com/property/30",
            trigger="manual", prod_version=2, sqa_version=1, created_new_property=True,
            removed_rules=[], error_summary=None, log="Saved draft",
            profile=SimpleNamespace(notify_emails=["test@example.com"]),
        )
        with patch.object(notify_service, "send_email") as send:
            notify_service.notify_job_outcome("test", job)
        self.assertIn("manual activation", send.call_args.args[1])
        self.assertIn("not activated", send.call_args.args[2])
        self.assertIn(job.sqa_property_url, send.call_args.args[2])

    def test_notification_reports_activation_outcome(self):
        for status in ("ACTIVE", "FAILED", "TIMED_OUT"):
            with self.subTest(status=status):
                job = SimpleNamespace(
                    status="SUCCESS" if status == "ACTIVE" else "FAILED",
                    activation_status=status, activation_network="PRODUCTION",
                    sqa_property_url="https://example.com/property/30", trigger="manual",
                    prod_version=2, sqa_version=21, created_new_property=False,
                    removed_rules=[], error_summary=None, log="Rule tree saved",
                    profile=SimpleNamespace(notify_emails=["test@example.com"]),
                )
                with patch.object(notify_service, "send_email") as send:
                    notify_service.notify_job_outcome("test", job)
                subject, body = send.call_args.args[1:]
                self.assertNotIn("manual activation", subject)
                self.assertNotIn("not activated", body)
                self.assertIn("activated on PRODUCTION" if status == "ACTIVE" else f"activation status: {status}", body)


class RuleMergeTests(unittest.TestCase):
    def test_change_summary_tracks_actual_edits_without_values(self):
        before = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "private-old.example.com"}},
            {"name": "caching", "options": {"behavior": "NO_STORE"}},
        ], "children": [{"name": "Empty group", "children": []}]}}
        after = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "private-new.example.com"}},
        ], "variables": [{"name": "PMUSER_SECRET", "value": "do-not-email-this", "sensitive": True}], "children": []}}
        original_before, original_after = copy.deepcopy(before), copy.deepcopy(after)
        summary = summarize_rule_changes(before, after)
        entries = {(entry["kind"], entry["name"]): entry for entry in summary["changes"]}
        self.assertEqual(entries["behavior", "origin"]["fields"], ["hostname"])
        self.assertEqual(entries["behavior", "origin"]["action"], "updated")
        self.assertEqual(entries["behavior", "caching"]["action"], "removed")
        self.assertEqual(entries["variable", "PMUSER_SECRET"]["action"], "added")
        self.assertEqual(entries["section", "Empty group"]["action"], "removed")
        self.assertNotIn("do-not-email-this", str(summary))
        self.assertNotIn("private-old.example.com", str(summary))
        self.assertNotIn("private-new.example.com", str(summary))
        self.assertEqual(before, original_before)
        self.assertEqual(after, original_after)
        self.assertEqual(summarize_rule_changes(after, after)["changes"], [])

    def test_removed_conditional_origins_leave_no_empty_sections(self):
        prod_tree = {"rules": {"name": "default", "children": [{
            "name": "Conditional Origin Group",
            "behaviors": [{"name": "allowConditionalOrigins", "options": {}}],
            "children": [{
                "name": "Regional origin", "comments": "Removed regional settings",
                "behaviors": [{"name": "origin", "options": {"hostname": "prod.example.com"}}],
                "criteria": [{"name": "cloudletsOrigin", "options": {"originId": "region"}}],
                "children": [],
            }],
        }]}}
        kept_origins = [{
            "name": name,
            "behaviors": [{"name": "origin", "options": {"hostname": f"{name.lower()}.example.com"}}],
            "criteria": [{"name": "cloudletsOrigin", "options": {"originId": name.lower()}}],
            "children": [],
        } for name in ("SQA", "Prod")]
        sqa_tree = {"rules": {"name": "default", "children": [{
            "name": "Conditional Origin Group", "children": kept_origins,
        }]}}
        policies = {
            "default/Conditional Origin Group/Regional origin::behavior::origin#1": "remove",
            "default/Conditional Origin Group/Regional origin::criterion::cloudletsOrigin#1": "remove",
        }
        original_prod, original_sqa = copy.deepcopy(prod_tree), copy.deepcopy(sqa_tree)
        merged = apply_policies(prod_tree, sqa_tree, policies, {})
        self.assertEqual(merged["rules"]["children"][0]["children"], kept_origins)
        self.assertEqual(prod_tree, original_prod)
        self.assertEqual(sqa_tree, original_sqa)

    def test_empty_sections_are_pruned_recursively_without_removing_remaining_settings(self):
        retained = [
            {"name": "Live parent", "children": [{"name": "Live leaf", "behaviors": [{"name": "caching", "options": {}}]}]},
            {"name": "Variable only", "variables": [{"name": "PMUSER_LOCAL", "value": "kept"}]},
            {"name": "Criteria only", "criteria": [{"name": "hostname", "options": {"values": ["sqa.example.com"]}}]},
            {"name": "Disabled behavior", "behaviors": [{"name": "mtlsOriginKeystore", "options": {"enable": False}}]},
        ]
        tree = {"rules": {"name": "default", "children": [
            {"name": "Empty parent", "comments": "Metadata is not a setting", "children": [{
                "name": "Removed leaf", "behaviors": [{"name": "caching", "options": {}}],
            }]},
            {"name": "Already empty", "criteriaMustSatisfy": "all", "comments": "Empty", "children": []},
            *copy.deepcopy(retained),
        ]}}
        original = copy.deepcopy(tree)
        merged = apply_policies(tree, None, {"default/Empty parent/Removed leaf::behavior::caching#1": "remove"}, {})
        self.assertEqual(merged["rules"]["children"], retained)
        self.assertEqual(tree, original)

    def test_empty_root_is_always_retained(self):
        root = {"name": "default", "behaviors": [], "criteria": [], "variables": [], "children": []}
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                tree = {"rules": root, "ruleFormat": "latest"} if wrapped else root
                self.assertEqual(apply_policies(tree, None, {}, {}), tree)

    def test_variables_are_diffed_and_follow_saved_decisions(self):
        prod_tree = {"rules": {"name": "default", "variables": [
            {"name": "PMUSER_SHARED", "value": "prod", "hidden": False},
            {"name": "PMUSER_REMOVE", "value": "remove"},
        ]}}
        sqa_tree = {"rules": {"name": "default", "variables": [
            {"name": "PMUSER_SHARED", "value": "sqa", "hidden": True},
            {"name": "PMUSER_SQA", "value": "retained", "sensitive": True},
        ]}}
        nodes = diff_rule_sets(prod_tree, sqa_tree)
        variables = {node["name"]: node for node in nodes if node["kind"] == "variable"}
        self.assertEqual(set(variables), {"PMUSER_SHARED", "PMUSER_REMOVE", "PMUSER_SQA"})
        self.assertEqual(variables["PMUSER_SHARED"]["signature"], "default::variable::PMUSER_SHARED#1")
        self.assertEqual(variables["PMUSER_SHARED"]["rule_path"], "default/Variables")
        self.assertTrue(variables["PMUSER_SHARED"]["changed"])
        self.assertEqual(variables["PMUSER_SQA"]["default_policy"], "sqa")
        merged = apply_policies(prod_tree, sqa_tree, {
            "default::variable::PMUSER_SHARED#1": "sqa",
            "default::variable::PMUSER_REMOVE#1": "remove",
        }, {"default::variable::PMUSER_SHARED#1": {"value": "fixed", "hidden": False}})
        self.assertEqual(merged["rules"]["variables"], [
            {"name": "PMUSER_SHARED", "value": "fixed", "hidden": False},
            {"name": "PMUSER_SQA", "value": "retained", "sensitive": True},
        ])

    def test_sqa_variable_declarations_survive_with_selected_sqa_behaviors(self):
        prod_tree = {"rules": {"name": "default", "variables": [
            {"name": "PMUSER_PROD", "value": "prod", "description": "Production variable"},
        ], "behaviors": [{"name": "origin", "options": {"hostname": "prod.example.com"}}]}}
        sqa_tree = {"rules": {"name": "default", "variables": [
            {"name": "PMUSER_HOST_HEADER", "value": "sqa.example.com", "hidden": False, "sensitive": False},
        ], "behaviors": [{"name": "origin", "options": {"customForwardHostHeader": "{{user.PMUSER_HOST_HEADER}}"}}]}}
        original_prod, original_sqa = copy.deepcopy(prod_tree), copy.deepcopy(sqa_tree)
        merged = apply_policies(prod_tree, sqa_tree, {"default::behavior::origin#1": "sqa"}, {})
        self.assertEqual(merged["rules"]["variables"], prod_tree["rules"]["variables"] + sqa_tree["rules"]["variables"])
        self.assertEqual(prod_tree, original_prod)
        self.assertEqual(sqa_tree, original_sqa)

    def test_sqa_only_conditional_origin_rules_are_preserved(self):
        prod_tree = {"rules": {"name": "default", "behaviors": [], "children": []}}
        sqa_tree = {"rules": {"name": "default", "behaviors": [], "children": [{
            "name": "Conditional Origin Group", "behaviors": [], "children": [{
                "name": "SQA origin", "criteriaMustSatisfy": "all",
                "criteria": [{"name": "matchVariable", "options": {"variableName": "PMUSER_REGION", "value": "jp"}}],
                "behaviors": [{"name": "origin", "options": {"hostname": "sqa-origin.example.com"}}],
                "children": [],
            }],
        }]}}
        merged = apply_policies(prod_tree, sqa_tree, {}, {})
        self.assertEqual(merged["rules"]["children"], sqa_tree["rules"]["children"])

    def test_sqa_only_items_in_shared_rules_honor_overrides_and_removals(self):
        prod_tree = {"rules": {"name": "default", "behaviors": [
            {"name": "origin", "options": {"hostname": "prod.example.com"}},
        ], "children": []}}
        sqa_tree = {"rules": {"name": "default", "behaviors": [
            {"name": "setVariable", "options": {"variableName": "PMUSER_SQA", "value": "original"}},
            {"name": "origin", "options": {"hostname": "sqa.example.com"}},
            {"name": "rewriteUrl", "options": {"targetUrl": "/sqa"}},
        ], "criteria": [{"name": "hostname", "options": {"values": ["sqa.example.com"]}}], "children": []}}
        original_prod, original_sqa = copy.deepcopy(prod_tree), copy.deepcopy(sqa_tree)
        merged = apply_policies(prod_tree, sqa_tree, {
            "default::behavior::origin#1": "sqa",
            "default::behavior::rewriteUrl#1": "remove",
        }, {"default::behavior::setVariable#1": {"value": "fixed"}})
        self.assertEqual(merged["rules"]["behaviors"], [
            {"name": "setVariable", "options": {"variableName": "PMUSER_SQA", "value": "fixed"}},
            {"name": "origin", "options": {"hostname": "sqa.example.com"}},
        ])
        self.assertEqual(merged["rules"]["criteria"], sqa_tree["rules"]["criteria"])
        self.assertEqual(prod_tree, original_prod)
        self.assertEqual(sqa_tree, original_sqa)

    def test_sqa_only_rule_order_and_metadata_are_preserved(self):
        shared = {"name": "Fallback", "behaviors": [{"name": "caching", "options": {}}], "children": []}
        sqa_only = {"name": "SQA", "criteriaMustSatisfy": "any", "comments": "SQA origin",
                    "criteria": [{"name": "cloudletsOrigin", "options": {"originId": "sqa"}}],
                    "behaviors": [{"name": "origin", "options": {"hostname": "sqa.example.com"}}], "children": []}
        prod_tree = {"rules": {"name": "default", "children": [copy.deepcopy(shared)]}}
        sqa_tree = {"rules": {"name": "default", "children": [sqa_only, shared]}}
        merged = apply_policies(prod_tree, sqa_tree, {}, {})
        self.assertEqual([rule["name"] for rule in merged["rules"]["children"]], ["SQA", "Fallback"])
        self.assertEqual(merged["rules"]["children"][0], sqa_only)

    def test_repeated_sqa_behaviors_keep_occurrence_policies(self):
        prod_tree = {"rules": {"name": "default", "behaviors": [{"name": "setVariable", "options": {"value": 1}}]}}
        sqa_tree = {"rules": {"name": "default", "behaviors": [
            {"name": "setVariable", "options": {"value": 2}},
            {"name": "setVariable", "options": {"value": 3}},
        ]}}
        merged = apply_policies(prod_tree, sqa_tree, {"default::behavior::setVariable#1": "remove"}, {
            "default::behavior::setVariable#2": {"value": 4},
        })
        self.assertEqual(merged["rules"]["behaviors"], [{"name": "setVariable", "options": {"value": 4}}])

    def test_missing_saved_sqa_choices_block_merging(self):
        prod_tree = {"rules": {"name": "default", "behaviors": [{"name": "origin", "options": {}}]}}
        for signature in ("default::behavior::origin#1", "default/SQA::behavior::origin#1"):
            with self.subTest(signature=signature):
                with self.assertRaisesRegex(ValueError, "Saved SQA choices are missing"):
                    apply_policies(prod_tree, {"rules": {"name": "default", "behaviors": []}}, {signature: "sqa"}, {})


class PropertyLookupTests(unittest.TestCase):
    def setUp(self):
        self.client = PapiClient.__new__(PapiClient)
        self.client._post = MagicMock()
        self.client._post.return_value.json.return_value = {"versions": {"items": []}}
        self.client._get = MagicMock()
        self.match = {
            "propertyName": "draft", "propertyId": "prp_30", "contractId": "ctr_test",
            "groupId": "grp_40", "latestVersion": 3,
        }

    def test_empty_search_falls_back_to_scoped_listing(self):
        self.client._get.return_value = {"properties": {"items": [self.match]}}
        self.assertEqual(self.client.find_property("draft", "test", "key", "40"), self.match)
        self.client._get.assert_called_once_with(
            "/papi/v1/properties", {"accountSwitchKey": "key", "contractId": "test", "groupId": "40"}
        )

    def test_wrong_scope_is_not_selected(self):
        self.client._get.return_value = {
            "properties": {"items": [{**self.match, "groupId": "grp_99"}]}
        }
        self.assertIsNone(self.client.find_property("draft", "test", "key", "40"))

    def test_latest_draft_lookup_omits_activation_filter(self):
        self.client._get.return_value = {"versions": {"items": [{"propertyVersion": 3}]}}
        self.assertEqual(self.client.get_latest_version("prp_30", "test", "40", "key", None), 3)
        self.assertNotIn("activatedOn", self.client._get.call_args.args[1])

    def test_version_details_and_activation_history_are_scoped(self):
        details = {"propertyVersion": 21, "productionStatus": "INACTIVE", "stagingStatus": "INACTIVE"}
        activation = {"propertyVersion": 20, "status": "ACTIVE", "network": "PRODUCTION"}
        self.client._get.side_effect = [
            {"versions": {"items": [details]}}, {"activations": {"items": [activation]}},
        ]
        self.assertEqual(self.client.get_version_details("prp_30", 21, "test", "40", "key"), details)
        self.assertEqual(self.client.get_activations("prp_30", "test", "40", "key"), [activation])
        for call in self.client._get.call_args_list:
            self.assertEqual(call.args[1], {"contractId": "test", "groupId": "40", "accountSwitchKey": "key"})
        self.assertEqual(self.client._get.call_args_list[0].args[0], "/papi/v1/properties/prp_30/versions/21")
        self.assertEqual(self.client._get.call_args_list[1].args[0], "/papi/v1/properties/prp_30/activations")

    def test_activation_request_error_preserves_detail(self):
        self.client._post.side_effect = PapiError("Invalid rule configuration", 400, {"detail": "Invalid rule configuration"})
        result = self.client.activate("prp_30", 21, "test", "40", "key", "PRODUCTION", [])
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.errors, [{"detail": "Invalid rule configuration"}])

    def test_api_error_handles_missing_and_malformed_validation_details(self):
        response = MagicMock(status_code=400)
        response.request.method = "PUT"
        response.request.url = "https://example.test/papi/rules"
        for errors in (None, "invalid", [None, {}, {"detail": 42}]):
            with self.subTest(errors=errors):
                response.json.return_value = {"detail": "Rejected", "errors": errors}
                with self.assertRaises(PapiError) as raised:
                    self.client._check(response)
                self.assertEqual(str(raised.exception), "PAPI PUT https://example.test/papi/rules -> 400: Rejected")
                self.assertEqual(raised.exception.status_code, 400)

    def test_api_error_includes_multiple_validation_locations(self):
        response = MagicMock(status_code=400)
        response.request.method = "PUT"
        response.request.url = "https://example.test/papi/rules"
        response.json.return_value = {
            "detail": "Rejected", "requestId": "request-1",
            "errors": [
                {"errorLocation": "#/rules", "detail": "Invalid rule"},
                {"instance": "#/rules/behaviors/0", "title": "Invalid behavior"},
            ],
        }
        with self.assertRaises(PapiError) as raised:
            self.client._check(response)
        self.assertIn("requestId=request-1", str(raised.exception))
        self.assertIn("#/rules: Invalid rule", str(raised.exception))
        self.assertIn("#/rules/behaviors/0: Invalid behavior", str(raised.exception))

    def test_duplicate_name_has_actionable_error_without_retry(self):
        self.client._post.side_effect = PapiError(
            "duplicate", 400, {"detail": "The name `draft` is already being used."}
        )
        with self.assertRaisesRegex(PapiError, "recently deleted"):
            self.client.create_property("draft", "prd_test", "test", "40", "key")
        self.client._post.assert_called_once()

    def test_lookup_server_error_is_not_treated_as_absent(self):
        self.client.search_switch_keys = MagicMock(return_value=[{"accountSwitchKey": "key"}])
        self.client.find_property = MagicMock(side_effect=PapiError("server failure", 500))
        with self.assertRaisesRegex(PapiError, "server failure"):
            self.client.resolve_property("draft", "test", "account", "internal", "40")


if __name__ == "__main__":
    unittest.main()