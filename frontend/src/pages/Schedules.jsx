import React, { useEffect, useState } from "react";
import { Alert, Button, Form, Modal, Table } from "react-bootstrap";
import { Link } from "react-router-dom";
import { deleteProfile, listProfiles, runProfile, updateSchedule } from "../api/client.js";

export default function Schedules() {
  const [profiles, setProfiles] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [message, setMessage] = useState(null);
  const [error, setError] = useState(null);
  const [pendingDelete, setPendingDelete] = useState(null);

  async function reload() {
    try {
      const { data } = await listProfiles();
      setProfiles(data);
    } catch (loadError) {
      setError(loadError?.response?.data?.detail || loadError.message);
    }
  }

  useEffect(() => {
    reload();
  }, []);

  async function handleSave(id) {
    try {
      await updateSchedule(id, drafts[id] || null);
      setMessage(`Updated schedule for profile #${id}`);
      setError(null);
      reload();
    } catch (saveError) {
      setError(saveError?.response?.data?.detail || saveError.message);
    }
  }

  async function handleRunNow(id) {
    try {
      const { data } = await runProfile(id);
      window.location.assign(`/jobs/${data.id}`);
    } catch (runError) {
      setError(runError?.response?.data?.detail || runError.message);
    }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    try {
      await deleteProfile(pendingDelete.id);
      setPendingDelete(null);
      setMessage(`Deleted profile '${pendingDelete.name}'`);
      setError(null);
      reload();
    } catch (deleteError) {
      setError(deleteError?.response?.data?.detail || deleteError.message);
      setPendingDelete(null);
    }
  }

  return (
    <div>
      <h3>Profiles &amp; Schedules</h3>
      {message && <Alert variant="info">{message}</Alert>}
      {error && <Alert variant="danger">{error}</Alert>}
      <Table striped bordered hover responsive size="sm">
        <thead>
          <tr>
            <th>Profile</th>
            <th>Production property</th>
            <th>SQA property</th>
            <th>Last cloned prod version</th>
            <th>Cron schedule</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {profiles.map((p) => (
            <tr key={p.id}>
              <td>{p.name}</td>
              <td>{p.production_property_name}</td>
              <td>{p.sqa_property_name}</td>
              <td>{p.last_known_prod_version ?? "—"}</td>
              <td>
                <Form.Control
                  size="sm"
                  placeholder="e.g. 0 6 * * *"
                  defaultValue={p.schedule_cron || ""}
                  onChange={(e) => setDrafts((prev) => ({ ...prev, [p.id]: e.target.value }))}
                />
              </td>
              <td>
                <div className="d-flex flex-wrap gap-1">
                  <Button size="sm" onClick={() => handleSave(p.id)}>Save schedule</Button>
                  <Button size="sm" variant="secondary" onClick={() => handleRunNow(p.id)}>Run now</Button>
                  <Button size="sm" as={Link} to={`/profiles/${p.id}/edit`} variant="outline-primary">Edit</Button>
                  <Button size="sm" variant="outline-danger" onClick={() => setPendingDelete(p)}>Delete</Button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>

      <Modal show={Boolean(pendingDelete)} onHide={() => setPendingDelete(null)} centered>
        <Modal.Header closeButton>
          <Modal.Title>Delete profile?</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          Delete <strong>{pendingDelete?.name}</strong>, its saved schedule, and its stored job history?
          This cannot be undone.
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setPendingDelete(null)}>Cancel</Button>
          <Button variant="danger" onClick={confirmDelete}>Delete profile</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}
