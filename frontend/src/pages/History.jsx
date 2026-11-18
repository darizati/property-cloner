import React, { useEffect, useMemo, useState } from "react";
import { Alert, Button, Form, Spinner, Table } from "react-bootstrap";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Search, X } from "lucide-react";
import { listJobs, listProfiles } from "../api/client.js";
import { ActivationCell, SqaPortalLink, StatusBadge } from "../components/StatusBadge.jsx";
import { errorMessage, formatDateTime, formatDuration, formatRelative, formatVersions, parseTimestamp, statusLabel } from "../utils.js";

const FILTER_KEYS = ["q", "status", "trigger", "profile", "activation", "range"];
const RANGES = { "24h": 1, "7d": 7, "30d": 30 };

export default function History() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [jobs, setJobs] = useState(null);
  const [profiles, setProfiles] = useState([]);
  const [error, setError] = useState(null);

  const filters = Object.fromEntries(FILTER_KEYS.map((key) => [key, params.get(key) || ""]));

  function setFilter(key, value) {
    setParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    }, { replace: true });
  }

  useEffect(() => {
    listProfiles().then(({ data }) => setProfiles(data)).catch(() => {});
  }, []);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const { data } = await listJobs();
        if (mounted) {
          setJobs(data);
          setError(null);
        }
      } catch (loadError) {
        if (mounted) setError(errorMessage(loadError));
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  const profileById = useMemo(() => Object.fromEntries(profiles.map((profile) => [String(profile.id), profile])), [profiles]);
  const statuses = useMemo(() => [...new Set((jobs || []).map((job) => job.status))].sort(), [jobs]);

  const query = filters.q.trim().toLowerCase();
  const rangeStart = RANGES[filters.range] ? Date.now() - RANGES[filters.range] * 86400000 : null;
  const filteredJobs = (jobs || []).filter((job) => {
    const profile = profileById[String(job.profile_id)];
    if (filters.status && job.status !== filters.status) return false;
    if (filters.trigger && job.trigger !== filters.trigger) return false;
    if (filters.profile && String(job.profile_id) !== filters.profile) return false;
    if (filters.activation === "activated" && job.activation_status !== "ACTIVE") return false;
    if (filters.activation === "not-activated" && job.activation_status === "ACTIVE") return false;
    if (rangeStart && (parseTimestamp(job.started_at)?.getTime() ?? 0) < rangeStart) return false;
    if (!query) return true;
    return [
      `#${job.id}`, String(job.id), job.status, statusLabel[job.status], job.trigger, job.activation_status,
      profile?.name, profile?.production_property_name, profile?.sqa_property_name, job.error_summary,
    ].some((text) => text?.toLowerCase().includes(query));
  });
  const activeFilterCount = FILTER_KEYS.filter((key) => filters[key]).length;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Clone history</h1>
          <p className="page-subtitle">Every clone job, newest first. Updates automatically.</p>
        </div>
      </div>

      {error && <Alert variant="danger">{error}</Alert>}

      <div className="filter-bar">
        <div className="search-input filter-search">
          <Search size={15} aria-hidden="true" />
          <Form.Control type="search" placeholder="Search job #, profile, property, error…" aria-label="Search jobs"
            value={filters.q} onChange={(event) => setFilter("q", event.target.value)} />
        </div>
        <Form.Select aria-label="Filter by profile" value={filters.profile} onChange={(event) => setFilter("profile", event.target.value)}>
          <option value="">All profiles</option>
          {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
          {filters.profile && !profileById[filters.profile] && <option value={filters.profile}>Profile #{filters.profile}</option>}
        </Form.Select>
        <Form.Select aria-label="Filter by status" value={filters.status} onChange={(event) => setFilter("status", event.target.value)}>
          <option value="">All statuses</option>
          {statuses.map((status) => <option key={status} value={status}>{statusLabel[status] || status}</option>)}
        </Form.Select>
        <Form.Select aria-label="Filter by trigger" value={filters.trigger} onChange={(event) => setFilter("trigger", event.target.value)}>
          <option value="">Any trigger</option>
          <option value="manual">Manual</option>
          <option value="scheduled">Scheduled</option>
        </Form.Select>
        <Form.Select aria-label="Filter by activation" value={filters.activation} onChange={(event) => setFilter("activation", event.target.value)}>
          <option value="">Any activation</option>
          <option value="activated">Activated</option>
          <option value="not-activated">Not activated</option>
        </Form.Select>
        <Form.Select aria-label="Filter by date" value={filters.range} onChange={(event) => setFilter("range", event.target.value)}>
          <option value="">All time</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </Form.Select>
        {activeFilterCount > 0 && (
          <Button variant="link" size="sm" className="btn-icon text-decoration-none" onClick={() => setParams({}, { replace: true })}>
            <X size={15} aria-hidden="true" /> Clear
          </Button>
        )}
      </div>

      <div className="panel">
        <div className="panel-header">
          <span className="text-muted small">
            {jobs ? `${filteredJobs.length} of ${jobs.length} jobs` : "Loading…"}
          </span>
        </div>
        {!jobs ? (
          <div className="panel-body text-muted"><Spinner animation="border" size="sm" className="me-2" />Loading jobs…</div>
        ) : filteredJobs.length === 0 ? (
          <div className="empty-state m-3">{jobs.length ? "No jobs match these filters." : "No clone jobs have run yet."}</div>
        ) : (
          <Table hover responsive className="data-table mb-0">
            <thead>
              <tr>
                <th>Job</th>
                <th>Profile</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Status</th>
                <th>SQA activation</th>
                <th>Prod → SQA</th>
                <th title="Rules removed">Removed</th>
                <th className="text-end">Link</th>
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map((job) => {
                const profile = profileById[String(job.profile_id)];
                return (
                  <tr key={job.id} className="clickable-row" onClick={(event) => {
                    if (!event.target.closest("a, button")) navigate(`/jobs/${job.id}`);
                  }}>
                    <td><Link to={`/jobs/${job.id}`} className="fw-semibold">#{job.id}</Link></td>
                    <td>
                      <div className="fw-medium">{profile?.name || `Profile #${job.profile_id}`}</div>
                      {profile && <div className="text-muted small text-truncate" style={{ maxWidth: 260 }}>{profile.production_property_name}</div>}
                    </td>
                    <td title={formatDateTime(job.started_at)} className="text-nowrap">
                      <div>{formatRelative(job.started_at)}</div>
                      <div className="text-muted small text-capitalize">{job.trigger}</div>
                    </td>
                    <td className="text-nowrap">{formatDuration(job.started_at, job.finished_at)}</td>
                    <td><StatusBadge status={job.status} /></td>
                    <td><ActivationCell job={job} /></td>
                    <td className="text-nowrap">{formatVersions(job)}</td>
                    <td>{job.removed_rules?.length || 0}</td>
                    <td className="text-end text-nowrap">
                      <SqaPortalLink url={job.sqa_property_url} jobId={job.id} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </div>
    </div>
  );
}
