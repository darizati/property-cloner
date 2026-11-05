import React, { useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Col, Row, Spinner } from "react-bootstrap";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getJob } from "../api/client.js";

const terminalStatuses = new Set([
  "SUCCESS",
  "PARTIAL_SUCCESS",
  "FAILED",
  "SKIPPED_NO_NEWER_VERSION",
  "SKIPPED_NO_RULE_CHANGE",
]);

const statusVariant = {
  SUCCESS: "success",
  PARTIAL_SUCCESS: "warning",
  FAILED: "danger",
  RUNNING: "info",
  PENDING: "secondary",
  SKIPPED_NO_NEWER_VERSION: "secondary",
  SKIPPED_NO_RULE_CHANGE: "secondary",
};

export default function JobDetail() {
  const { jobId } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState(null);
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
        if (mounted) setError(loadError?.response?.data?.detail || loadError.message);
      }
    }

    timer = setInterval(load, 1500);
    load();
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [jobId]);

  if (error) return <Alert variant="danger">{error}</Alert>;
  if (!job) {
    return (
      <div className="text-center py-5">
        <Spinner animation="border" />
        <p className="mt-2">Loading job console...</p>
      </div>
    );
  }

  const active = !terminalStatuses.has(job.status);
  const activated = job.status === "SUCCESS" && job.activation_status === "ACTIVE";
  const savedDraft = job.status === "SUCCESS" && Boolean(job.sqa_property_url) && !activated;
  return (
    <div>
      <div className="d-flex justify-content-between align-items-center mb-3">
        <div>
          <h3 className="mb-1">Clone Job #{job.id}</h3>
          <span className="text-muted">Profile #{job.profile_id} · {job.trigger} run</span>
        </div>
        <Badge bg={statusVariant[job.status] || "secondary"} className="fs-6">
          {active && <Spinner animation="border" size="sm" className="me-2" />}
          {activated ? "ACTIVATED" : savedDraft ? "SAVED FOR REVIEW" : job.status}
        </Badge>
      </div>

      <Row className="g-3 mb-3">
        <Col md={3}><Metric label="Production version" value={job.prod_version ?? "—"} /></Col>
        <Col md={3}><Metric label="SQA version" value={job.sqa_version ?? "—"} /></Col>
        <Col md={3}><Metric label="Rules removed" value={job.removed_rules?.length || 0} /></Col>
        <Col md={3}><Metric label="Property created" value={job.created_new_property ? "Yes" : "No"} /></Col>
      </Row>

      {savedDraft && <Alert variant="success">Draft saved. Not activated.</Alert>}
      {activated && <Alert variant="success">Version {job.sqa_version} activated on {job.activation_network}.</Alert>}
      {!activated && job.activation_status && (
        <Alert variant="warning">
          Rule tree saved in version {job.sqa_version}. Activation status: {job.activation_status}
          {job.activation_network && ` on ${job.activation_network}`}.
        </Alert>
      )}

      {job.sqa_property_url && (
        <div className="mb-3">
          <Button
            as="a"
            href={job.sqa_property_url}
            target="_blank"
            rel="noopener noreferrer"
            variant="primary"
          >
            Open SQA Property
          </Button>
        </div>
      )}

      {job.error_summary && (
        <Alert variant="danger">
          <Alert.Heading>Job error</Alert.Heading>
          <pre className="mb-0" style={{ whiteSpace: "pre-wrap" }}>{job.error_summary}</pre>
        </Alert>
      )}

      {job.removed_rules?.length > 0 && (
        <Alert variant="warning">
          <Alert.Heading>Rules removed during activation retry</Alert.Heading>
          <ul className="mb-0">
            {job.removed_rules.map((rule, index) => (
              <li key={`${rule.location}-${index}`}>
                <code>{rule.location}</code>: {rule.detail || "No detail returned"}
              </li>
            ))}
          </ul>
        </Alert>
      )}

      <Card>
        <Card.Header className="d-flex justify-content-between align-items-center">
          <span>Live console output</span>
          {active && <span className="text-muted small">refreshing every 1.5 seconds</span>}
        </Card.Header>
        <Card.Body className="p-0">
          <pre
            className="bg-dark text-light m-0 p-3"
            style={{ minHeight: 360, maxHeight: 620, overflow: "auto", whiteSpace: "pre-wrap" }}
          >
            {job.log || job.error_summary || "Waiting for the clone worker to start..."}
          </pre>
        </Card.Body>
      </Card>

      <div className="mt-3">
        <Button variant="secondary" className="me-2" onClick={() => navigate("/history")}>
          Back to history
        </Button>
        <Button as={Link} to={`/profiles/${job.profile_id}/edit`} variant="outline-primary">
          Edit profile
        </Button>
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <Card>
      <Card.Body>
        <div className="text-muted small">{label}</div>
        <div className="fs-4">{value}</div>
      </Card.Body>
    </Card>
  );
}
