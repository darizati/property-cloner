"""Rule-tree flattening, diffing, policy application, and JSON-pointer removal.

This is the generic replacement for prod-check-mate-ab's per-behavior handler
files: instead of bespoke code per Akamai behavior, every behavior/criterion in
the tree gets a stable signature, and the caller supplies a policy
(prod/sqa/remove) plus optional fixed field overrides per signature.
"""
import copy
from typing import Any, Literal, Optional

Policy = Literal["prod", "sqa", "remove"]


def _rules_root(rule_tree_response: dict) -> dict:
    """PAPI's GET rules response wraps the tree under 'rules'; accept either shape."""
    return rule_tree_response.get("rules", rule_tree_response)


def flatten_rule_tree(rule_tree_response: dict) -> list[dict]:
    """Returns behavior, criterion, and variable nodes with stable signatures:
    '<rule name path>::<behavior|criterion|variable>::<name>#<occurrence>'.
    """
    out: list[dict] = []

    def walk(node: dict, path_names: list[str]) -> None:
        name = node.get("name", "default")
        path_names = path_names + [name]
        prefix = "/".join(path_names)
        counters: dict[str, int] = {}

        variable_counters: dict[str, int] = {}
        for variable in node.get("variables", []):
            variable_name = variable["name"]
            variable_counters[variable_name] = variable_counters.get(variable_name, 0) + 1
            out.append({
                "signature": f"{prefix}::variable::{variable_name}#{variable_counters[variable_name]}",
                "rule_path": f"{prefix}/Variables",
                "kind": "variable",
                "name": variable_name,
                "options": {key: value for key, value in variable.items() if key != "name"},
            })

        for beh in node.get("behaviors", []):
            bname = beh.get("name", "unknown")
            counters[bname] = counters.get(bname, 0) + 1
            out.append(
                {
                    "signature": f"{prefix}::behavior::{bname}#{counters[bname]}",
                    "rule_path": prefix,
                    "kind": "behavior",
                    "name": bname,
                    "options": beh.get("options", {}),
                }
            )

        crit_counters: dict[str, int] = {}
        for crit in node.get("criteria", []):
            cname = crit.get("name", "unknown")
            crit_counters[cname] = crit_counters.get(cname, 0) + 1
            out.append(
                {
                    "signature": f"{prefix}::criterion::{cname}#{crit_counters[cname]}",
                    "rule_path": prefix,
                    "kind": "criterion",
                    "name": cname,
                    "options": crit.get("options", {}),
                }
            )

        for child in node.get("children", []):
            walk(child, path_names)

    walk(_rules_root(rule_tree_response), [])
    return out


def summarize_rule_changes(before: Optional[dict], after: dict) -> dict:
    def indexed_nodes(tree):
        if tree is None:
            return {}
        result = {node["signature"]: node for node in flatten_rule_tree(tree)}

        def walk(rule, parents):
            path = parents + [rule.get("name", "default")]
            prefix = "/".join(path)
            metadata = {
                key: value for key, value in rule.items()
                if key not in {"name", "uuid", "variables", "behaviors", "criteria", "children"}
            }
            for collection in ("children", "behaviors", "criteria"):
                metadata[f"{collection} order"] = [item.get("name", "unknown") for item in rule.get(collection, [])]
            result[f"{prefix}::section"] = {
                "rule_path": prefix, "kind": "section", "name": rule.get("name", "default"),
                "options": metadata,
            }
            for child in rule.get("children", []):
                walk(child, path)

        walk(_rules_root(tree), [])
        return result

    old_nodes, new_nodes = indexed_nodes(before), indexed_nodes(after)
    changes = []
    counts = {"added": 0, "updated": 0, "removed": 0}
    for signature in dict.fromkeys([*old_nodes, *new_nodes]):
        old, new = old_nodes.get(signature), new_nodes.get(signature)
        if old is not None and new is not None and old["options"] == new["options"]:
            continue
        action = "added" if old is None else "removed" if new is None else "updated"
        node = new if new is not None else old
        old_options, new_options = (old or {}).get("options", {}), (new or {}).get("options", {})
        fields = sorted(
            key for key in old_options.keys() | new_options.keys()
            if key not in old_options or key not in new_options or old_options[key] != new_options[key]
        )
        counts[action] += 1
        changes.append({
            "action": action, "kind": node["kind"], "rule_path": node["rule_path"],
            "name": node["name"], "fields": fields,
        })
    return {"counts": counts, "changes": changes}


def diff_rule_sets(
    prod_tree: dict, sqa_tree: Optional[dict]
) -> list[dict]:
    """Combines flattened prod + SQA nodes into a single list with default policies:
    - only in prod -> default 'prod'
    - only in SQA (a pre-existing SQA-specific customization) -> default 'sqa'
    - in both -> default 'prod', flagged 'changed' if options differ
    """
    prod_nodes = flatten_rule_tree(prod_tree)
    sqa_nodes = flatten_rule_tree(sqa_tree) if sqa_tree else []
    sqa_by_sig = {n["signature"]: n for n in sqa_nodes}

    combined: list[dict] = []
    for n in prod_nodes:
        sqa_match = sqa_by_sig.get(n["signature"])
        combined.append(
            {
                **n,
                "prod_options": n["options"],
                "sqa_options": sqa_match["options"] if sqa_match else None,
                "in_prod": True,
                "in_sqa": sqa_match is not None,
                "changed": sqa_match is not None and sqa_match["options"] != n["options"],
                "default_policy": "prod",
            }
        )

    prod_sigs = {n["signature"] for n in prod_nodes}
    for n in sqa_nodes:
        if n["signature"] not in prod_sigs:
            combined.append(
                {
                    **n,
                    "prod_options": None,
                    "sqa_options": n["options"],
                    "in_prod": False,
                    "in_sqa": True,
                    "changed": False,
                    "default_policy": "sqa",
                }
            )
    return combined


def merge_variable_declarations(primary: list[dict], fallback: list[dict]) -> list[dict]:
    declarations = {variable["name"]: copy.deepcopy(variable) for variable in primary}
    for variable in fallback:
        if variable["name"] not in declarations:
            declarations[variable["name"]] = copy.deepcopy(variable)
    return list(declarations.values())


def apply_policies(
    prod_tree: dict,
    sqa_tree: Optional[dict],
    policies: dict[str, Policy],
    field_overrides: dict[str, dict[str, Any]],
) -> dict:
    """Merge both rule structures, apply item source/removal policies, and overrides."""
    sqa_signatures = {node["signature"] for node in flatten_rule_tree(sqa_tree)} if sqa_tree else set()
    missing_sqa = [signature for signature, policy in policies.items() if policy == "sqa" and signature not in sqa_signatures]
    if missing_sqa:
        raise ValueError(
            "Saved SQA choices are missing from the SQA rule tree; restore them or change their decisions "
            "before cloning: " + ", ".join(missing_sqa)
        )
    merged = copy.deepcopy(prod_tree)

    def indexed(items: list[dict]) -> dict[str, dict]:
        counters: dict[str, int] = {}
        result = {}
        for item in items:
            name = item.get("name", "unknown")
            counters[name] = counters.get(name, 0) + 1
            result[f"{name}#{counters[name]}"] = item
        return result

    def combined_keys(prod_items: dict, sqa_items: dict) -> list[str]:
        before: dict[str, list[str]] = {}
        pending: list[str] = []
        for key in sqa_items:
            if key in prod_items:
                before[key] = pending
                pending = []
            else:
                pending.append(key)
        result = []
        for key in prod_items:
            result.extend(before.get(key, []))
            result.append(key)
        return result + pending

    def apply_to_list(prod_items: list[dict], sqa_items: list[dict], kind: str, prefix: str) -> list[dict]:
        prod_by_name = indexed(prod_items)
        sqa_by_name = indexed(sqa_items)
        result: list[dict] = []
        for key in combined_keys(prod_by_name, sqa_by_name):
            sig = f"{prefix}::{kind}::{key}"
            policy = policies.get(sig, "prod" if key in prod_by_name else "sqa")
            if policy == "remove":
                continue
            source = sqa_by_name if policy == "sqa" else prod_by_name
            if key not in source:
                raise ValueError(f"Selected {policy.upper()} source is missing for {sig}")
            item = copy.deepcopy(source[key])
            overrides = field_overrides.get(sig)
            if overrides:
                if kind == "variable":
                    if "name" in overrides:
                        raise ValueError(f"Variable names cannot be changed through fixed overrides: {sig}")
                    item.update(copy.deepcopy(overrides))
                else:
                    item["options"] = {**item.get("options", {}), **overrides}
            result.append(item)
        return result

    def walk(prod_node: Optional[dict], sqa_node: Optional[dict], path_names: list[str]) -> dict:
        node = copy.deepcopy(prod_node if prod_node is not None else sqa_node)
        prod_node = prod_node or {}
        sqa_node = sqa_node or {}
        name = node.get("name", "default")
        path_names = path_names + [name]
        prefix = "/".join(path_names)

        for collection, kind in (("variables", "variable"), ("behaviors", "behavior"), ("criteria", "criterion")):
            if collection in prod_node or collection in sqa_node:
                node[collection] = apply_to_list(
                    prod_node.get(collection, []), sqa_node.get(collection, []), kind, prefix,
                )
        if "children" in prod_node or "children" in sqa_node:
            prod_children = indexed(prod_node.get("children", []))
            sqa_children = indexed(sqa_node.get("children", []))
            children = [
                walk(prod_children.get(key), sqa_children.get(key), path_names)
                for key in combined_keys(prod_children, sqa_children)
            ]
            node["children"] = [
                child for child in children
                if any(child.get(collection) for collection in ("variables", "behaviors", "criteria", "children"))
            ]
        return node

    root = walk(_rules_root(prod_tree), _rules_root(sqa_tree) if sqa_tree else None, [])
    if "rules" in merged:
        merged["rules"] = root
    else:
        merged = root
    return merged


def remove_by_json_pointer(rule_tree: dict, pointer: str) -> Optional[Any]:
    """Removes the item referenced by a PAPI activation error's `errorLocation`
    JSON pointer (e.g. '#/rules/children/2/behaviors/1') from the tree in place.
    Returns the removed item, or None if the pointer didn't resolve.
    """
    if pointer.startswith("#"):
        pointer = pointer[1:]
    parts = [p for p in pointer.split("/") if p != ""]
    if not parts:
        return None

    node: Any = rule_tree
    try:
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        last = parts[-1]
        if isinstance(node, list):
            return node.pop(int(last))
        return node.pop(last, None)
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def replace_hostname_strings(obj: Any, org_hostnames: list[str], prefix: str = "sqa-") -> bool:
    """Recursively rewrites production hostnames found in string values (e.g. in
    match criteria) to their SQA-prefixed equivalent. Returns True if anything changed.
    """
    changed = False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str):
                for host in org_hostnames:
                    if host in v and not v.startswith(prefix):
                        obj[k] = v.replace(host, f"{prefix}{host}")
                        changed = True
                        break
            else:
                changed = replace_hostname_strings(v, org_hostnames, prefix) or changed
    elif isinstance(obj, list):
        for idx, val in enumerate(obj):
            if isinstance(val, str):
                for host in org_hostnames:
                    if host in val and not val.startswith(prefix):
                        obj[idx] = val.replace(host, f"{prefix}{host}")
                        changed = True
                        break
            else:
                changed = replace_hostname_strings(val, org_hostnames, prefix) or changed
    return changed
