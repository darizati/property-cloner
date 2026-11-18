import React, { useEffect, useState } from "react";
import { Alert, Button, Col, Form, Modal, Row, Spinner } from "react-bootstrap";
import { ArrowRight, Check, ChevronUp, History as HistoryIcon, Pencil, Play, RefreshCw, Save } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ComboInput from "../components/ComboInput.jsx";
import RuleDiffTable from "../components/RuleDiffTable.jsx";
import {
  createProfile,
  fetchDiff,
  getProfile,
  runProfile,
  updateProfile,
} from "../api/client.js";

// Common values only; the backend treats account type as a free-text account-name filter.
const ACCOUNT_TYPES = ["Direct Customer", "Akamai Internal"];

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
  const [propertiesOpen, setPropertiesOpen] = useState(!editing);
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
        if (!cancelled) {
          setError(loadError?.response?.data?.detail || loadError.message);
          setPropertiesOpen(true);
        }
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
      setPropertiesOpen(false);
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

  const step = !diff ? 1 : pendingOverrides.size || !comparisonCurrent ? 2 : 3;
  const saveDisabled = busy || !comparisonCurrent || pendingOverrides.size > 0;

  return (
    <div className="wizard">
      <div className="page-header">
        <div>
          {editing && <nav className="page-breadcrumb"><Link to="/profiles">Profiles</Link> / Edit</nav>}
          <h1 className="page-title">{editing ? (profileName || `Profile #${profileId}`) : "New clone setup"}</h1>
          <p className="page-subtitle">
            {editing ? "Review the latest rule comparison, adjust decisions, and save or run this profile."
              : "Choose the source and destination properties, review rule differences, then save or run."}
          </p>
        </div>
        {editing && <Button as={Link} to={`/history?profile=${profileId}`} variant="outline-secondary" size="sm" className="btn-icon">
          <HistoryIcon size={16} aria-hidden="true" /> Profile history
        </Button>}
      </div>

      <ol className="wizard-steps" aria-label="Progress">
        {["Properties", "Rules & overrides", "Save or run"].map((label, index) => {
          const number = index + 1;
          const state = number < step ? "done" : number === step ? "current" : "upcoming";
          return (
            <li key={label} className={`wizard-step wizard-step-${state}`} aria-current={state === "current" ? "step" : undefined}>
              <span className="wizard-step-number">{state === "done" ? <Check size={14} aria-hidden="true" /> : number}</span>
              {label}
            </li>
          );
        })}
      </ol>

      {error && <Alert variant="danger" dismissible onClose={() => setError(null)}>{error}</Alert>}
      {loadingProfile && <div role="status" className="panel panel-body mb-3 text-muted"><Spinner animation="border" size="sm" className="me-2" />Loading saved profile and rule comparison…</div>}

      <section className="panel">
        <div className="panel-header">
          <h2 className="panel-title"><span className="panel-step">1</span> Properties</h2>
          {diff && (
            <Button size="sm" variant="outline-secondary" className="btn-icon" aria-expanded={propertiesOpen}
              onClick={() => setPropertiesOpen((open) => !open)}>
              {propertiesOpen ? <><ChevronUp size={16} aria-hidden="true" /> Hide settings</> : <><Pencil size={15} aria-hidden="true" /> Edit properties</>}
            </Button>
          )}
        </div>

        <div className="panel-body">
          {!propertiesOpen ? (
            <div className="property-route">
              <PropertySummary label="Production source" tone="source" value={production} />
              <ArrowRight className="property-route-arrow" size={22} aria-hidden="true" />
              <PropertySummary label="SQA Shared destination" tone="destination" value={shared}
                extra={`Group ${sqaGroupId || "not set"}`} />
            </div>
          ) : (
            <>
              <Form.Group className="property-product" controlId="product-id">
                <Form.Label>Product ID</Form.Label>
                <Form.Control value={productId} onChange={(e) => setProductId(e.target.value)} />
              </Form.Group>

              <div className="property-route property-route-edit">
                <PropertyCard label="Production source" tone="source" hint="Rules are copied from here" value={production} onChange={setProduction} idPrefix="prod" />
                <ArrowRight className="property-route-arrow" size={22} aria-hidden="true" />
                <PropertyCard label="SQA Shared destination" tone="destination" hint="Created or updated as a new draft" value={shared} onChange={setShared} idPrefix="sqa">
                  <Form.Group controlId="sqa-group-id" className="property-field-full">
                    <Form.Label>SQA group ID</Form.Label>
                    <Form.Control value={sqaGroupId} placeholder="e.g. 35723" onChange={(e) => setSqaGroupId(e.target.value)} />
                  </Form.Group>
                </PropertyCard>
              </div>

              <div className="panel-actions">
                {diff && !comparisonCurrent && <span className="text-warning-emphasis small me-auto">Settings changed since the last comparison.</span>}
                <Button onClick={handleFetchDiff} disabled={busy || pendingOverrides.size > 0} className="btn-icon">
                  {fetching ? <Spinner animation="border" size="sm" aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}
                  {diff ? "Re-compare rule trees" : "Fetch & compare rule trees"}
                </Button>
              </div>
            </>
          )}
        </div>
      </section>

      {diff && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title"><span className="panel-step">2</span> Rules, variables &amp; overrides</h2>
            <span className="text-muted small">Production v{diff.prod_version} · SQA{" "}
              {diff.sqa_property_exists ? `v${diff.sqa_version}` : "new property"}</span>
          </div>
          <div className="panel-body">
            {!comparisonCurrent && !fetching && <Alert variant="warning">The comparison does not match the current property settings. Re-compare before saving or running.</Alert>}
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
          </div>
        </section>
      )}

      {diff && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title"><span className="panel-step">3</span> Profile settings</h2>
          </div>
          <div className="panel-body">
            <Row className="g-3">
              <Col md={6}>
                <Form.Group controlId="profile-name">
                  <Form.Label>Profile name</Form.Label>
                  <Form.Control value={profileName} placeholder="e.g. ion-func-example" onChange={(e) => setProfileName(e.target.value)} />
                </Form.Group>
              </Col>
              <Col md={6}>
                <Form.Group controlId="profile-schedule">
                  <Form.Label>Schedule <span className="text-muted fw-normal">(cron, optional)</span></Form.Label>
                  <Form.Control value={scheduleCron} placeholder="0 6 * * *" onChange={(e) => setScheduleCron(e.target.value)} />
                  <Form.Text>Leave blank for manual runs only.</Form.Text>
                </Form.Group>
              </Col>
              <Col md={12}>
                <Form.Group controlId="profile-emails">
                  <Form.Label>Notification emails</Form.Label>
                  <Form.Control value={notifyEmails} placeholder="alice@example.com, bob@example.com" onChange={(e) => setNotifyEmails(e.target.value)} />
                  <Form.Text>Comma separated.</Form.Text>
                </Form.Group>
              </Col>
            </Row>
          </div>
        </section>
      )}

      {diff && (
        <div className="wizard-action-bar">
          <div className="d-flex flex-wrap align-items-center gap-3">
            <Form.Check
              id="attempt-activation"
              type="switch"
              label="Attempt activation"
              checked={attemptActivation}
              disabled={busy}
              onChange={(event) => setAttemptActivation(event.target.checked)}
            />
            {pendingOverrides.size > 0 && <span className="text-warning-emphasis small">{pendingOverrides.size} {pendingOverrides.size === 1 ? "item has" : "items have"} unapplied JSON changes.</span>}
          </div>
          <div className="wizard-action-buttons">
            <Button variant="outline-secondary" className="btn-icon" disabled={saveDisabled} onClick={() => setConfirmAction("save")}>
              <Save size={16} aria-hidden="true" /> Save profile
            </Button>
            <Button className="btn-icon" disabled={saveDisabled || missingSqaPolicies.length > 0} onClick={() => setConfirmAction("run")}>
              {saving ? <Spinner animation="border" size="sm" aria-hidden="true" /> : <Play size={16} aria-hidden="true" />} Save &amp; run now
            </Button>
          </div>
        </div>
      )}

      <Modal show={Boolean(confirmAction)} onHide={() => setConfirmAction(null)} centered>
        <Modal.Header closeButton>
          <Modal.Title>{confirmAction === "run" ? "Start clone job?" : "Save profile?"}</Modal.Title>
        </Modal.Header>
        <Modal.Body>
          <dl className="confirm-list">
            <dt>Production source</dt>
            <dd>{production.property_name}<br /><small className="text-muted">{production.account_name} · {production.contract_id}</small></dd>
            <dt>SQA Shared destination</dt>
            <dd>{shared.property_name}<br /><small className="text-muted">{shared.account_name} · {shared.contract_id} · Group {sqaGroupId}</small></dd>
            <dt>Activation</dt>
            <dd>{attemptActivation ? "Attempt activation when eligible" : "Draft only; do not activate"}</dd>
          </dl>
          <p className="mb-0">
            {confirmAction === "run"
              ? "This saves the current rules and settings, then starts a background clone job. You will be taken to its live console."
              : "Save the current property references, rule policies, overrides, activation preference, notifications, and schedule?"}
          </p>
        </Modal.Body>
        <Modal.Footer>
          <Button variant="outline-secondary" onClick={() => setConfirmAction(null)}>Cancel</Button>
          <Button disabled={saveDisabled || (confirmAction === "run" && missingSqaPolicies.length > 0)} onClick={commitSave}>Confirm</Button>
        </Modal.Footer>
      </Modal>
    </div>
  );
}

function PropertySummary({ label, tone, value, extra }) {
  return (
    <div className={`property-card property-card-${tone}`}>
      <div className="property-card-label">{label}</div>
      <div className="property-card-name">{value.property_name || <span className="text-muted">Not selected</span>}</div>
      <div className="property-card-meta">
        {[value.account_name, value.contract_id, extra].filter(Boolean).join(" · ") || "—"}
      </div>
    </div>
  );
}

function PropertyCard({ label, tone, hint, value, onChange, idPrefix, children }) {
  const set = (field) => (e) => onChange({ ...value, [field]: e.target.value });
  return (
    <fieldset className={`property-card property-card-${tone}`} aria-label={label}>
      <div className="property-card-label" aria-hidden="true">{label}</div>
      <div className="property-card-hint">{hint}</div>
      <div className="property-fields">
        <Form.Group controlId={`${idPrefix}-property-name`} className="property-field-full">
          <Form.Label>Property name</Form.Label>
          <Form.Control value={value.property_name} onChange={set("property_name")} />
        </Form.Group>
        <Form.Group controlId={`${idPrefix}-account-name`}>
          <Form.Label>Account name</Form.Label>
          <Form.Control value={value.account_name} onChange={set("account_name")} />
        </Form.Group>
        <Form.Group controlId={`${idPrefix}-account-type`}>
          <Form.Label>Account type</Form.Label>
          <ComboInput id={`${idPrefix}-account-type`} value={value.account_type} options={ACCOUNT_TYPES}
            placeholder="e.g. Direct Customer" onChange={(next) => onChange({ ...value, account_type: next })} />
        </Form.Group>
        <Form.Group controlId={`${idPrefix}-contract-id`} className="property-field-full">
          <Form.Label>Contract ID</Form.Label>
          <Form.Control value={value.contract_id} onChange={set("contract_id")} />
        </Form.Group>
        {children}
      </div>
    </fieldset>
  );
}
