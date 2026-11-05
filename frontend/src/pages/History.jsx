import React, { useEffect, useState } from "react";
import { Alert, Badge, Button, Table } from "react-bootstrap";
import { Link } from "react-router-dom";
import { listJobs } from "../api/client.js";

const statusVariant = {
  SUCCESS: "success",
  PARTIAL_SUCCESS: "warning",
  FAILED: "danger",
  RUNNING: "info",
  PENDING: "secondary",
  SKIPPED_NO_NEWER_VERSION: "secondary",
  SKIPPED_NO_RULE_CHANGE: "secondary",
};

const activationVariant = {
  ACTIVE: "success",
  FAILED: "danger",
  ERROR: "danger",
  ABORTED: "warning",
  PENDING: "info",
  IN_PROGRESS: "info",
};

export default function History() {
  const [jobs, setJobs] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const { data } = await listJobs();
        if (mounted) setJobs(data);
      } catch (loadError) {
        if (mounted) setError(loadError?.response?.data?.detail || loadError.message);
      }
    }
    load();
    const timer = setInterval(load, 2000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <div>
      <h3>Clone History</h3>
      {error && <Alert variant="danger">{error}</Alert>}
      <Table striped bordered hover responsive size="sm">
        <thead>
          <tr>
            <th>Job</th>
            <th>Profile</th>
            <th>Trigger</th>
            <th>Status</th>
            <th>SQA activation</th>
            <th>Prod v</th>
            <th>SQA v</th>
            <th>Removed rules</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><Button as={Link} to={`/jobs/${j.id}`} variant="link" className="p-0">#{j.id}</Button></td>
              <td>{j.profile_id}</td>
              <td>{j.trigger}</td>
              <td>
                <Badge bg={statusVariant[j.status] || "secondary"}>{j.status}</Badge>
              </td>
              <td>
                {j.activation_status ? (
                  <>
                    <Badge bg={activationVariant[j.activation_status] || "secondary"}>{j.activation_status}</Badge>
                    {j.activation_network && <span className="text-muted small ms-2">{j.activation_network}</span>}
                  </>
                ) : j.status === "SUCCESS" && j.sqa_property_url ? "Not activated" : "—"}
              </td>
              <td>{j.prod_version ?? "—"}</td>
              <td>{j.sqa_version ?? "—"}</td>
              <td>{j.removed_rules?.length || 0}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
