import React, { useState } from "react";
import { Badge, Button, Form, Table } from "react-bootstrap";
import { ChevronRight, ChevronsDownUp, ChevronsUpDown, Code2, Plus, Search, Trash2 } from "lucide-react";

const EMPTY = {};

function valueType(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value === "object" ? "object" : typeof value;
}

function equalValues(left, right) {
  if (Object.is(left, right)) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length
    && keys.every((key) => Object.hasOwn(right, key) && equalValues(left[key], right[key]));
}

function displayValue(value) {
  if (value === undefined) return "Not present";
  if (value === "") return '""';
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function changedFields(node) {
  if (!node.in_prod || !node.in_sqa) return [];
  return [...new Set([...Object.keys(node.prod_options || EMPTY), ...Object.keys(node.sqa_options || EMPTY)])]
    .filter((key) => !equalValues(node.prod_options?.[key], node.sqa_options?.[key]));
}

function sameInBoth(node) {
  return node.in_prod && node.in_sqa && equalValues(node.prod_options, node.sqa_options);
}

function differenceReason(left, right) {
  if (equalValues(left, right)) return "Same";
  if (left === undefined || right === undefined) return "Option missing on one side";
  if (valueType(left) !== valueType(right)) return `Type differs: ${valueType(left)} vs ${valueType(right)}`;
  if (typeof left === "string" && typeof right === "string") {
    if (left.replace(/\r\n/g, "\n") === right.replace(/\r\n/g, "\n")) return "Line endings differ (CRLF vs LF)";
    if (left.trimEnd() === right.trimEnd()) return "Trailing whitespace / newline differs";
    if (left.replace(/\s/g, "") === right.replace(/\s/g, "")) return "Whitespace differs";
    return "Text differs";
  }
  if (Array.isArray(left)) return "Array contents or order differ";
  if (left && typeof left === "object") return "Nested fields differ";
  return "Value differs";
}

function visibleWhitespace(value) {
  return value.replace(/ /g, "\u00b7").replace(/\t/g, "\\t").replace(/\r/g, "\\r").replace(/\n/g, "\\n\n");
}

function nodeMatches(node, query, changedOnly) {
  if (changedOnly && changedFields(node).length === 0 && node.in_prod && node.in_sqa) return false;
  if (!query) return true;
  return [node.name, node.kind, node.rule_path].some((text) => text?.toLowerCase().includes(query));
}

export default function RuleDiffTable({ nodes, policies, onPolicyChange, onSubsectionPolicyChange, overrides, onOverrideChange, onPendingChange }) {
  const [closedGroups, setClosedGroups] = useState(new Set());
  const [expandedItems, setExpandedItems] = useState(new Set());
  const [search, setSearch] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const query = search.trim().toLowerCase();
  const filtering = Boolean(query) || changedOnly;
  const visibleNodes = new Set(nodes.filter((node) => nodeMatches(node, query, changedOnly)).map((node) => node.signature));
  const groups = new Map();
  nodes.forEach((node) => {
    const parts = node.rule_path.split("/");
    parts.forEach((_, index) => {
      const path = parts.slice(0, index + 1).join("/");
      if (!groups.has(path)) groups.set(path, []);
    });
    groups.get(node.rule_path).push(node);
  });

  function toggleGroup(path) {
    setClosedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  return (
    <div className="rule-diff">
      <div className="rule-diff-stats">
        <span><strong>{groups.size}</strong> sections</span>
        <span><strong>{nodes.length}</strong> items</span>
        <span><strong>{nodes.filter((node) => node.kind === "variable").length}</strong> variables</span>
        <span className="rule-diff-stat-changed"><strong>{nodes.filter((node) => changedFields(node).length > 0).length}</strong> changed</span>
        <span><strong>{nodes.filter((node) => !node.in_prod || !node.in_sqa).length}</strong> one-sided</span>
      </div>
      <div className="rule-diff-toolbar">
        <div className="rule-diff-filters">
          <div className="search-input">
            <Search size={15} aria-hidden="true" />
            <Form.Control size="sm" type="search" placeholder="Filter rules, behaviors, variables…" aria-label="Filter rule items"
              value={search} onChange={(event) => {
                setSearch(event.target.value);
                if (event.target.value.trim()) setClosedGroups(new Set());
              }} />
          </div>
          <Form.Check type="switch" id="rule-diff-changed-only" label="Differences only" checked={changedOnly}
            onChange={(event) => {
              setChangedOnly(event.target.checked);
              if (event.target.checked) setClosedGroups(new Set());
            }} />
          {filtering && <span className="text-muted small">{visibleNodes.size} of {nodes.length} shown</span>}
        </div>
        <div className="d-flex gap-2">
          <Button size="sm" variant="outline-secondary" onClick={() => {
            setClosedGroups(new Set());
            setExpandedItems(new Set(nodes.map((node) => node.signature)));
          }}><ChevronsUpDown size={16} aria-hidden="true" /> Expand all</Button>
          <Button size="sm" variant="outline-secondary" onClick={() => {
            setClosedGroups(new Set(groups.keys()));
            setExpandedItems(new Set());
          }}><ChevronsDownUp size={16} aria-hidden="true" /> Collapse all</Button>
        </div>
      </div>
      {[...groups].map(([path, items]) => {
        const closed = closedGroups.has(path);
        const descendants = nodes.filter((node) => node.rule_path === path || node.rule_path.startsWith(`${path}/`));
        const ancestors = path.split("/").slice(0, -1).map((_, index) => path.split("/").slice(0, index + 1).join("/"));
        const changedCount = descendants.filter((node) => changedFields(node).length > 0).length;
        const selectedPolicies = new Set(descendants.map((node) => policies[node.signature] || node.default_policy));
        const subsectionPolicy = selectedPolicies.size === 1 ? [...selectedPolicies][0] : "mixed";
        const sameValues = descendants.every(sameInBoth);
        const displayedPolicy = sameValues && !selectedPolicies.has("remove") ? "same" : subsectionPolicy;
        const allInProd = descendants.every((node) => node.in_prod);
        const allInSqa = descendants.every((node) => node.in_sqa);
        const segments = path.split("/");
        const hasVisible = descendants.some((node) => visibleNodes.has(node.signature));
        const visibleItems = items.filter((node) => visibleNodes.has(node.signature));
        return (
          <section className={`rule-group${segments.length > 1 ? " rule-group-nested" : ""}`} key={path}
            style={{ "--depth": segments.length - 1 }}
            hidden={!hasVisible || ancestors.some((ancestor) => closedGroups.has(ancestor))}>
            <div className="rule-group-heading">
            <button type="button" className="rule-group-toggle" aria-expanded={!closed} title={path} onClick={() => toggleGroup(path)}>
              <ChevronRight className={closed ? "" : "chevron-open"} size={18} aria-hidden="true" />
              <span className="rule-section-title">{segments[segments.length - 1]}</span>
              <span className="rule-section-counts">
                {changedCount > 0 && <Badge bg="warning" text="dark">{changedCount} changed {changedCount === 1 ? "item" : "items"}</Badge>}
                <span>{descendants.length} {descendants.length === 1 ? "item" : "items"}</span>
              </span>
            </button>
            <Form.Select size="sm" className="rule-subsection-select" aria-label={`Source for subsection ${path}`} value={displayedPolicy}
              onChange={(event) => onSubsectionPolicyChange(descendants, event.target.value)}>
              {sameValues && <option value="same" disabled>Same in both</option>}
              <option value="mixed" disabled>Mixed decisions</option>
              <option value="prod" disabled={!allInProd}>{allInProd ? "Production for subsection" : "Production (some items absent)"}</option>
              <option value="sqa" disabled={!allInSqa}>{allInSqa ? "SQA for subsection" : "SQA (some items absent)"}</option>
              <option value="remove">Remove subsection items</option>
            </Form.Select>
            </div>
            <div className="rule-node-list" hidden={closed || visibleItems.length === 0}>
              {visibleItems.map((node) => (
                <RuleNode key={node.signature} node={node}
                  open={expandedItems.has(node.signature)}
                  onToggle={() => setExpandedItems((previous) => {
                    const next = new Set(previous);
                    if (next.has(node.signature)) next.delete(node.signature);
                    else next.add(node.signature);
                    return next;
                  })}
                  policy={policies[node.signature] || node.default_policy}
                  onPolicyChange={onPolicyChange}
                  override={overrides[node.signature] || EMPTY}
                  onOverrideChange={onOverrideChange}
                  onPendingChange={onPendingChange}
                />
              ))}
            </div>
          </section>
        );
      })}
      {filtering && visibleNodes.size === 0 && <div className="empty-state">No rule items match the current filter.</div>}
    </div>
  );
}

function RuleNode({ node, open, onToggle, policy, onPolicyChange, override, onOverrideChange, onPendingChange }) {
  const sqaLabel = node.sqa_source_version ? `SQA v${node.sqa_source_version} (active)` : "SQA";
  const changes = changedFields(node);
  const fields = [...new Set([
    ...Object.keys(node.prod_options || EMPTY), ...Object.keys(node.sqa_options || EMPTY), ...Object.keys(override),
  ])].sort();
  const [field, setField] = useState("");
  const [source, setSource] = useState("custom");
  const [type, setType] = useState("string");
  const [value, setValue] = useState("");
  const [fieldError, setFieldError] = useState("");
  const [rawDraft, setRawDraft] = useState(null);
  const [jsonError, setJsonError] = useState("");
  const [showWhitespace, setShowWhitespace] = useState(false);
  const hasOverride = Object.keys(override).length > 0;
  const removed = policy === "remove";
  const jsonDirty = rawDraft !== null;

  function commit(next) {
    onOverrideChange(node.signature, JSON.stringify(next));
    setFieldError("");
  }

  function selectField(nextField) {
    setField(nextField);
    const sample = Object.hasOwn(override, nextField) ? override[nextField]
      : Object.hasOwn(node.prod_options || EMPTY, nextField) ? node.prod_options[nextField] : node.sqa_options?.[nextField];
    setType(sample === undefined ? "string" : valueType(sample));
    setValue(sample === undefined ? "" : typeof sample === "string" ? sample : JSON.stringify(sample));
    setSource("custom");
    setFieldError("");
  }

  function setFixedValue() {
    try {
      let parsed;
      if (source !== "custom") {
        parsed = (source === "prod" ? node.prod_options : node.sqa_options)[field];
      } else if (type === "string") parsed = value;
      else if (type === "null") parsed = null;
      else {
        if (!value.trim()) throw new Error("Enter a value.");
        parsed = JSON.parse(value);
        if (valueType(parsed) !== type) throw new Error(`Enter a valid ${type} value.`);
        if (type === "number" && !Number.isFinite(parsed)) throw new Error("Enter a finite number.");
      }
      commit({ ...override, [field]: parsed });
      setField("");
    } catch (error) {
      setFieldError(error.message);
    }
  }

  function applyJson() {
    try {
      const parsed = JSON.parse(rawDraft || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Overrides must be a JSON object.");
      commit(parsed);
      setRawDraft(null);
      setJsonError("");
      onPendingChange?.(node.signature, false);
    } catch (error) {
      setJsonError(error.message);
    }
  }

  return (
    <div className={`rule-node${changes.length ? " rule-node-changed" : ""}`}>
      <div className="rule-node-heading">
        <button type="button" className="rule-node-summary" aria-expanded={open} onClick={onToggle}>
          <ChevronRight className={open ? "chevron-open" : ""} size={16} aria-hidden="true" />
          <span className="rule-node-kind">{node.kind}</span>
          <strong>{node.name}</strong>
          <span className="rule-node-status">
            {changes.length > 0 && <Badge bg="warning" text="dark">{changes.length} changed {changes.length === 1 ? "option" : "options"}</Badge>}
            {!node.in_prod && <Badge bg="info">SQA only</Badge>}
            {!node.in_sqa && <Badge bg="secondary">Production only</Badge>}
            {node.sqa_source_version && <Badge bg="secondary">Active SQA v{node.sqa_source_version}</Badge>}
            {hasOverride && <Badge bg="success">{Object.keys(override).length} fixed</Badge>}
          </span>
        </button>
        <Form.Select size="sm" className="rule-source-select" aria-label={`Source for ${node.signature}`} value={sameInBoth(node) && !removed ? "same" : policy}
          onChange={(event) => onPolicyChange(node.signature, event.target.value)}>
          {sameInBoth(node) && <option value="same" disabled>Same in both</option>}
          <option value="prod" disabled={!node.in_prod}>Production</option>
          <option value="sqa" disabled={!node.in_sqa}>{node.sqa_source_version ? `SQA v${node.sqa_source_version}` : "SQA"}</option>
          <option value="remove">Remove item</option>
        </Form.Select>
      </div>
      <div className="rule-node-content" hidden={!open}>
        {changes.length > 0 && <div className="rule-change-reasons">
          {changes.map((key) => <div key={key}><code>{key}</code>: {differenceReason(node.prod_options?.[key], node.sqa_options?.[key])}</div>)}
          <Form.Check type="switch" id={`whitespace-${encodeURIComponent(node.signature)}`} label="Show whitespace" checked={showWhitespace} onChange={(event) => setShowWhitespace(event.target.checked)} />
        </div>}
        {fields.length > 0 ? (
          <Table responsive size="sm" className="rule-options-table">
            <thead><tr><th>Option</th><th>Production</th><th>{sqaLabel}</th><th>Status</th></tr></thead>
            <tbody>{fields.map((key) => (
              <tr key={key} className={changes.includes(key) ? "option-row-changed" : ""}>
                <th scope="row">{key}</th>
                <td><pre>{showWhitespace && typeof node.prod_options?.[key] === "string" ? visibleWhitespace(node.prod_options[key]) : displayValue(node.prod_options?.[key])}</pre></td>
                <td><pre>{showWhitespace && typeof node.sqa_options?.[key] === "string" ? visibleWhitespace(node.sqa_options[key]) : displayValue(node.sqa_options?.[key])}</pre></td>
                <td>{changes.includes(key) ? <Badge bg="warning" text="dark">Changed here</Badge>
                  : !node.in_prod ? "SQA only" : !node.in_sqa ? "Production only" : "Same"}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <p className="text-muted small">No options</p>}
        <fieldset className="rule-override-editor" disabled={removed}>
          <legend className="rule-options-heading">Fixed overrides {removed && <span className="text-muted">(item removed)</span>}</legend>
          {hasOverride && <div className="override-list">{Object.entries(override).map(([key, fixed]) => (
            <div className="override-item" key={key}>
              <code>{key}</code><span>{displayValue(fixed)}</span>
              <Button size="sm" variant="outline-danger" title={`Remove override for ${key}`} aria-label={`Remove override for ${key}`} disabled={jsonDirty}
                onClick={() => { const next = { ...override }; delete next[key]; commit(next); }}><Trash2 size={15} aria-hidden="true" /></Button>
            </div>
          ))}</div>}
          <div className="override-controls">
            <Form.Select size="sm" aria-label={`Override option for ${node.signature}`} value={field} disabled={jsonDirty} onChange={(event) => selectField(event.target.value)}>
              <option value="">Choose option...</option>
              {fields.map((key) => <option key={key} value={key}>{key}</option>)}
            </Form.Select>
            {field && <>
              <Form.Select size="sm" aria-label="Override value source" value={source} disabled={jsonDirty} onChange={(event) => setSource(event.target.value)}>
                <option value="custom">Custom value</option>
                {Object.hasOwn(node.prod_options || EMPTY, field) && <option value="prod">Production: {displayValue(node.prod_options[field])}</option>}
                {Object.hasOwn(node.sqa_options || EMPTY, field) && <option value="sqa">{sqaLabel}: {displayValue(node.sqa_options[field])}</option>}
              </Form.Select>
              {source === "custom" && <>
                <Form.Select size="sm" className="override-type" aria-label="Override value type" value={type} disabled={jsonDirty} onChange={(event) => {
                  const nextType = event.target.value;
                  setType(nextType);
                  setValue(nextType === "boolean" ? "false" : nextType === "array" ? "[]" : nextType === "object" ? "{}" : "");
                }}>
                  <option value="string">Text</option><option value="number">Number</option>
                  <option value="boolean">Boolean</option><option value="array">Array</option>
                  <option value="object">Object</option><option value="null">Null</option>
                </Form.Select>
                {type === "boolean" ? <Form.Check type="switch" label={value === "true" ? "True" : "False"} aria-label="Fixed boolean value" checked={value === "true"} disabled={jsonDirty} onChange={(event) => setValue(String(event.target.checked))} />
                  : type !== "null" && <Form.Control size="sm" as={type === "array" || type === "object" ? "textarea" : "input"} rows={2}
                    type={type === "number" ? "number" : "text"} step="any" aria-label="Fixed override value" value={value} disabled={jsonDirty} onChange={(event) => setValue(event.target.value)} />}
              </>}
              <Button size="sm" variant="outline-primary" disabled={jsonDirty} onClick={setFixedValue}><Plus size={15} aria-hidden="true" /> Set override</Button>
            </>}
          </div>
          {fieldError && <p role="alert" className="text-danger small mt-2 mb-0">{fieldError}</p>}
          <details className="override-json">
            <summary><Code2 size={15} aria-hidden="true" /> Advanced JSON</summary>
            <Form.Control as="textarea" rows={4} spellCheck={false} aria-label={`JSON overrides for ${node.signature}`}
              value={rawDraft ?? (hasOverride ? JSON.stringify(override, null, 2) : "{}")}
              onChange={(event) => {
                setRawDraft(event.target.value);
                setJsonError("");
                onPendingChange?.(node.signature, true);
              }} />
            {jsonError && <p role="alert" className="text-danger small mt-1">{jsonError}</p>}
            {jsonDirty && <div className="d-flex gap-2 mt-2 align-items-center">
              <Button size="sm" onClick={applyJson}>Apply JSON</Button>
              <Button size="sm" variant="outline-secondary" onClick={() => {
                setRawDraft(null); setJsonError(""); onPendingChange?.(node.signature, false);
              }}>Discard changes</Button>
              <span className="text-muted small">Not applied</span>
            </div>}
          </details>
        </fieldset>
      </div>
    </div>
  );
}
