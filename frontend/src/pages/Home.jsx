import React, { useEffect, useMemo, useState } from "react";
import { Alert } from "react-bootstrap";
import { Link } from "react-router-dom";
import { ArrowRight, CalendarClock, History as HistoryIcon, ListChecks, PlusCircle } from "lucide-react";
import { listJobs, listProfiles } from "../api/client.js";
import { StatusBadge } from "../components/StatusBadge.jsx";
import { errorMessage, formatRelative } from "../utils.js";

export default function Home() {
  const [profiles, setProfiles] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    Promise.all([listProfiles(), listJobs()])
      .then(([profileResponse, jobResponse]) => {
        if (!mounted) return;
        setProfiles(profileResponse.data);
        setJobs(jobResponse.data);
      })
      .catch((loadError) => mounted && setError(errorMessage(loadError)));
    return () => { mounted = false; };
  }, []);

  const profileNames = useMemo(
    () => Object.fromEntries((profiles || []).map((profile) => [profile.id, profile.name])),
    [profiles],
  );
  const scheduledCount = (profiles || []).filter((profile) => profile.schedule_cron).length;
  const failedCount = (jobs || []).filter((job) => job.status === "FAILED").length;
  const recentJobs = (jobs || []).slice(0, 5);

  const options = [
    {
      to: "/setup",
      icon: PlusCircle,
      title: "New clone setup",
      description: "Pick a production property and an SQA Shared destination, compare rule trees, then save or run.",
      meta: "Start here for a new property",
      primary: true,
    },
    {
      to: "/profiles",
      icon: ListChecks,
      title: "Profiles & schedules",
      description: "Edit saved clone profiles, run them on demand, and manage their cron schedules.",
      meta: profiles ? `${profiles.length} saved · ${scheduledCount} scheduled` : "Loading…",
    },
    {
      to: "/history",
      icon: HistoryIcon,
      title: "Clone history",
      description: "Search and filter past clone jobs, check activation results, and open job consoles.",
      meta: jobs ? `${jobs.length} jobs · ${failedCount} failed` : "Loading…",
    },
  ];

  return (
    <div>
      <header className="home-hero">
        <h1>What would you like to do?</h1>
        <p>Clone production property configurations into SQA Shared, on demand or on a schedule.</p>
      </header>

      {error && <Alert variant="danger">{error}</Alert>}

      <div className="home-options">
        {options.map(({ to, icon: Icon, title, description, meta, primary }) => (
          <Link key={to} to={to} className={`home-option${primary ? " home-option-primary" : ""}`}>
            <span className="home-option-icon"><Icon size={22} aria-hidden="true" /></span>
            <span className="home-option-title">{title}</span>
            <span className="home-option-description">{description}</span>
            <span className="home-option-footer">
              <span>{meta}</span>
              <ArrowRight size={18} aria-hidden="true" />
            </span>
          </Link>
        ))}
      </div>

      {recentJobs.length > 0 && (
        <section className="panel mt-4">
          <div className="panel-header">
            <h2 className="panel-title"><CalendarClock size={18} aria-hidden="true" /> Recent activity</h2>
            <Link to="/history" className="small">View all history</Link>
          </div>
          <ul className="activity-list">
            {recentJobs.map((job) => (
              <li key={job.id}>
                <Link to={`/jobs/${job.id}`} className="activity-item">
                  <span className="activity-id">#{job.id}</span>
                  <span className="activity-name">{profileNames[job.profile_id] || `Profile #${job.profile_id}`}</span>
                  <span className="text-muted small text-capitalize">{job.trigger}</span>
                  <span className="text-muted small activity-time">{formatRelative(job.started_at)}</span>
                  <StatusBadge status={job.status} />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
