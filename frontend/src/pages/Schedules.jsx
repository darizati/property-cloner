import React, { useEffect, useMemo, useState } from "react";
import { Alert, Button, Dropdown, Form, Modal, Spinner } from "react-bootstrap";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight, CalendarClock, ChevronDown, History as HistoryIcon, MoreHorizontal, Pencil, Play, Plus, Search, Trash2,
} from "lucide-react";
import { deleteProfile, listJobs, listProfiles, runProfile, updateSchedule } from "../api/client.js";
import { ActivationCell, SqaPortalLink, StatusBadge } from "../components/StatusBadge.jsx";
import { errorMessage, formatDateTime, formatRelative, formatVersions } from "../utils.js";

const RECENT_LIMIT = 5;

export default function Schedules() {
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState(null);
  const [jobs, setJobs] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);
  const [openHistory, setOpenHistory] = useState(new Set());
  const [search, setSearch] = useState("");
  const [runningId, setRunningId] = useState(null);

  async function reload() {
    try {
      const [profileResponse, jobResponse] = await Promise.all([listProfiles(), listJobs()]);
      setProfiles(profileResponse.data);
      setJobs(jobResponse.data);
    } catch (loadError) {
      setError(errorMessage(loadError));
    }
  }

  useEffect(() => {
    reload();
  }, []);

  const jobsByProfile = useMemo(() => {
    const grouped = {};
    jobs.forEach((job) => {
      (grouped[job.profile_id] ||= []).push(job);
    });
    return grouped;
  }, [jobs]);

  const query = search.trim().toLowerCase();
  const visibleProfiles = (profiles || []).filter((profile) => !query || [
    profile.name, profile.production_property_name, profile.sqa_property_name, profile.schedule_cron,
  ].some((text) => text?.toLowerCase().includes(query)));

  async function handleSave(profile) {
    try {
      await updateSchedule(profile.id, (drafts[profile.id] ?? "").trim() || null);
      setMessage(`Updated schedule for '${profile.name}'.`);
      setError(null);
      setDrafts((previous) => {
        const next = { ...previous };
        delete next[profile.id];
        return next;
      });
      reload();
    } catch (saveError) {
      setError(errorMessage(saveError));
    }
  }

  async function handleRunNow(id) {
    setRunningId(id);
    try {
      const { data } = await runProfile(id);
      navigate(`/jobs/${data.id}`);
    } catch (runError) {
      setError(errorMessage(runError));
      setRunningId(null);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      await deleteProfile(pendingDelete.id);
      setMessage(`Deleted profile '${pendingDelete.name}'.`);
      setError(null);
      reload();
    } catch (deleteError) {
      setError(errorMessage(deleteError));
    } finally {
      setPendingDelete(null);
    }
  }

  function toggleHistory(id) {
    setOpenHistory((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1 className="page-title">Profiles &amp; schedules</h1>
          <p className="page-subtitle">Saved clone configurations, their schedules, and recent runs.</p>
        </div>
        <Button as={Link} to="/setup" className="btn-icon"><Plus size={16} aria-hidden="true" /> New profile</Button>
      </div>

      {message && <Alert variant="info" dismissible onClose={() => setMessage(null)}>{message}</Alert>}
      {error && <Alert variant="danger" dismissible onClose={() => setError(null)}>{error}</Alert>}

      {profiles && profiles.length > 0 && (
        <div className="filter-bar">
          <div className="search-input">
            <Search size={15} aria-hidden="true" />
            <Form.Control type="search" placeholder="Search profiles or properties…" aria-label="Search profiles"
              value={search} onChange={(event) => setSearch(event.target.value)} />
          </div>
          <span className="text-muted small ms-auto">{visibleProfiles.length} of {profiles.length} profiles</span>
        </div>
      )}

      {!profiles && <div className="panel panel-body text-muted"><Spinner animation="border" size="sm" className="me-2" />Loading profiles…</div>}
      {profiles && profiles.length === 0 && (
        <div className="empty-state">
          <p className="mb-2">No saved profiles yet.</p>
          <Button as={Link} to="/setup" size="sm">Create your first profile</Button>
        </div>
      )}
      {profiles && profiles.length > 0 && visibleProfiles.length === 0 && <div className="empty-state">No profiles match “{search}”.</div>}

      <div className="profile-list">
        {visibleProfiles.map((profile) => {
          const profileJobs = jobsByProfile[profile.id] || [];
          const lastJob = profileJobs[0];
          const historyOpen = openHistory.has(profile.id);
          const draft = drafts[profile.id];
          const dirty = draft !== undefined && draft.trim() !== (profile.schedule_cron || "");
          return (
            <article className="panel profile-card" key={profile.id}>
              <div className="profile-card-main">
                <div className="profile-card-info">
                  <div className="profile-card-title">
                    <Link to={`/profiles/${profile.id}/edit`}>{profile.name}</Link>
                    {profile.schedule_cron
                      ? <span className="chip chip-info"><CalendarClock size={13} aria-hidden="true" /> Scheduled</span>
                      : <span className="chip">Manual</span>}
                  </div>
                  <div className="profile-card-route">
                    <span title="Production source">{profile.production_property_name}</span>
                    <ArrowRight size={14} aria-hidden="true" />
                    <span title="SQA Shared destination">{profile.sqa_property_name}</span>
                  </div>
                  <div className="profile-card-meta">
                    <span>Last cloned prod v{profile.last_known_prod_version ?? "—"}</span>
                    {lastJob
                      ? <span>Last run {formatRelative(lastJob.started_at)} <StatusBadge status={lastJob.status} /></span>
                      : <span>Never run</span>}
                  </div>
                </div>

                <div className="profile-card-schedule">
                  <Form.Label htmlFor={`cron-${profile.id}`} className="small text-muted mb-1">Cron schedule</Form.Label>
                  <div className="d-flex gap-2">
                    <Form.Control
                      id={`cron-${profile.id}`}
                      size="sm"
                      placeholder="e.g. 0 6 * * *"
                      value={draft ?? profile.schedule_cron ?? ""}
                      onChange={(event) => setDrafts((previous) => ({ ...previous, [profile.id]: event.target.value }))}
                    />
                    <Button size="sm" variant={dirty ? "primary" : "outline-secondary"} disabled={!dirty} onClick={() => handleSave(profile)}>Save</Button>
                  </div>
                </div>

                <div className="profile-card-actions">
                  <Button size="sm" className="btn-icon" disabled={runningId === profile.id} onClick={() => handleRunNow(profile.id)}>
                    {runningId === profile.id ? <Spinner animation="border" size="sm" aria-hidden="true" /> : <Play size={15} aria-hidden="true" />} Run now
                  </Button>
                  <Button size="sm" variant="outline-secondary" className="btn-icon" as={Link} to={`/profiles/${profile.id}/edit`}>
                    <Pencil size={14} aria-hidden="true" /> Edit
                  </Button>
                  <Dropdown align="end">
                    <Dropdown.Toggle size="sm" variant="outline-secondary" className="btn-icon no-caret" aria-label={`More actions for ${profile.name}`}>
                      <MoreHorizontal size={16} aria-hidden="true" />
                    </Dropdown.Toggle>
                    <Dropdown.Menu>
                      <Dropdown.Item as={Link} to={`/history?profile=${profile.id}`}><HistoryIcon size={14} aria-hidden="true" className="me-2" />View full history</Dropdown.Item>
                      <Dropdown.Divider />
                      <Dropdown.Item className="text-danger" onClick={() => setPendingDelete(profile)}>
                        <Trash2 size={14} aria-hidden="true" className="me-2" />Delete profile
                      </Dropdown.Item>
                    </Dropdown.Menu>
                  </Dropdown>
                </div>
              </div>

              <button type="button" className="profile-history-toggle" aria-expanded={historyOpen} onClick={() => toggleHistory(profile.id)}>
                <HistoryIcon size={15} aria-hidden="true" />
                Run history <span className="chip">{profileJobs.length}</span>
                <ChevronDown size={16} aria-hidden="true" className={`ms-auto toggle-chevron${historyOpen ? " open" : ""}`} />
              </button>

              {historyOpen && (
                <div className="profile-history">
                  {profileJobs.length === 0 ? (
                    <p className="text-muted small mb-0">This profile has not been run yet.</p>
                  ) : (
                    <>
                      <table className="table table-sm mini-table mb-2">
                        <thead>
                          <tr><th>Job</th><th>Started</th><th>Trigger</th><th>Status</th><th>Activation</th><th>Prod → SQA</th><th className="text-end">Link</th></tr>
                        </thead>
                        <tbody>
                          {profileJobs.slice(0, RECENT_LIMIT).map((job) => (
                            <tr key={job.id}>
                              <td><Link to={`/jobs/${job.id}`}>#{job.id}</Link></td>
                              <td title={formatDateTime(job.started_at)}>{formatRelative(job.started_at)}</td>
                              <td className="text-capitalize">{job.trigger}</td>
                              <td><StatusBadge status={job.status} /></td>
                              <td><ActivationCell job={job} /></td>
                              <td>{formatVersions(job)}</td>
                              <td className="text-end">
                                <SqaPortalLink url={job.sqa_property_url} jobId={job.id} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <Link to={`/history?profile=${profile.id}`} className="small">
                        {profileJobs.length > RECENT_LIMIT ? `View all ${profileJobs.length} runs` : "Open in clone history"} →
                      </Link>
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>

      <Modal show={Boolean(pendingDelete)} onHide={() => setPendingDelete(null)} centered>
        <Modal.Header closeButton>
          <Modal.Title>Delete profile?</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          Delete <strong>{pendingDelete?.name}</strong>, its saved schedule, and its stored job history?
          This cannot be undone.
        </Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => setPendingDelete(null)}>Cancel</Button>
          <Button variant="danger" onClick={confirmDelete}>Delete profile</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
