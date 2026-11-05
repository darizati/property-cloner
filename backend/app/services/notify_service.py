"""SMTP or Microsoft Graph email notifications for clone job outcomes."""
import logging
import smtplib
import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html import escape
from urllib.parse import quote, urlsplit

import requests
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger(__name__)


def _send_graph_email(to: list[str], subject: str, body: str, html_body: str | None = None) -> bool:
    secret = settings.graph_client_secret.get_secret_value()
    if not all((settings.graph_tenant_id, settings.graph_client_id, secret, settings.graph_sender)):
        logger.error("Graph email requires tenant ID, client ID, client secret, and sender mailbox")
        return False
    stage = "authentication"
    try:
        token_response = requests.post(
            f"https://login.microsoftonline.com/{quote(settings.graph_tenant_id, safe='')}/oauth2/v2.0/token",
            data={
                "client_id": settings.graph_client_id,
                "client_secret": secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=30,
            allow_redirects=False,
        )
        if token_response.status_code != 200:
            logger.error("Graph email authentication failed (HTTP %s)", token_response.status_code)
            return False
        token_payload = token_response.json()
        token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
        if not isinstance(token, str) or not token:
            logger.error("Graph email authentication returned no access token")
            return False
        stage = "submission"
        response = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{quote(settings.graph_sender, safe='')}/sendMail",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML" if html_body else "Text", "content": html_body or body},
                    "toRecipients": [{"emailAddress": {"address": address}} for address in to],
                },
                "saveToSentItems": True,
            },
            timeout=30,
            allow_redirects=False,
        )
        if response.status_code != 202:
            logger.error("Graph email submission failed (HTTP %s)", response.status_code)
            return False
    except (requests.RequestException, ValueError):
        logger.error("Graph email %s failed; check connectivity and configuration", stage)
        return False
    return True


def send_email(to: list[str], subject: str, body: str, html_body: str | None = None) -> bool:
    if not to:
        return True
    if settings.mail_provider == "graph":
        return _send_graph_email(to, subject, body, html_body=html_body)
    if html_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = ", ".join(to)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(settings.smtp_from, to, msg.as_string())
    except (OSError, smtplib.SMTPException):
        logger.exception("Unable to send clone notification email")
        return False
    return True


def capture_run_context(profile) -> dict:
    context = {
        "production_property_name": getattr(profile, "production_property_name", None),
        "sqa_property_name": getattr(profile, "sqa_property_name", None),
        "schedule_cron": getattr(profile, "schedule_cron", None),
    }
    if context["schedule_cron"]:
        try:
            context["schedule_timezone"] = str(CronTrigger.from_crontab(context["schedule_cron"]).timezone)
        except ValueError:
            context["schedule_error"] = "Invalid cron expression"
    return context


def _format_time(value) -> str:
    if value is None:
        return "Not recorded"
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _html_section(title: str, rows: list[tuple]) -> str:
    table_rows = "".join(
        '<tr><th scope="row" style="width:110px;text-align:left;vertical-align:top;'
        'font-weight:bold;padding:6px 8px;border:1px solid #888">'
        f'{escape(str(label))}</th><td style="padding:6px 8px;border:1px solid #888;'
        f'overflow-wrap:anywhere">{escape(str(value)).replace(chr(10), "<br>")}</td></tr>'
        for label, value in rows
    )
    return (
        f'<h2 style="font-size:16px;margin:18px 0 6px">{escape(title)}</h2>'
        f'<table border="1" cellspacing="0" cellpadding="6" style="width:100%;border-collapse:collapse;font-size:13px">{table_rows}</table>'
    )


def _html_columns(title: str, fields: list[tuple]) -> str:
    headers = "".join(
        '<th scope="col" style="text-align:left;vertical-align:top;padding:6px 8px;border:1px solid #888">'
        f'{escape(str(label))}</th>' for label, value in fields
    )
    values = "".join(
        '<td style="vertical-align:top;padding:6px 8px;border:1px solid #888;overflow-wrap:anywhere">'
        f'{escape(str(value)).replace(chr(10), "<br>")}</td>' for label, value in fields
    )
    return (
        f'<h2 style="font-size:16px;margin:18px 0 6px">{escape(title)}</h2>'
        '<table border="1" cellspacing="0" cellpadding="6" style="width:100%;border-collapse:collapse;font-size:12px">'
        f'<thead><tr>{headers}</tr></thead><tbody><tr>{values}</tr></tbody></table>'
    )


def _html_link(url: str, label: str) -> str:
    if urlsplit(url).scheme not in {"http", "https"}:
        return escape(label)
    return f'<a style="color:#222;text-decoration:underline" href="{escape(url, quote=True)}">{escape(label)}</a>'


def notify_job_outcome(profile_name: str, job) -> None:
    """job is a models.CloneJob instance."""
    status = getattr(job.status, "value", job.status)
    activated = getattr(job, "activation_status", None) == "ACTIVE"
    if status == "SUCCESS" and activated:
        subject = f"[property-cloner] {profile_name}: clone activated on {job.activation_network}"
    elif status == "SUCCESS" and job.sqa_property_url:
        subject = f"[property-cloner] {profile_name}: draft saved for manual activation"
    elif status == "SUCCESS":
        subject = f"[property-cloner] {profile_name}: clone succeeded"
    elif status == "PARTIAL_SUCCESS":
        subject = f"[property-cloner] {profile_name}: clone succeeded with rules removed"
    elif status in ("SKIPPED_NO_NEWER_VERSION", "SKIPPED_NO_RULE_CHANGE"):
        subject = f"[property-cloner] {profile_name}: scheduled run skipped ({status})"
    else:
        subject = f"[property-cloner] {profile_name}: clone FAILED"

    run_type = "Scheduled" if job.trigger == "scheduled" else "Manual"
    started = getattr(job, "started_at", None)
    finished = getattr(job, "finished_at", None)
    profile = job.profile
    summary = getattr(job, "change_summary", None) or {}
    context = summary.get("context") or capture_run_context(profile)
    rows = [
        ("Profile", profile_name),
        ("Job", f"#{getattr(job, 'id', None) or 'Not recorded'}"),
        ("Status", status),
        ("Run type", run_type),
        ("Started", _format_time(started)),
        ("Finished", _format_time(finished)),
    ]
    if started and finished:
        if started.tzinfo is None:
            started = started.replace(tzinfo=datetime.UTC)
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=datetime.UTC)
        elapsed = max(0, int((finished - started).total_seconds()))
        rows.append(("Duration", f"{elapsed // 60}m {elapsed % 60}s"))
    property_rows = [
        ("Source property", context.get("production_property_name") or "Not recorded"),
        ("Production version", job.prod_version if job.prod_version is not None else "Not recorded"),
        ("SQA property", context.get("sqa_property_name") or "Not recorded"),
        ("SQA version", job.sqa_version if job.sqa_version is not None else "Not recorded"),
        ("Created new property", "Yes" if job.created_new_property else "No"),
    ]
    schedule_rows = []
    cron = context.get("schedule_cron")
    if cron:
        schedule_rows.append(("Schedule at run" if summary.get("context") else "Schedule (current profile)", cron))
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=context.get("schedule_timezone"))
            next_check = trigger.get_next_fire_time(None, datetime.datetime.now(datetime.UTC))
            schedule_rows.extend([
                ("Schedule timezone", str(trigger.timezone)),
                ("Next scheduled check", next_check.strftime("%a %d %b %Y, %H:%M %Z") + " (calculated)" if next_check else "None"),
            ])
        except ValueError:
            schedule_rows.append(("Schedule", "Invalid cron expression; next check unavailable"))
    else:
        schedule_rows.append(("Schedule", "Not configured at run time" if summary.get("context") else "Not configured"))
    if not summary.get("context"):
        schedule_rows.append(("Schedule context", "Historical run: no schedule snapshot; current profile shown"))

    skipped = status in {"SKIPPED_NO_NEWER_VERSION", "SKIPPED_NO_RULE_CHANGE"}
    if skipped:
        outcome = "Scheduled check skipped. No property upload or activation performed."
    elif activated:
        outcome = f"Version {job.sqa_version} activated on {job.activation_network} in SQA Shared."
    elif getattr(job, "activation_status", None):
        outcome = f"Rule tree saved; activation status: {job.activation_status} on {job.activation_network}."
    elif status == "SUCCESS":
        outcome = "Draft saved; not activated. Review and activate manually."
    else:
        outcome = "Clone failed. Review the error details below."
    if skipped:
        rows.append(("Skip reason", job.log or status))
    errors = job.error_summary or ("Job failed; see execution log" if status == "FAILED" else "None recorded")
    validation = summary.get("validation", {})
    if errors == "None recorded" and validation.get("errors"):
        errors = "No execution failure recorded; rule validation errors reported below"
    error_rows = [("Errors", errors)]
    for kind in ("errors", "warnings"):
        if validation.get(kind):
            details = "\n".join(
                f"{issue.get('location') or 'Rules'}: {issue['detail']}"
                for issue in validation[kind][:40]
            )
            if len(validation[kind]) > 40:
                details += f"\nShowing 40 of {len(validation[kind])} validation issues."
            error_rows.append((f"Rule validation {kind}", details))
    if not validation.get("warnings"):
        error_rows.append(("Warnings", "None recorded" if "validation" in summary else "No validation snapshot captured"))
    if job.removed_rules:
        error_rows.append(("Historical rule removals", "\n".join(
            f"{entry.get('location')}: {entry.get('detail')}" for entry in job.removed_rules
        )))

    changes = summary.get("changes", [])
    if skipped:
        change_note = "No changes applied: this scheduled check was skipped."
    elif "changes" not in summary:
        change_note = "Change details were not captured for this historical run. Future runs include a before/after summary."
    else:
        baseline = summary.get("baseline_version")
        comparison = f"Compared with SQA v{baseline} before upload." if baseline is not None else "New property: no previous SQA rule tree."
        change_note = ("Uploaded changes. " if summary.get("state") == "saved" else "Planned changes only; upload not confirmed. ") + comparison
        counts = summary.get("counts", {})
        change_note += f" Added: {counts.get('added', 0)}; updated: {counts.get('updated', 0)}; removed: {counts.get('removed', 0)}."
        if not changes:
            change_note += " No rule-tree differences."

    sections = [("Run details", rows), ("Properties", property_rows), ("Schedule", schedule_rows)]
    summary_rows = [
        (title, " | ".join(f"{label}: {value}" for label, value in section_rows))
        for title, section_rows in sections
    ]
    lines = ["PROPERTY CLONER REPORT", "", outcome]
    lines.extend(["", *[f"{title}: {details}" for title, details in summary_rows]])
    lines.extend(["", "PROPERTY CHANGES", change_note])
    for entry in changes[:100]:
        lines.append(
            f"{entry['action'].upper()} | {entry['rule_path']} | {entry['kind']}: {entry['name']}"
            + (f" | Fields: {', '.join(entry['fields'])}" if entry.get("fields") else "")
        )
    if len(changes) > 100:
        lines.append(f"Showing 100 of {len(changes)} entries; the full summary is retained in the job record.")
    lines.extend(["", "ERRORS & WARNINGS", *[f"{label}: {value}" for label, value in error_rows]])
    links = []
    if job.sqa_property_url:
        lines.append(f"Review property: {job.sqa_property_url}")
        links.append(_html_link(job.sqa_property_url, "Review SQA property"))
    job_id = getattr(job, "id", None)
    if job_id:
        job_url = f"{settings.frontend_origin.rstrip('/')}/jobs/{job_id}"
        lines.append(f"Job details: {job_url}")
        links.append(_html_link(job_url, f"Open job #{job_id}"))
    log_tail = (job.log or "No execution log recorded.")[-4000:]
    lines.extend(["", "EXECUTION LOG (LAST 4,000 CHARACTERS)", log_tail])

    change_html = (
        '<h2 style="font-size:16px;margin:18px 0 6px">Property changes</h2>'
        f'<p style="margin:6px 0">{escape(change_note)}</p>'
    )
    if changes:
        change_html += '<table border="1" cellspacing="0" cellpadding="6" style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr>'
        for heading in ("Action", "Rule / item", "Fields changed"):
            change_html += f'<th scope="col" style="text-align:left;padding:6px 8px;border:1px solid #888">{heading}</th>'
        change_html += "</tr></thead><tbody>"
        for entry in changes[:100]:
            item = f"{entry['kind']}: {entry['name']}"
            change_html += (
                '<tr><td style="vertical-align:top;padding:6px 8px;border:1px solid #888">'
                f"{escape(entry['action'].capitalize())}</td>"
                '<td style="padding:6px 8px;border:1px solid #888;overflow-wrap:anywhere">'
                f"{escape(entry['rule_path'])}<br><strong>{escape(item)}</strong></td>"
                '<td style="padding:6px 8px;border:1px solid #888;overflow-wrap:anywhere">'
                f"{escape(', '.join(entry.get('fields', []))) or 'Whole item'}</td></tr>"
            )
        change_html += "</tbody></table>"
        if len(changes) > 100:
            change_html += f"<p>Showing 100 of {len(changes)} entries; the full summary is retained in the job record.</p>"
    html_body = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Property Cloner report</title></head>'
        '<body style="margin:0;padding:16px 12px;background:#fff;color:#222;'
        'font:13px/1.45 Verdana,sans-serif">'
        '<div style="max-width:1040px;margin:0 auto">'
        f'<h1 style="font-size:20px;margin:0 0 6px">{escape(profile_name)}</h1>'
        f'<p style="margin:0 0 6px">{escape(outcome)}</p>'
        f'<p style="margin:6px 0">{" &nbsp; | &nbsp; ".join(links)}</p>'
        + "".join(_html_columns(title, fields) for title, fields in sections)
        + change_html + _html_section("Errors & warnings", error_rows)
        + '<h2 style="font-size:16px;margin:18px 0 6px">Execution log</h2>'
        '<p style="margin:6px 0">Last 4,000 characters; full log available in the job details.</p>'
        '<pre style="font:11px/1.6 monospace;white-space:pre-wrap;overflow-wrap:anywhere;'
        f'border-top:1px solid #ddd;padding-top:12px">{escape(log_tail)}</pre>'
        '</div></body></html>'
    )
    send_email(profile.notify_emails, subject, "\n".join(lines), html_body=html_body)
