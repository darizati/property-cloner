import React, { useEffect, useState } from "react";
import { Alert, Button, Col, Form, Modal, Row, Spinner } from "react-bootstrap";
import { ChevronRight, Play, Save, RefreshCw } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import RuleDiffTable from "../components/RuleDiffTable.jsx";
import {
  createProfile,
  fetchDiff,
  getProfile,
  runProfile,
  updateProfile,
} from "../api/client.js";

const blankRef = { account_name: "", account_type: "", contract_id: "", property_name: "" };

function propertyRef(profile, prefix) {
  return {
    account_name: profile[`${prefix}_account_name`] || "",
    account_type: profile[`${prefix}_account_type`] || "",
    contract_id: profile[`${prefix}_contract_id`] || "",
    property_name: profile[`${prefix}_property_name`] || "",
  };
}

export default function SetupWizard() {
  const { profileId } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(profileId);

  const [productId, setProductId] = useState("prd_Fresca");
  const [production, setProduction] = useState({ ...blankRef, account_type: "Direct Customer" });
  const [shared, setShared] = useState({ ...blankRef, account_type: "Akamai Internal" });
  const [sqaGroupId, setSqaGroupId] = useState("");
  const [sqaEdgeHostnameSecure, setSqaEdgeHostnameSecure] = useState("");
  const [sqaEdgeHostnameNonsecure, setSqaEdgeHostnameNonsecure] = useState("");

  const [diff, setDiff] = useState(null);
  const [comparisonRequest, setComparisonRequest] = useState(null);
  const [loadingProfile, setLoadingProfile] = useState(editing);
  const [fetching, setFetching] = useState(false);
  const [policies, setPolicies] = useState({});
  const [overrides, setOverrides] = useState({});
  const [pendingOverrides, setPendingOverrides] = useState(new Set());
  const [error, setError] = useState(null);

  const [profileName, setProfileName] = useState("");
  const [notifyEmails, setNotifyEmails] = useState("");
  const [attemptActivation, setAttemptActivation] = useState(false);
  const [scheduleCron, setScheduleCron] = useState("");
  const [confirmAction, setConfirmAction] = useState(null);
  const [saving, setSaving] = useState(false);

  const diffRequest = {
    product_id: productId, production, shared, sqa_group_id: sqaGroupId,
  };
  const comparisonCurrent = Boolean(diff) && JSON.stringify(comparisonRequest) === JSON.stringify(diffRequest);
  const busy = loadingProfile || fetching || saving;
  const unmatchedPolicies = diff
    ? Object.entries(policies).filter(([signature]) => !diff.nodes.some((node) => node.signature === signature))
    : [];
  const missingSqaPolicies = diff
    ? Object.entries(policies).filter(([signature, policy]) => policy === "sqa"
      && !diff.nodes.some((node) => node.signature === signature && node.in_sqa))
    : [];

  useEffect(() => {
    if (!profileId) return undefined;
    let cancelled = false;
    setLoadingProfile(true);
    setDiff(null);
    setComparisonRequest(null);

    async function loadProfile() {
      try {
        const { data } = await getProfile(profileId);
        if (cancelled) return;
        const productionRef = propertyRef(data, "production");
        const sharedRef = propertyRef(data, "sqa");
        setProductId(data.product_id);
        setProfileName(data.name);
        setProduction(productionRef);
        setShared(sharedRef);
        setSqaGroupId(data.sqa_group_id || "");
        setSqaEdgeHostnameSecure(data.sqa_edge_hostname_secure || "");
        setSqaEdgeHostnameNonsecure(data.sqa_edge_hostname_nonsecure || "");
        setNotifyEmails((data.notify_emails || []).join(", "));
        setAttemptActivation(data.attempt_activation ?? false);
        setScheduleCron(data.schedule_cron || "");
        setPolicies(data.policies || {});
        setOverrides(data.field_overrides || {});

        const request = {
          product_id: data.product_id,
          production: productionRef,
          shared: sharedRef,
          sqa_group_id: data.sqa_group_id || "",
        };
        const diffResponse = await fetchDiff(request);
        if (!cancelled) {
          setDiff(diffResponse.data);
          setComparisonRequest(request);
          const defaults = {};
          diffResponse.data.nodes.forEach((node) => {
            defaults[node.signature] = node.default_policy;
          });
          setPolicies((current) => ({ ...defaults, ...current }));
        }
      } catch (loadError) {
        if (!cancelled) setError(loadError?.response?.data?.detail || loadError.message);
      } finally {
        if (!cancelled) setLoadingProfile(false);
      }
    }

    loadProfile();
    return () => {
      cancelled = true;
    };
  }, [profileId]);

  async function handleFetchDiff() {
    if (busy || pendingOverrides.size) return;
    setError(null);
    setFetching(true);
    setComparisonRequest(null);
    try {
      const { data } = await fetchDiff(diffRequest);
      setDiff(data);
      setComparisonRequest(diffRequest);
      const initialPolicies = {};
      data.nodes.forEach((n) => (initialPolicies[n.signature] = n.default_policy));
      setPolicies((current) => ({ ...initialPolicies, ...current }));
    } catch (e) {
      setError(e?.response?.data?.detail || e.message);
    } finally {
      setFetching(false);
    }
  }

  function handlePolicyChange(sig, value) {
    setPolicies((prev) => ({ ...prev, [sig]: value }));
  }

  function handleSubsectionPolicyChange(nodes, value) {
    setPolicies((previous) => {
      const next = { ...previous };
      nodes.forEach((node) => { next[node.signature] = value; });
      return next;
    });
  }

  function handlePendingChange(signature, pending) {
    setPendingOverrides((previous) => {
      const next = new Set(previous);
      if (pending) next.add(signature);
      else next.delete(signature);
      return next;
    });
  }

  function handleOverrideChange(sig, rawJson) {
    if (!rawJson.trim()) {
      setOverrides((prev) => {
        const next = { ...prev };
        delete next[sig];
        return next;
      });
      return;
    }
    try {
      const parsed = JSON.parse(rawJson);
      setOverrides((prev) => ({ ...prev, [sig]: parsed }));
      setError(null);
    } catch {
      setError(`Invalid JSON for override on ${sig}`);
    }
  }

  function buildPayload() {
    return {
      name: profileName,
      product_id: productId,
      production,
      shared,
      sqa_group_id: sqaGroupId,
      sqa_edge_hostname_secure: sqaEdgeHostnameSecure,
      sqa_edge_hostname_nonsecure: sqaEdgeHostnameNonsecure,
      policies,
      field_overrides: overrides,
      notify_emails: notifyEmails.split(",").map((email) => email.trim()).filter(Boolean),
      attempt_activation: attemptActivation,
      schedule_cron: scheduleCron || null,
    };
  }

  async function commitSave() {
    const runNow = confirmAction === "run";
    if (busy || !comparisonCurrent || pendingOverrides.size || (runNow && missingSqaPolicies.length)) return;
    setConfirmAction(null);
    setSaving(true);
    setError(null);
    try {
      const response = editing
        ? await updateProfile(profileId, buildPayload())
        : await createProfile(buildPayload());
      const savedId = response.data.id;
      if (runNow) {
        const jobResponse = await runProfile(savedId);
        navigate(`/jobs/${jobResponse.data.id}`);
      } else {
        navigate("/profiles");
      }
    } catch (saveError) {
      setError(saveError?.response?.data?.detail || saveError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h3>{editing ? `Edit Profile #${profileId}` : "Setup Wizard"}</h3>
      {error && <Alert variant="danger">{error}</Alert>}
      {loadingProfile && <div role="status" className="mb-3"><Spinner animation="border" size="sm" className="me-2" />Loading saved profile and rule comparison...</div>}

      <Row className="g-2 mb-3 wizard-targets">
        <Col md={6}>
          <div className="text-muted small">Production source</div>
          <strong>{production.property_name || "Not selected"}</strong>
          <div className="small">{production.account_name} · {production.contract_id}</div>
        </Col>
        <Col md={6}>
          <div className="text-muted small">SQA Shared destination</div>
          <strong>{shared.property_name || "Not selected"}</strong>
          <div className="small">{shared.account_name} · {shared.contract_id} · Group {sqaGroupId || "not selected"}</div>
        </Col>
      </Row>

      <details className="wizard-section wizard-properties" open={!diff}>
        <summary><ChevronRight size={18} aria-hidden="true" /> Property settings</summary>
        <div className="wizard-section-content">
          <Form.Group className="mb-3">
            <Form.Label>Product ID</Form.Label>
            <Form.Control value={productId} onChange={(e) => setProductId(e.target.value)} />
          </Form.Group>

          <Row>
            <Col md={6}>
              <h5>Production</h5>
              <PropertyRefForm value={production} onChange={setProduction} />
            </Col>
            <Col md={6}>
              <h5>SQA Shared</h5>
              <PropertyRefForm value={shared} onChange={setShared} />
              <Form.Group className="mb-3">
                <Form.Label>SQA Group ID</Form.Label>
                <Form.Control value={sqaGroupId} onChange={(e) => setSqaGroupId(e.target.value)} />
              </Form.Group>
            </Col>
          </Row>

          <Button onClick={handleFetchDiff} disabled={busy || pendingOverrides.size > 0}>
            {fetching ? <Spinner animation="border" size="sm" aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />} Fetch &amp; Diff Rule Trees
          </Button>
        </div>
      </details>

      {diff && (
        <section className="wizard-rules">
            <div className="wizard-rules-heading">
              <h4>Rules, Variables &amp; Overrides</h4>
              <span className="text-muted small">Production v{diff.prod_version} · SQA{" "}
                {diff.sqa_property_exists ? `v${diff.sqa_version}` : "new property"}</span>
            </div>
            {!comparisonCurrent && !fetching && <Alert variant="warning">The comparison does not match the current property settings. Saving and running are disabled.</Alert>}
            {missingSqaPolicies.length > 0 && <Alert variant="danger">
              {missingSqaPolicies.length} SQA-selected {missingSqaPolicies.length === 1 ? "item is" : "items are"} missing from the current SQA tree. Cloning is blocked to prevent losing those rules.
            </Alert>}
            {unmatchedPolicies.length > 0 && <details className="mb-3 small">
              <summary>Retained decisions for {unmatchedPolicies.length} items absent from the current comparison</summary>
              <ul>{unmatchedPolicies.map(([signature, policy]) => <li key={signature}><code>{signature}</code>: {policy}</li>)}</ul>
            </details>}
            <RuleDiffTable
              nodes={diff.nodes}
              policies={policies}
              onPolicyChange={handlePolicyChange}
              onSubsectionPolicyChange={handleSubsectionPolicyChange}
              overrides={overrides}
              onOverrideChange={handleOverrideChange}
              onPendingChange={handlePendingChange}
            />
        </section>
      )}

      {diff && (
        <section className="wizard-profile">
          <details className="wizard-section" open>
            <summary><ChevronRight size={18} aria-hidden="true" /> Profile settings</summary>
            <div className="wizard-section-content">
            <Form.Group className="mb-3">
              <Form.Label>Profile name</Form.Label>
              <Form.Control value={profileName} onChange={(e) => setProfileName(e.target.value)} />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Notify emails (comma separated)</Form.Label>
              <Form.Control value={notifyEmails} onChange={(e) => setNotifyEmails(e.target.value)} />
            </Form.Group>
            <Form.Group className="mb-3">
              <Form.Label>Schedule (cron, optional — e.g. "0 6 * * *")</Form.Label>
              <Form.Control value={scheduleCron} onChange={(e) => setScheduleCron(e.target.value)} />
            </Form.Group>
            </div>
          </details>
          {pendingOverrides.size > 0 && <Alert variant="warning" className="mt-3">{pendingOverrides.size} {pendingOverrides.size === 1 ? "item has" : "items have"} unapplied JSON changes.</Alert>}
          <div className="wizard-save-actions">
            <Form.Check
              id="attempt-activation"
              type="checkbox"
              className="me-3 align-self-center"
              label="Attempt activation"
              checked={attemptActivation}
              disabled={busy}
              onChange={(event) => setAttemptActivation(event.target.checked)}
            />
            <Button
              variant="secondary"
              className="me-2"
              disabled={busy || !comparisonCurrent || pendingOverrides.size > 0}
              onClick={() => setConfirmAction("save")}
            >
              <Save size={16} aria-hidden="true" /> Save Profile
            </Button>
            <Button disabled={busy || !comparisonCurrent || pendingOverrides.size > 0 || missingSqaPolicies.length > 0} onClick={() => setConfirmAction("run")}>
              {saving && <Spinner animation="border" size="sm" className="me-2" />}
              <Play size={16} aria-hidden="true" /> Save &amp; Run Now
            </Button>
          </div>
        </section>
      )}

      <Modal show={Boolean(confirmAction)} onHide={() => setConfirmAction(null)} centered>
        <Modal.Header closeButton>
          <Modal.Title>{confirmAction === "run" ? "Start clone job?" : "Save profile?"}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <dl>
            <dt>Production source</dt>
            <dd>{production.property_name}<br /><small>{production.account_name} · {production.contract_id}</small></dd>
            <dt>SQA Shared destination</dt>
            <dd>{shared.property_name}<br /><small>{shared.account_name} · {shared.contract_id} · Group {sqaGroupId}</small></dd>
            <dt>Activation</dt>
            <dd>{attemptActivation ? "Attempt activation when eligible" : "Draft only; do not activate"}</dd>
          </dl>
          {confirmAction === "run"
            ? "This saves the current rules and settings, then starts a background clone job. You will be taken to its live console."
            : "Save the current property references, rule policies, overrides, activation preference, notifications, and schedule?"}
        </Modal.Body>
        <Modal.Footer>
          <Button variant="secondary" onClick={() => setConfirmAction(null)}>Cancel</Button>
          <Button disabled={busy || !comparisonCurrent || pendingOverrides.size > 0 || (confirmAction === "run" && missingSqaPolicies.length > 0)} onClick={commitSave}>Confirm</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}

function PropertyRefForm({ value, onChange }) {
  const set = (field) => (e) => onChange({ ...value, [field]: e.target.value });
  return (
    <>
      <Form.Group className="mb-2">
        <Form.Label>Account name</Form.Label>
        <Form.Control value={value.account_name} onChange={set("account_name")} />
      </Form.Group>
      <Form.Group className="mb-2">
        <Form.Label>Account type</Form.Label>
        <Form.Control value={value.account_type} onChange={set("account_type")} />
      </Form.Group>
      <Form.Group className="mb-2">
        <Form.Label>Contract ID</Form.Label>
        <Form.Control value={value.contract_id} onChange={set("contract_id")} />
      </Form.Group>
      <Form.Group className="mb-2">
        <Form.Label>Property name</Form.Label>
        <Form.Control value={value.property_name} onChange={set("property_name")} />
      </Form.Group>
    </>
  );
}
