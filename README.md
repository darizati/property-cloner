# Property Cloner

Standalone tool for cloning Akamai properties from Production into the SQA Shared
network, with per-rule source selection, fixed-value overrides, scheduled re-clones,
and email notification. Editable drafts are reused; activation is opt-in for existing
properties with hostnames and successful activation history.

Split out of `prod-check-mate-ab` because that repo bundles many unrelated QA tools;
this project is scoped to just the property-cloning workflow and its own UI.

## Stack (v1 — lab-machine friendly, designed to scale later)

- **Backend**: FastAPI + SQLAlchemy + SQLite (swap to Postgres later by changing
  `DATABASE_URL` — no code changes needed, see `app/db.py`).
- **Scheduler**: APScheduler running in-process (`app/services/scheduler_service.py`
  is a small abstraction — swap to Celery beat later without touching business logic
  in `clone_service.py`).
- **Frontend**: React + Vite + react-bootstrap + react-router, mirroring the patterns
  used in `prod-check-mate-ab/react-frontend`.
- **Auth**: Akamai EdgeGrid via `.edgerc` (same as `prod-check-mate-ab`).

This intentionally avoids Docker, Postgres, Redis, and Dbattery/TLS-cert provisioning
so it runs immediately on a single lab machine. When adoption grows, the natural
upgrade path is: SQLite → Postgres (`DATABASE_URL` env var), APScheduler → Celery beat
(implement `SchedulerBackend` with a Celery-backed class), single-process → containerized
deployment (add a Dockerfile/compose once needed).

## Why no per-behavior handler files (unlike prod-check-mate-ab)

The old tool has ~20 hardcoded `handlers/handler_<behavior>.py` files, one per
Akamai behavior that needs environment-specific treatment. This tool replaces that
with a generic **policy engine** (`app/papi/rule_tree.py`): every behavior/criterion
in the rule tree gets a stable signature (`ruleNamePath::behavior::name#occurrence`),
and the user assigns each signature a policy of `prod` (clone from production),
`sqa` (keep the existing SQA value), or `remove`, plus optional fixed field-value
overrides (e.g. force a Cloud Wrapper config ID or a Cloudlet policy ID to a specific
SQA value regardless of source). This covers arbitrary behaviors without new code
per behavior type.

## Setup

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit EDGERC_PATH / SMTP settings
uvicorn app.main:app --reload --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

## Lab service

The lab deployment is available at `http://198.18.110.91:8088`. A production
frontend build and the API are served by one Uvicorn process. API requests use
the browser's origin; Vite proxies `/api` to localhost:8000 during development.
Browser routes such as `/profiles/1/edit` also work when opened directly.

The boot-enabled systemd unit is [deploy/property-cloner.service](deploy/property-cloner.service).
It uses a dedicated `property-cloner` account, restarts on failure, and runs one
worker so scheduled jobs are not registered by multiple processes.

- Application and built frontend: `/opt/property-cloner`
- Virtual environment: `/opt/property-cloner/backend/.venv`
- Persistent database: `/var/lib/property-cloner/property_cloner.db`
- Private environment: `/etc/property-cloner/property-cloner.env`
- Private EdgeGrid credentials: `/etc/property-cloner/.edgerc`

The environment file supplies `DATABASE_URL`, `EDGERC_PATH`, `FRONTEND_ORIGIN`,
`TZ`, and the approved mail settings. `TZ` preserves the timezone of the original
profile's scheduler; cron fields are not rewritten. Private files are readable
only by root and the service account. Profiles, notification addresses, job
history, and credentials are deployed separately and never committed.

To inspect or restart the service on the lab host:

```bash
systemctl status property-cloner
journalctl -u property-cloner -n 100 --no-pager
systemctl restart property-cloner
systemctl is-enabled property-cloner
curl --fail http://127.0.0.1:8088/api/health
```

For code updates, build with `npm ci && npm run build` in `frontend/`, copy the
source and generated `frontend/dist/` into `/opt/property-cloner` without replacing
the private configuration or database, install backend requirements into its
virtual environment, and restart the service only when no jobs are running.
Do not run a second scheduler against a copied active profile. Back up the live
SQLite database using SQLite's backup API rather than copying an open database.

This is an unauthenticated internal lab tool with property-write capabilities.
Keep its port on the trusted lab network; do not expose it to the public Internet.

## Core flow

1. **Setup wizard** (`POST /api/properties/fetch-diff`): fetches the production
   rule tree and, if the SQA property already exists, its current rule tree too.
   Every behavior/criterion is diffed and returned with a suggested default policy
   (`prod` if only in production, `sqa` if it's an SQA-only customization, flagged
   `changed` if it exists in both but differs).
2. User adjusts policies per rule + adds fixed-value overrides, saves a
   **CloneProfile**, optionally with a cron schedule. **Attempt activation** is an
   unchecked-by-default checkbox beside Save and Save & Run. It is saved with the
   profile and applies to manual and scheduled runs. Save alone never starts a job
   or activates a property.
3. **Run** (`POST /api/profiles/{id}/run`): merges the trees per policy, then either
   creates a brand-new SQA property (first clone), updates the latest editable draft,
   or creates a new version when the latest version has already been activated.
   The configured policies and fixed overrides are applied before uploading rules.
   If PAPI rejects the upload, the job reports any returned validation details.
   When PAPI identifies an option's expected values, the job identifies the
   feature, location, and expected type so you can select an SQA value or set an override.
   A property or version may already have been created; review it before retrying.
4. **Activation**: when **Attempt activation** is checked, existing properties with
   hostnames on the target version and successful activation history are activated
   after saving. Unchecked runs only save a draft. The target network is
   PRODUCTION within the SQA Shared account if previously used, otherwise STAGING
   for staging-only properties. New properties, hostname-free properties, and
   properties never successfully activated remain drafts for manual setup.
   Job details and email report the actual activation outcome and include an
   **Open SQA Property** link. Failed activation leaves the saved rule tree intact;
   retrying reuses the same editable draft. Rules are never removed automatically.
   A pending activation blocks another run until it finishes, avoiding duplicate
   activation requests and edits to in-flight versions.
   `PROPERTY_MANAGER_URL` sets the portal base URL (defaults to SQA Shared).
5. **Scheduled re-clone** (`scheduler_service` + `clone_service.rerun_scheduled`):
   on each scheduled tick, checks the production property's latest active version
   against the profile's cached `last_known_prod_version`. No newer version ->
   skip. Newer version -> diff scoped to the profile's selected rule signatures;
   no relevant change -> skip; otherwise re-run the clone.
6. **Hostnames**: new SQA properties are created without hostname assignments.
   Cloning does not create edge hostnames or submit hostname updates. Existing
   SQA properties retain their draft hostnames or those inherited by a new version. Assign
   hostnames separately when needed; saved edge-hostname settings are retained
   for compatibility but are no longer used by cloning.

Existing SQA properties are looked up by exact name, contract, and configured
group, with a scoped property-list fallback when version search returns no
match. Re-clones and wizard comparisons use the latest saved SQA version,
including versions that have never been activated.

Editing or refreshing a comparison preserves existing policy decisions and fixed
overrides, using defaults only for newly encountered items. Decisions for absent
items are retained and listed separately. The editor and confirmation identify the
Production source and SQA Shared destination, including the target group. Saving
and running are blocked while loading or when property settings no longer match
the fetched comparison.

Rule merging preserves SQA-only subtrees, behaviors, and criteria, including
conditional-origin rules and their matching criteria. SQA-only items default to
SQA unless explicitly removed; fixed overrides still apply. If a saved SQA choice
is missing from the SQA source, cloning fails before creating a version or
uploading rules rather than silently dropping it or substituting Production.

After applying decisions, empty rule sections are removed recursively, including
parent groups left empty by removed descendants. Sections with remaining variables,
behaviors, criteria, or nonempty children are preserved; names and comments alone
do not keep a section alive. The required root rule is always retained. This avoids
uploading empty conditional-origin containers after removing their settings.

Variable declarations appear under **default/Variables** in the comparison editor.
Each variable supports Production/SQA source selection, removal, and fixed field
overrides for its value, description, and flags. These choices use the profile's
existing policies and overrides, so Save and reopening retain them. Variable names
are stable identifiers and cannot be renamed through field overrides.

If the latest SQA draft is missing declarations available on its active version,
the editor offers those declarations with their active source version labeled.
Cloning uses the same source-loading logic, preserves existing draft declarations,
and respects explicit removal. This does not revert other draft settings or copy
active rules over the user's selected behavior/criterion policies.

If creation reports that a name is already in use but lookup finds no accessible
property, check the group and deletion status in Property Manager. A recently
deleted property may still reserve its name. Use a different name for a fresh
clone or resolve the reservation before retrying; the tool does not rename
profiles or retry creation automatically.

## Credentials

Copy your `.edgerc` (with `[production]` and `[shared]` sections) to
`backend/.edgerc` — it's gitignored, and `EDGERC_PATH` defaults to `./.edgerc`
(resolved against the `backend/` directory regardless of cwd), so nothing needs
to live in your home directory for this tool.

### Email without committing personal settings

Keep actual SMTP settings in `backend/.env`, which is already excluded by
`.gitignore`. If you already have this file, edit it rather than copying over it.
Only the placeholder `backend/.env.example` belongs in Git.

For a relay supporting STARTTLS, use its supplied settings, for example:

```dotenv
MAIL_PROVIDER=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=your-smtp-username
SMTP_PASSWORD="your-smtp-password"
SMTP_FROM=your-approved-sender@example.com
```

Use your organization's approved relay hostname, port, authentication method, and
sender address. Leave username and password empty if the relay does not require
authentication; a corporate relay may require VPN access. `SMTP_USE_TLS=true`
enables STARTTLS, not implicit TLS on port 465. The default `localhost:25` only
works if a local SMTP server is running.

Set recipients in each profile's **Notify emails (comma separated)** field. These are stored
in the local SQLite database, not in source files; `*.db` is already ignored.
Keep database backups, exported profiles, and any custom database location out of
Git too. Never put real settings in `.env.example` or commit an actual `.env`.
Ignoring a file does not protect it if it was already tracked; remove it from
tracking and rotate any credentials previously committed.

Restart the backend after changing SMTP settings. Deployment environments can
instead inject the same environment variables from their secret manager; process
environment variables take precedence over `.env`. Email delivery failure does
not undo a successful clone or activation.

#### Microsoft Graph (Entra)

For application-based sending instead of SMTP, set these in your existing ignored
`backend/.env`. Enter the secret directly in the editor, not in chat or shell
commands; use its **value**, not its secret ID.

```dotenv
MAIL_PROVIDER=graph
GRAPH_TENANT_ID=your-directory-tenant-id
GRAPH_CLIENT_ID=your-application-client-id
GRAPH_CLIENT_SECRET="your-client-secret-value"
GRAPH_SENDER=your-authorized-mailbox@example.com
```

This uses client-credentials authentication and the Microsoft Graph **application**
`Mail.Send` permission with administrator consent (delegated permissions alone do
not work). The sender must be a mailbox the application is authorized to use;
scope the application's Exchange access to that mailbox where possible. Profile
notification recipients remain unchanged. Graph mode does not use or fall back to
SMTP, and credentials and access tokens are not logged.

Restart the backend after configuring it. To send one test message without
cloning or activating anything, run from `backend/`, replacing only the recipient:

```bash
.venv/bin/python -c 'from app.services.notify_service import send_email; accepted = send_email(["recipient@example.com"], "Property Cloner email test", "Email-only test. No clone or activation was run."); print("Accepted for sending" if accepted else "Email submission failed"); raise SystemExit(0 if accepted else 1)'
```

A successful Graph response means **accepted for sending**, not confirmed inbox
delivery. If rejected, verify secret expiry, application permission/admin consent,
sender mailbox access, and tenant policy. Requests have a timeout and are not
automatically retried, avoiding duplicate messages after ambiguous failures.

### Notification reports

Outcome notifications are monochrome HTML reports with a plain-text alternative
when using SMTP. They separate run details, property versions, schedule, changes,
errors and validation warnings, and the execution-log tail. Links open the SQA
property and local job details; local links are only accessible where this app runs.

Each new run records whether it was manual or scheduled, its cron expression and
timezone at run time, and its source/target identities. The next check is calculated
from that schedule and labeled as such. Skipped checks show their reason and do not
claim an upload or activation happened. Early scheduled lookup failures are recorded
and notified too.

Clones capture the actual previous SQA rule tree versus the merged upload, including
recovered variable declarations, added/updated/removed settings, pruned sections,
and rule ordering. Reports show item names and changed field names, not option or
variable values. Changes are marked planned until upload succeeds, then uploaded,
even if activation later fails. Upload validation warnings/errors remain distinct
from execution failures. Email lists at most 100 change entries and 40 issues per
validation category; full summaries are retained with the job record.

Historical jobs without captured changes or schedule snapshots are labeled as
such; reports do not reconstruct or invent their original changes. An additive,
repeatable database update preserves existing job history.

## Known limitations / next steps

- Rule matching between prod/SQA trees is by structural signature (rule-name path +
  behavior name + occurrence index), not a semantic diff — if the two trees'
  structures diverge significantly (extra/reordered rules), signatures may not
  line up. Good enough for properties cloned from the same template; revisit if
  that assumption breaks down.
- No multi-user auth yet — add before exposing beyond your own lab machine.
- Tables are created on startup, with additive updates for the job review-link
   and activation-result columns in existing databases; introduce Alembic for broader schema changes.
- Email supports SMTP (`smtplib`) and Microsoft Graph application authentication;
   delivery tracking and automatic retries are not implemented.
