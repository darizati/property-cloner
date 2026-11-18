export const terminalStatuses = new Set([
  "SUCCESS",
  "PARTIAL_SUCCESS",
  "FAILED",
  "SKIPPED_NO_NEWER_VERSION",
  "SKIPPED_NO_RULE_CHANGE",
]);

export const statusVariant = {
  SUCCESS: "success",
  PARTIAL_SUCCESS: "warning",
  FAILED: "danger",
  RUNNING: "info",
  PENDING: "secondary",
  SKIPPED_NO_NEWER_VERSION: "secondary",
  SKIPPED_NO_RULE_CHANGE: "secondary",
};

export const statusLabel = {
  SUCCESS: "Success",
  PARTIAL_SUCCESS: "Partial success",
  FAILED: "Failed",
  RUNNING: "Running",
  PENDING: "Pending",
  SKIPPED_NO_NEWER_VERSION: "Skipped · no newer version",
  SKIPPED_NO_RULE_CHANGE: "Skipped · no rule change",
};

export const activationVariant = {
  ACTIVE: "success",
  FAILED: "danger",
  ERROR: "danger",
  ABORTED: "warning",
  PENDING: "info",
  IN_PROGRESS: "info",
};

export function errorMessage(error) {
  return error?.response?.data?.detail || error?.message || String(error);
}

// The API returns naive UTC timestamps; treat them as UTC rather than local time.
export function parseTimestamp(value) {
  if (!value) return null;
  const hasZone = /[zZ]|[+-]\d\d:?\d\d$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
  const date = parseTimestamp(value);
  return date ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function formatRelative(value) {
  const date = parseTimestamp(value);
  if (!date) return "—";
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const units = [["day", 86400], ["hour", 3600], ["minute", 60]];
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

export function formatVersions(job) {
  if (job.prod_version == null && job.sqa_version == null) return "—";
  return `v${job.prod_version ?? "—"} → v${job.sqa_version ?? "—"}`;
}

export function formatDuration(start, end) {
  const from = parseTimestamp(start);
  const to = parseTimestamp(end);
  if (!from || !to) return "—";
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
