"""Independent real-filesystem review of the fixed remote file helper."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest

from backend.toolbox.ssh import file_helper as helper


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="requires real Linux fd/inode/renameat2 behavior; CI must execute it",
)


def _endpoint() -> dict:
    result = {
        "schema_version": 1,
        "host": "fixture.invalid",
        "port": 22,
        "username": "ci-review",
        "scheduler_target": {"scheduler": "fixture"},
        "host_key": {
            "algorithm": "fixture-key",
            "sha256": "11" * 32,
            "verification": "known_hosts",
        },
    }
    result["endpoint_digest"] = helper.digest(result)
    return result


def _root(path: Path, endpoint_digest: str, *, root_id: str = "root") -> dict:
    evidence = helper.root_evidence(str(path))
    return {
        **evidence,
        "root_id": root_id,
        "version": 1,
        "endpoint_digest": endpoint_digest,
    }


def _source(path: Path, endpoint_digest: str) -> dict:
    fd, evidence = helper.open_source(str(path))
    os.close(fd)
    return {**evidence, "endpoint_digest": endpoint_digest}


def _manifest(
    root_path: Path,
    *,
    action_id: str,
    operation: str,
    destination: str,
    source_path: Path | None = None,
    text: str | None = None,
    max_total_bytes: int | None = None,
) -> dict:
    endpoint = _endpoint()
    root = _root(root_path, endpoint["endpoint_digest"])
    source = _source(source_path, endpoint["endpoint_digest"]) if source_path else None
    item_id = "item"
    destination_evidence = helper.destination_evidence(root, destination)
    if operation in {"copy", "symlink"}:
        assert source is not None and text is None
        content_class = helper.strict_class(
            source["content_class"], helper.content_class(destination)
        )
        byte_count = (
            source["size"]
            if operation == "copy"
            else len(source["canonical_path"].encode("utf-8"))
        )
        provenance = {"origin": "external_source"}
        mode = None if operation == "symlink" else 0o600
    elif operation == "write_text":
        assert source is None and text is not None
        content_class = "normal_text"
        byte_count = len(text.encode("utf-8"))
        provenance = None
        mode = 0o600
    else:
        assert operation == "mkdir" and source is None and text is None
        content_class = "unclassified_external"
        byte_count = 0
        provenance = None
        mode = 0o700
    item = {
        "item_id": item_id,
        "op": operation,
        "source": source,
        "destination": destination_evidence,
        "text": text,
        "mode": mode,
        "on_conflict": "fail",
        "content_class": content_class,
        "names": helper.names(action_id, item_id),
        "bytes": byte_count,
        "source_provenance": provenance,
    }
    result = {
        "protocol_version": helper.PROTOCOL,
        "policy_version": helper.POLICY,
        "action_id": action_id,
        "project_id": "project",
        "task_id": "task",
        "job_key": "job",
        "attempt_id": "attempt",
        "scope_id": "scope",
        "scope_version": 1,
        "endpoint": endpoint,
        "roots": [root],
        "expires_at": "2099-01-01T00:00:00Z",
        "max_operations": 1,
        "max_total_bytes": byte_count if max_total_bytes is None else max_total_bytes,
        "items": [item],
    }
    result["manifest_digest"] = helper.digest(result)
    return result


def _permit(manifest: dict, prepared: dict) -> dict:
    item = manifest["items"][0]
    return {
        "action_id": manifest["action_id"],
        "manifest_digest": manifest["manifest_digest"],
        "item_id": item["item_id"],
        "scope_id": manifest["scope_id"],
        "scope_version": manifest["scope_version"],
        "job_key": manifest["job_key"],
        "attempt_id": manifest["attempt_id"],
        "endpoint_digest": manifest["endpoint"]["endpoint_digest"],
        "root_id": item["destination"]["root_id"],
        "root_version": item["destination"]["root_version"],
        "prepared_digest": prepared["prepared_digest"],
        "prepare_token": prepared["prepare_token"],
        "valid_until": "2099-01-01T00:00:00Z",
    }


def _sha256(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


class _HelperProcess:
    def __init__(self):
        source = Path(helper.__file__).read_text(encoding="utf-8")
        self.command = [sys.executable, "-I", "-u", "-c", source]
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.raw_replies: list[bytes] = []

    def request(self, payload: dict) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(helper.canonical(payload) + b"\n")
        self.process.stdin.flush()
        raw = self.process.stdout.readline(helper.MAX_REPLY + 2)
        self.raw_replies.append(raw)
        assert raw.endswith(b"\n") and len(raw) <= helper.MAX_REPLY + 1
        reply = json.loads(raw)
        assert reply.get("ok") is True, reply
        return reply["data"]

    def close(self) -> bytes:
        if self.process.poll() is None:
            try:
                self.request({"op": "abort"})
            except (AssertionError, BrokenPipeError):
                pass
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)
        assert self.process.stderr is not None
        stderr = self.process.stderr.read()
        assert self.process.returncode == 0, stderr.decode("utf-8", "replace")
        return stderr


@pytest.mark.posix_critical
def test_linux_helper_copy_prepare_commit_keeps_bytes_remote(
    tmp_path: Path, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root = tmp_path / "root"
    source_dir = tmp_path / "history outside root"
    root.mkdir()
    source_dir.mkdir()
    source = source_dir / "WAVECAR.snapshot"
    marker = b"S1B-REMOTE-CONTENT-DO-NOT-RETURN\x00"
    block = marker + bytes(1024 * 1024 - len(marker))
    expected = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(129):
            stream.write(block)
            expected.update(block)
    manifest = _manifest(
        root,
        action_id="1" * 32,
        operation="copy",
        source_path=source,
        destination="WAVECAR",
    )

    child = _HelperProcess()
    command_text = " ".join(child.command[:4])
    assert str(source) not in command_text and str(root) not in command_text
    ready = child.request({"op": "begin", "manifest": manifest, "remaining_seconds": 120})
    assert ready["manifest_digest"] == manifest["manifest_digest"]
    prepared = child.request({"op": "prepare_next"})
    target = root / "WAVECAR"
    assert prepared["state"] == "prepared" and not target.exists()
    committed = child.request({
        "op": "commit",
        "permit": _permit(manifest, prepared),
        "remaining_seconds": 30,
    })
    stderr = child.close()

    assert committed["state"] == "committed" and committed["published"] is True
    assert committed["bytes_processed"] == source.stat().st_size
    assert committed["sha256"] == expected.hexdigest() == _sha256(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    protocol_bytes = b"".join(child.raw_replies) + stderr
    assert marker not in protocol_bytes
    assert len(protocol_bytes) < 4 * helper.MAX_REPLY


@pytest.mark.posix_critical
def test_linux_noreplace_target_race_and_same_inode(
    tmp_path: Path, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root = tmp_path / "root"
    history = tmp_path / "history"
    root.mkdir()
    history.mkdir()
    source = history / "CHGCAR"
    source.write_bytes(b"trusted-source")

    manifest = _manifest(
        root,
        action_id="2" * 32,
        operation="copy",
        source_path=source,
        destination="CHGCAR",
    )
    transaction = helper.FileTransaction(manifest, remaining_seconds=60)
    prepared = transaction.prepare_next()
    target = root / "CHGCAR"
    target.write_bytes(b"racing-writer")
    before = target.stat()
    with pytest.raises(helper.FileError) as conflict:
        transaction.commit(_permit(manifest, prepared), remaining_seconds=30)
    after = target.stat()
    assert conflict.value.code == "DESTINATION_CONFLICT"
    assert target.read_bytes() == b"racing-writer"
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    transaction.close()

    same = root / "same"
    os.link(source, same)
    same_manifest = _manifest(
        root,
        action_id="3" * 32,
        operation="copy",
        source_path=source,
        destination="same",
    )
    same_tx = helper.FileTransaction(same_manifest, remaining_seconds=60)
    with pytest.raises(helper.FileError) as equal:
        same_tx.prepare_next()
    assert equal.value.code == "SOURCE_EQUALS_DESTINATION"
    assert same.read_bytes() == b"trusted-source"
    same_tx.close()


@pytest.mark.posix_critical
def test_linux_source_parent_and_root_changes_do_not_publish(
    tmp_path: Path, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root = tmp_path / "root"
    parent = root / "job"
    history = tmp_path / "history"
    parent.mkdir(parents=True)
    history.mkdir()
    source = history / "WAVECAR"
    source.write_bytes(b"original-source")

    source_manifest = _manifest(
        root,
        action_id="4" * 32,
        operation="copy",
        source_path=source,
        destination="job/WAVECAR",
    )
    source_tx = helper.FileTransaction(source_manifest, remaining_seconds=60)
    prepared = source_tx.prepare_next()
    source.write_bytes(b"changed--source")
    with pytest.raises(helper.FileError) as changed:
        source_tx.commit(_permit(source_manifest, prepared), remaining_seconds=30)
    assert changed.value.code == "SOURCE_CHANGED"
    assert not (parent / "WAVECAR").exists()
    source_tx.close()

    source.write_bytes(b"stable-source")
    parent_manifest = _manifest(
        root,
        action_id="5" * 32,
        operation="copy",
        source_path=source,
        destination="job/WAVECAR",
    )
    parent_tx = helper.FileTransaction(parent_manifest, remaining_seconds=60)
    parent_prepared = parent_tx.prepare_next()
    moved = root / "job-moved"
    parent.rename(moved)
    parent.mkdir()
    with pytest.raises(helper.FileError) as replaced:
        parent_tx.commit(_permit(parent_manifest, parent_prepared), remaining_seconds=30)
    assert replaced.value.code in {"ROOT_CHANGED", "ACTION_UNKNOWN"}
    assert not (parent / "WAVECAR").exists()
    assert not (moved / "WAVECAR").exists()
    parent_tx.close()

    root2 = tmp_path / "root-replace"
    root2.mkdir()
    root_manifest = _manifest(
        root2,
        action_id="9" * 32,
        operation="copy",
        source_path=source,
        destination="WAVECAR",
    )
    root_tx = helper.FileTransaction(root_manifest, remaining_seconds=60)
    root_prepared = root_tx.prepare_next()
    old_root = tmp_path / "root-replace-moved"
    root2.rename(old_root)
    root2.mkdir()
    with pytest.raises(helper.FileError) as root_changed:
        root_tx.commit(_permit(root_manifest, root_prepared), remaining_seconds=30)
    assert root_changed.value.code in {"ROOT_CHANGED", "ACTION_UNKNOWN"}
    assert not (root2 / "WAVECAR").exists()
    assert not (old_root / "WAVECAR").exists()
    root_tx.close()


@pytest.mark.posix_critical
def test_linux_content_policy_modes_and_capability_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, required_linux_capabilities: dict
):
    assert required_linux_capabilities and helper.probe()["supported"] is True
    root = tmp_path / "root"
    history = tmp_path / "history"
    root.mkdir()
    history.mkdir()

    with pytest.raises(helper.FileError) as traversal:
        helper.root_evidence(str(root) + "/.")
    assert traversal.value.code == "PATH_OUTSIDE_ROOT"

    previous = history / "previous"
    previous.mkdir()
    wavecar = previous / "WAVECAR"
    wavecar.write_bytes(b"wave")
    link_dir = history / "current"
    link_dir.mkdir()
    (link_dir / "WAVECAR").symlink_to("../previous/WAVECAR")
    fd, linked = helper.open_source(str(link_dir / "WAVECAR"))
    os.close(fd)
    assert linked["canonical_path"] == str(wavecar)

    sensitive = tmp_path / ".ssh"
    sensitive.mkdir()
    secret = sensitive / "id_rsa"
    secret.write_text("PRIVATE", encoding="utf-8")
    for view in ("stat", "list", "text"):
        with pytest.raises(helper.FileError) as denied:
            helper.inspect(
                str(secret if view != "list" else sensitive),
                view,
                provenance={"origin": "external_source"},
            )
        assert denied.value.code == "CONTENT_READ_DENIED"

    potcar = history / "POTCAR"
    potcar.write_text("not-for-preview", encoding="utf-8")
    assert helper.inspect(str(potcar), "stat")["content_class"] == "potcar"
    with pytest.raises(helper.FileError) as potcar_text:
        helper.inspect(str(potcar), "text", provenance={"origin": "external_source"})
    assert potcar_text.value.code == "CONTENT_READ_DENIED"

    write_manifest = _manifest(
        root,
        action_id="6" * 32,
        operation="write_text",
        text="#!/bin/sh\necho saved-only\n",
        destination="submit.sh",
    )
    write_tx = helper.FileTransaction(write_manifest, remaining_seconds=60)
    write_prepared = write_tx.prepare_next()
    write_permit = _permit(write_manifest, write_prepared)
    write_tx.commit(write_permit, remaining_seconds=30)
    script = root / "submit.sh"
    first_identity = (script.stat().st_dev, script.stat().st_ino)
    with pytest.raises(helper.FileError) as repeated:
        write_tx.commit(write_permit, remaining_seconds=30)
    assert repeated.value.code == "PROTOCOL_ERROR"
    assert (script.stat().st_dev, script.stat().st_ino) == first_identity
    write_tx.close()
    assert script.read_text(encoding="utf-8") == "#!/bin/sh\necho saved-only\n"
    assert stat.S_IMODE(script.stat().st_mode) == 0o600

    link_source = history / "CHGCAR"
    link_source.write_bytes(b"linked-content")
    link_source.chmod(0o640)
    source_mode = stat.S_IMODE(link_source.stat().st_mode)
    link_manifest = _manifest(
        root,
        action_id="a" * 32,
        operation="symlink",
        source_path=link_source,
        destination="CHGCAR.link",
    )
    link_tx = helper.FileTransaction(link_manifest, remaining_seconds=60)
    link_prepared = link_tx.prepare_next()
    link_tx.commit(_permit(link_manifest, link_prepared), remaining_seconds=30)
    link_tx.close()
    linked = root / "CHGCAR.link"
    assert linked.is_symlink() and os.readlink(linked) == str(link_source)
    assert stat.S_IMODE(link_source.stat().st_mode) == source_mode

    potcar = history / "POTCAR"
    potcar.write_text("not-for-preview", encoding="utf-8")
    potcar_manifest = _manifest(
        root,
        action_id="b" * 32,
        operation="copy",
        source_path=potcar,
        destination="renamed.txt",
    )
    assert potcar_manifest["items"][0]["content_class"] == "potcar"
    potcar_tx = helper.FileTransaction(potcar_manifest, remaining_seconds=60)
    potcar_prepared = potcar_tx.prepare_next()
    potcar_committed = potcar_tx.commit(
        _permit(potcar_manifest, potcar_prepared), remaining_seconds=30
    )
    potcar_tx.close()
    provenance = {
        **potcar_committed["target_evidence"],
        "origin": "managed_output",
        "content_class": potcar_committed["content_class"],
        "receipt_id": potcar_committed["remote_receipt_id"],
        "manifest_digest": potcar_manifest["manifest_digest"],
        "endpoint_digest": potcar_manifest["endpoint"]["endpoint_digest"],
    }
    with pytest.raises(helper.FileError) as renamed_potcar:
        helper.inspect(
            str(root / "renamed.txt"),
            "text",
            provenance=provenance,
            endpoint_digest=potcar_manifest["endpoint"]["endpoint_digest"],
        )
    assert renamed_potcar.value.code == "CONTENT_READ_DENIED"

    clock = [100.0]
    expiry_root = tmp_path / "expiry"
    expiry_root.mkdir()
    expiry_manifest = _manifest(
        expiry_root,
        action_id="c" * 32,
        operation="write_text",
        text="expires",
        destination="expiry.txt",
    )
    expiry_tx = helper.FileTransaction(
        expiry_manifest, remaining_seconds=60, clock=lambda: clock[0]
    )
    expiry_prepared = expiry_tx.prepare_next()
    clock[0] += 31
    with pytest.raises(helper.FileError) as expired:
        expiry_tx.commit(_permit(expiry_manifest, expiry_prepared), remaining_seconds=30)
    assert expired.value.code == "SCOPE_EXPIRED"
    assert not (expiry_root / "expiry.txt").exists()
    expiry_tx.close()

    short_clock = [200.0]
    short_root = tmp_path / "short-ttl"
    short_root.mkdir()
    short_manifest = _manifest(
        short_root,
        action_id="e" * 32,
        operation="write_text",
        text="short",
        destination="short.txt",
    )
    short_tx = helper.FileTransaction(
        short_manifest, remaining_seconds=60, clock=lambda: short_clock[0]
    )
    short_prepared = short_tx.prepare_next()
    original_target_absent = short_tx._target_absent

    def delay_before_final_publication_check(*args, **kwargs):
        original_target_absent(*args, **kwargs)
        short_clock[0] += 2

    short_tx._target_absent = delay_before_final_publication_check
    with pytest.raises(helper.FileError) as short_expired:
        short_tx.commit(
            _permit(short_manifest, short_prepared), remaining_seconds=1
        )
    assert short_expired.value.code == "SCOPE_EXPIRED"
    assert not (short_root / "short.txt").exists()
    short_tx.close()

    fail_root = tmp_path / "unsupported"
    fail_root.mkdir()
    fail_manifest = _manifest(
        fail_root,
        action_id="7" * 32,
        operation="mkdir",
        destination="child",
    )
    monkeypatch.setattr(helper, "probe", lambda: {"supported": False})
    with pytest.raises(helper.FileError) as unavailable:
        helper.FileTransaction(fail_manifest, remaining_seconds=60)
    assert unavailable.value.code == "REMOTE_CAPABILITY_UNAVAILABLE"
    assert not (fail_root / "child").exists()
    assert not any(path.name.startswith(helper.RESERVED) for path in fail_root.iterdir())


@pytest.mark.posix_critical
def test_linux_publish_receipt_failure_and_prepared_only_reconcile_unknown(
    tmp_path: Path, required_linux_capabilities: dict
):
    assert required_linux_capabilities
    root = tmp_path / "root"
    root.mkdir()
    manifest = _manifest(
        root,
        action_id="d" * 32,
        operation="write_text",
        text="published exactly once",
        destination="result.txt",
    )
    transaction = helper.FileTransaction(manifest, remaining_seconds=60)
    prepared = transaction.prepare_next()
    before_publish = helper.reconcile(manifest)
    assert before_publish["state"] == "unknown"
    assert all(item["state"] == "unknown" for item in before_publish["items"])

    original_receipt = transaction._receipt
    committed_name = manifest["items"][0]["names"]["committed_name"]

    def fail_committed_receipt(root_id, name, value):
        if name == committed_name:
            raise OSError("injected committed receipt failure")
        return original_receipt(root_id, name, value)

    transaction._receipt = fail_committed_receipt
    with pytest.raises(helper.FileError) as uncertain:
        transaction.commit(_permit(manifest, prepared), remaining_seconds=30)
    assert uncertain.value.code == "ACTION_UNKNOWN"
    assert uncertain.value.published is True
    assert (root / "result.txt").read_text(encoding="utf-8") == "published exactly once"

    after_failure = helper.reconcile(manifest)
    assert after_failure["state"] == "unknown" and after_failure["read_only"] is True
    assert all(item["state"] == "unknown" for item in after_failure["items"])
    assert (root / "result.txt").read_text(encoding="utf-8") == "published exactly once"
    transaction.close()
