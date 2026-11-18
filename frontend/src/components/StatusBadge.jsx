import React from "react";
import { Spinner } from "react-bootstrap";
import { ExternalLink } from "lucide-react";
import { activationVariant, statusLabel, statusVariant, terminalStatuses } from "../utils.js";

export function StatusText({ variant = "secondary", children, live = false, className = "" }) {
  return (
    <span className={`status-text status-${variant} ${className}`}>
      {live
        ? <Spinner animation="border" size="sm" className="status-spinner" aria-hidden="true" />
        : <span className="status-dot" aria-hidden="true" />}
      {children}
    </span>
  );
}

export function StatusBadge({ status, className = "" }) {
  return (
    <StatusText variant={statusVariant[status] || "secondary"} live={!terminalStatuses.has(status)} className={className}>
      {statusLabel[status] || status}
    </StatusText>
  );
}

export function ActivationCell({ job }) {
  if (job.activation_status) {
    const label = job.activation_status.charAt(0) + job.activation_status.slice(1).toLowerCase().replace(/_/g, " ");
    return (
      <StatusText variant={activationVariant[job.activation_status] || "secondary"}>
        {label}
        {job.activation_network && <span className="text-muted fw-normal">· {job.activation_network.toLowerCase()}</span>}
      </StatusText>
    );
  }
  return <span className="text-muted">{job.status === "SUCCESS" && job.sqa_property_url ? "Draft only" : "—"}</span>;
}

export function SqaPortalLink({ url, jobId }) {
  if (!url) return null;
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="portal-link"
      title="Open this property in the SQA Shared Control Center" aria-label={`Open job ${jobId} property in SQA portal (new tab)`}>
      SQA portal <ExternalLink size={13} aria-hidden="true" />
    </a>
  );
}

