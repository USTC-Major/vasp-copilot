"""Pure projections of existing action evidence; no store or execution loop."""

import posixpath

from .contracts import ToolboxError
from .ssh import file_helper as h

RESTRICTED = {"potcar", "large_vasp", "credential", "opaque"}


def namespace(endpoint):
    key = endpoint.get("host_key") or {}
    if key.get("verification") != "known_hosts" or not key.get("sha256"):
        raise ToolboxError("ENDPOINT_CHANGED", "远端主机身份未验证", 409)
    return tuple(endpoint.get(k) for k in ("host", "port", "username")) + (
        key.get("algorithm"),
        key["sha256"],
    )


def actions(snapshot):
    for task in snapshot.get("tasks", []):
        for action in (
            ((task.get("flow") or {}).get("consent") or {}).get("actions", {}).values()
        ):
            yield task, action


def legacy_unknown(snapshot):
    return any(
        a.get("kind") == "hpc_upload"
        and a.get("state") in {"executing", "unknown"}
        and not (a.get("binding") or {}).get("file_identity")
        for _, a in actions(snapshot)
    )


def under(path, prefix):
    return prefix == "" or path == prefix or path.startswith(prefix.rstrip("/") + "/")


def targets(manifest):
    roots = {r["root_id"]: r for r in manifest["roots"]}
    result = []
    for item in manifest["items"]:
        destination = item["destination"]
        root = roots[destination["root_id"]]
        path = posixpath.join(root["canonical_path"], destination["relative_path"])
        anchors = []
        for entry in destination["parent_chain"]:
            if under(path, entry["path"]):
                anchors.append(
                    (
                        entry["device"],
                        entry["inode"],
                        posixpath.relpath(path, entry["path"]),
                    )
                )
        result.append({"path": path, "anchors": anchors, "item_id": item["item_id"]})
    return result


def overlap(a, b):
    if under(a["path"], b["path"]) or under(b["path"], a["path"]):
        return True
    return any(
        x[:2] == y[:2] and (under(x[2], y[2]) or under(y[2], x[2]))
        for x in a.get("anchors", [])
        for y in b.get("anchors", [])
    )


def not_executed(action):
    return {
        item_id
        for item_id, result in (action.get("receipt") or {})
        .get("item_outcomes", {})
        .items()
        if result.get("state") == "not_executed"
    }


def occupied(snapshot, endpoint, *, protect_finished=False, exclude=None):
    wanted = namespace(endpoint)
    for _, action in actions(snapshot):
        if action.get("action_id") == exclude:
            continue
        binding = action.get("binding") or {}
        if action.get("kind") == "remote_file":
            manifest = binding.get("manifest") or {}
            if not manifest:
                continue
            if namespace(manifest["endpoint"]) != wanted:
                continue
            committed = {
                i["item_id"]
                for i in (action.get("receipt") or {}).get("items", [])
                if i.get("state") == "committed"
            }
            released = not_executed(action)
            for target in targets(manifest):
                item_id = target["item_id"]
                unresolved = (
                    action.get("state") in {"executing", "unknown"}
                    and item_id not in committed | released
                )
                if unresolved or (protect_finished and item_id in committed):
                    yield target
        elif action.get("kind") == "hpc_upload" and action.get("state") in {
            "executing",
            "unknown",
        }:
            identity = binding.get("file_identity")
            if identity and namespace(identity["endpoint"]) == wanted:
                yield from identity["targets"]


def provenance(snapshot, endpoint, observed):
    wanted = namespace(endpoint)
    candidates = []
    for _, action in actions(snapshot):
        if action.get("kind") != "remote_file":
            continue
        manifest = (action.get("binding") or {}).get("manifest") or {}
        if not manifest or namespace(manifest["endpoint"]) != wanted:
            continue
        records = []
        for item in manifest["items"]:
            source = item.get("source")
            if source and source.get("content_class") in RESTRICTED:
                records.append(
                    (source, source["content_class"], "source:" + action["action_id"])
                )
        for receipt in (action.get("receipt") or {}).get("items", []):
            fields = (
                ("target_evidence",)
                if receipt.get("state") == "committed"
                else ("temporary_evidence",)
            )
            for field in fields:
                if receipt.get(field):
                    records.append(
                        (
                            receipt[field],
                            receipt["content_class"],
                            receipt.get("remote_receipt_id"),
                        )
                    )
        for evidence, label, receipt_id in records:
            same_inode = h.identity_matches(observed, evidence)
            same_path = evidence.get("canonical_path") == observed["canonical_path"]
            if same_inode or same_path:
                if manifest["endpoint"] != endpoint:
                    raise ToolboxError(
                        "ENDPOINT_CHANGED",
                        "已知文件来自不同端点配置；须核对历史身份，不能降为外部来源",
                        409,
                    )
                if (
                    not same_inode
                    or not h.identity_matches(observed, evidence, stable=True)
                    or not receipt_id
                ):
                    raise ToolboxError(
                        "CONTENT_READ_DENIED",
                        "已知产物身份或版本已变化，禁止按外部普通文件读取",
                        403,
                    )
                candidates.append(
                    {
                        **observed,
                        "origin": "managed_output",
                        "content_class": label,
                        "receipt_id": receipt_id,
                        "manifest_digest": manifest["manifest_digest"],
                    }
                )
        if action.get("state") in {"executing", "unknown"}:
            if (
                any(
                    t["path"] == observed["canonical_path"]
                    and t["item_id"] not in not_executed(action)
                    for t in targets(manifest)
                )
                and not candidates
            ):
                raise ToolboxError(
                    "CONTENT_READ_DENIED",
                    "该目标有尚未核实的文件动作，暂不能读取正文",
                    403,
                )
    if candidates:
        result = candidates[0]
        result["content_class"] = h.strict_class(
            *(c["content_class"] for c in candidates)
        )
        result["expected_evidence"] = observed
        return result
    return {"origin": "external_source", "expected_evidence": observed}
