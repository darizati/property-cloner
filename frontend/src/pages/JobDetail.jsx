import React, { useEffect, useState } from "react";
import { Alert, Button, Spinner } from "react-bootstrap";
import { Link, useParams } from "react-router-dom";
import { ExternalLink, History as HistoryIcon, Pencil } from "lucide-react";
import { getJob, getProfile } from "../api/client.js";
import { StatusText } from "../components/StatusBadge.jsx";
import { errorMessage, formatDateTime, formatDuration, statusLabel, statusVariant, terminalStatuses } from "../utils.js";

export default function JobDetail() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let timer;
    let mounted = true;

    async function load() {
      try {
        const { data } = await getJob(jobId);
        if (!mounted) return;
        setJob(data);
        if (terminalStatuses.has(data.status) && timer) clearInterval(timer);
      } catch (loadError) {
        if (mounted) setError(errorMessage(loadError));
      }
    }

    timer = setInterval(load, 1500);
    load();
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [jobId]);

  const profileId = job?.profile_id;
  useEffect(() => {
    if (!profileId) return undefined;
    let mounted = true;
    getProfile(profileId).then(({ data }) => mounted && setProfile(data)).catch(() => {});
    return () => { mounted = false; };
  }, [profileId]);

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!job) {
    return (
      <div className="text-center py-5 text-muted">
        <Spinner animation="border" />
        <p className="mt-2">Loading job console…</p>
      </div>
    );
  }

  const active = !terminalStatuses.has(job.status);
  const activated = job.status === "SUCCESS" && job.activation_status === "ACTIVE";
  const savedDraft = job.status === "SUCCESS" && Boolean(job.sqa_property_url) && !activated;
  return (
    <div>
      <div className="page-header">
        <div>
          <nav className="page-breadcrumb"><Link to="/history">Clone history</Link> / Job #{job.id}</nav>
          <h1 className="page-title">Clone job #{job.id}</h1>
          <p className="page-subtitle">
            {profile ? <Link to={`/profiles/${job.profile_id}/edit`}>{profile.name}</Link> : `Profile #${job.profile_id}`}
            {" · "}<span className="text-capitalize">{job.trigger}</span> run
            {" · "}started {formatDateTime(job.started_at)}
          </p>
        </div>
        <StatusText variant={statusVariant[job.status] || "secondary"} live={active} className="job-status">
          {activated ? "Activated" : savedDraft ? "Saved for review" : statusLabel[job.status] || job.status}
        </StatusText>
      </div>

      <div className="metric-grid">
        <Metric label="Production version" value={job.prod_version ?? "—"} />
        <Metric label="SQA version" value={job.sqa_version ?? "—"} />
        <Metric label="Rules removed" value={job.removed_rules?.length || 0} />
        <Metric label="Property created" value={job.created_new_property ? "Yes" : "No"} />
        <Metric label="Duration" value={active ? "Running…" : formatDuration(job.started_at, job.finished_at)} />
      </div>

      {savedDraft && <Alert variant="success">Draft saved. Not activated.</Alert>}
      {activated && <Alert variant="success">Version {job.sqa_version} activated on {job.activation_network}.</Alert>}
      {!activated && job.activation_status && (
        <Alert variant="warning">
          Rule tree saved in version {job.sqa_version}. Activation status: {job.activation_status}
          {job.activation_network && ` on ${job.activation_network}`}.
        </Alert>
      )}

      {job.error_summary && (
        <Alert variant="danger">
          <Alert.Heading as="h2" className="h6">Job error</Alert.Heading>
          <pre className="mb-0" style={{ whiteSpace: "pre-wrap" }}>{job.error_summary}</pre>
        </Alert>
      )}

      {job.removed_rules?.length > 0 && (
        <Alert variant="warning">
          <Alert.Heading as="h2" className="h6">Rules removed during activation retry</Alert.Heading>
          <ul className="mb-0">
            {job.removed_rules.map((rule, index) => (
              <li key={`${rule.location}-${index}`}>
                <code>{rule.location}</code>: {rule.detail || "No detail returned"}
              </li>
            ))}
          </ul>
        </Alert>
      )}

      <div className="job-actions">
        {job.sqa_property_url && (
          <Button as="a" href={job.sqa_property_url} target="_blank" rel="noopener noreferrer" className="btn-icon">
            <ExternalLink size={16} aria-hidden="true" /> Open in SQA portal
          </Button>
        )}
        <Button as={Link} to={`/profiles/${job.profile_id}/edit`} variant="outline-secondary" className="btn-icon">
          <Pencil size={15} aria-hidden="true" /> Edit profile
        </Button>
        <Button as={Link} to={`/history?profile=${job.profile_id}`} variant="outline-secondary" className="btn-icon">
          <HistoryIcon size={15} aria-hidden="true" /> Profile history
        </Button>
      </div>

      <div className="console">
        <div className="console-header">
          <span><span className={`console-dot${active ? " live" : ""}`} /> Console output</span>
          {active && <span className="small">refreshing every 1.5 seconds</span>}
        </div>
        <pre className="console-body">
          {job.log || job.error_summary || "Waiting for the clone worker to start..."}
        </pre>
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}
