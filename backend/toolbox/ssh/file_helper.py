"""Fixed, standard-library remote file protocol. No shell or scheduler operations.

This file is sent as product code to ``python3 -I -u -c``. Request data is
strictly JSON on stdin; importing it performs no filesystem operations.
"""
import ctypes
import errno
import hashlib
import json
import os
import posixpath
import re
import select
import stat
import sys
import time
import uuid

PROTOCOL = 1
POLICY = "remote-files-v1"
MAX_FRAME = 1024 * 1024
MAX_REPLY = 64 * 1024
TEXT_LIMIT = 12000
CHUNK = 1024 * 1024
RESERVED = ".vasp-doctor-"
CREDENTIAL_PARTS = ("/.ssh", "/.gnupg", "/.aws", "/.azure", "/.codex", "/.config", "/.vasp-ai")
TEXT_NAMES = {"INCAR", "POSCAR", "CONTCAR", "KPOINTS", "OUTCAR", "OSZICAR", "IBZKPT", "EIGENVAL", "DOSCAR", "PROCAR", "XDATCAR", "VASPRUN.XML"}
TEXT_SUFFIXES = {".txt", ".log", ".out", ".sh"}
CLASSES = {"unclassified_external", "normal_text", "opaque", "potcar", "large_vasp", "credential"}


class FileError(Exception):
    def __init__(self, code, message, *, stage="validate", published=False, leftovers=None):
        super().__init__(message)
        self.code, self.stage, self.published = code, stage, published
        self.leftovers = leftovers or []

    def payload(self):
        return {"code": self.code, "message": str(self), "retryable": False,
                "stage": self.stage, "published": self.published, "leftovers": self.leftovers}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition, code="INVALID_FILE_REQUEST", message="Invalid file request"):
    if not condition:
        raise FileError(code, message)


def integer(value, minimum=0, maximum=None):
    require(type(value) is int and value >= minimum and (maximum is None or value <= maximum))
    return value


def absolute(path):
    require(isinstance(path, str) and path.startswith("/") and "\x00" not in path
            and not any(ord(c) < 32 for c in path) and len(path.encode("utf-8")) <= 4096)
    require(not any(part in {".", ".."} for part in path.split("/")), "PATH_OUTSIDE_ROOT", "Absolute inputs cannot contain dot traversal components")
    return posixpath.normpath(path)


def relative(path):
    require(isinstance(path, str) and path and len(path.encode("utf-8")) <= 4096)
    parts = path.split("/")
    require(all(p and p not in {".", ".."} and not p.startswith(RESERVED) for p in parts),
            "PATH_OUTSIDE_ROOT", "Destination must use ordinary relative components")
    require(not any(c in path for c in "\x00\\$`*?[]{}~") and not any(ord(c) < 32 for c in path),
            "PATH_OUTSIDE_ROOT", "Destination contains forbidden expansion or control characters")
    return parts


def credential(path):
    low = path.replace("\\", "/").lower()
    name = posixpath.basename(low)
    return (any(part in low for part in CREDENTIAL_PARTS)
            or name.startswith((".env", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"))
            or name in {"config.json", "credentials", "credentials.json"}
            or any(word in name for word in ("private_key", "secret", "credential"))
            or posixpath.splitext(name)[1] in {".pem", ".key", ".p12", ".pfx"})


def content_class(path):
    name = posixpath.basename(path).upper()
    if credential(path):
        return "credential"
    if re.fullmatch(r"POTCAR(?:[._-].*)?", name):
        return "potcar"
    if re.fullmatch(r"(?:WAVE|CHG)CAR(?:[._-].*)?", name):
        return "large_vasp"
    return "unclassified_external"


def strict_class(*values):
    for value in values:
        require(value in CLASSES)
    for value in ("credential", "potcar", "large_vasp", "opaque", "unclassified_external", "normal_text"):
        if value in values:
            return value
    return "unclassified_external"


def names(action_id, item_id=None):
    require(isinstance(action_id, str) and re.fullmatch(r"[0-9a-f]{32}", action_id))
    result = {"action_directory": RESERVED + "action-" + action_id}
    if item_id is not None:
        require(isinstance(item_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item_id))
        suffix = hashlib.sha256(item_id.encode()).hexdigest()[:24]
        base = RESERVED + action_id + "-" + suffix
        result.update(staging_name=base + ".tmp", capability_probe_names=[base + ".probe-a", base + ".probe-b", base + ".probe-c"],
                      prepared_name=suffix + ".prepared.json", committed_name=suffix + ".committed.json")
    return result


def file_type(mode):
    for test, name in ((stat.S_ISREG, "file"), (stat.S_ISDIR, "directory"), (stat.S_ISLNK, "symlink")):
        if test(mode):
            return name
    return "special"


def metadata(st):
    return {"device": st.st_dev, "inode": st.st_ino, "type": file_type(st.st_mode),
            "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns,
            "mode": stat.S_IMODE(st.st_mode)}


def identity_matches(actual, expected, *, stable=False):
    fields = ("device", "inode", "type") + (("size", "mtime_ns", "ctime_ns") if stable else ())
    return all(actual.get(k) == expected.get(k) for k in fields)


def resolve_source(path):
    """Resolve each link explicitly so intermediate credential targets are denied."""
    require(not credential(path), "CONTENT_READ_DENIED", "Credential paths are not accessible")
    original = absolute(path)
    pending = original.split("/")[1:]
    resolved, chain, hops = [], [], 0
    while pending:
        part = pending.pop(0)
        if not part or part == ".":
            continue
        if part == "..":
            if resolved:
                resolved.pop()
            continue
        candidate = "/" + "/".join(resolved + [part])
        require(not credential(candidate), "CONTENT_READ_DENIED", "Credential resolution is denied")
        st = os.lstat(candidate)
        if stat.S_ISLNK(st.st_mode):
            hops += 1
            require(hops <= 40, "PATH_SYMLINK_ESCAPE", "Too many symbolic links")
            target = os.readlink(candidate)
            target_path = target if target.startswith("/") else posixpath.join(posixpath.dirname(candidate), target)
            require(not credential(target_path), "CONTENT_READ_DENIED", "Credential link target is denied")
            chain.append({"path": candidate, "target": target, **metadata(st)})
            if target.startswith("/"):
                resolved = []
            pending = target.split("/") + pending
        else:
            resolved.append(part)
    return "/" + "/".join(resolved), chain


def open_directory(path):
    """Walk from / using directory descriptors and O_NOFOLLOW at every step."""
    parts = absolute(path).split("/")[1:]
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    chain = [{"path": "/", **metadata(os.fstat(fd))}]
    current = ""
    try:
        for part in parts:
            if not part:
                continue
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            current += "/" + part
            chain.append({"path": current, **metadata(os.fstat(fd))})
        return fd, chain
    except BaseException:
        os.close(fd)
        raise


def open_source(path):
    canonical_path, links = resolve_source(path)
    parent, _ = open_directory(posixpath.dirname(canonical_path))
    try:
        fd = os.open(posixpath.basename(canonical_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        info = metadata(os.fstat(fd))
        require(info["type"] == "file", "SOURCE_TYPE_DENIED", "Only ordinary files are supported")
        return fd, {"requested_path": path, "canonical_path": canonical_path, **info,
                    "link_target": links[0]["target"] if links else None, "resolution_chain": links,
                    "content_class": strict_class(content_class(path), content_class(canonical_path)),
                    "verification_level": "metadata", "sha256": None,
                    "source_write_risk": "possible" if info["mode"] & 0o222 else "unknown"}
    except BaseException:
        os.close(fd)
        raise


def provenance_class(info, provenance, endpoint_digest):
    require(isinstance(provenance, dict) and provenance.get("origin") in {"external_source", "managed_output"},
            "CONTENT_READ_DENIED", "Trusted owner provenance resolution is required")
    if provenance["origin"] == "external_source":
        require(set(provenance) == {"origin"}, "CONTENT_READ_DENIED", "External classification cannot override protected labels")
        return info["content_class"]
    require(provenance.get("content_class") in CLASSES and provenance.get("receipt_id")
            and provenance.get("manifest_digest") and provenance.get("endpoint_digest") == endpoint_digest
            and provenance.get("canonical_path") == info["canonical_path"]
            and identity_matches(info, provenance, stable=True), "CONTENT_READ_DENIED", "Managed output provenance is absent or stale")
    return strict_class(info["content_class"], provenance["content_class"])


def inspect(path, view="stat", *, provenance=None, endpoint_digest="", limit=200, cursor=None):
    canonical_path, links = resolve_source(path)
    if view == "list":
        integer(limit, 1, 500)
        fd, _ = open_directory(canonical_path)
        try:
            info = metadata(os.fstat(fd))
            offset = 0
            if cursor is not None:
                require(isinstance(cursor, dict) and identity_matches(info, cursor, stable=True), "SOURCE_CHANGED", "Directory pagination evidence changed")
                offset = integer(cursor.get("offset"))
            result = {"view": "list", "canonical_path": canonical_path, "metadata": info, "entries": [], "next_cursor": None}
            with os.scandir(fd) as entries:
                index = 0
                for entry in entries:
                    if index < offset:
                        index += 1
                        continue
                    denied = credential(posixpath.join(canonical_path, entry.name))
                    item = {"name": entry.name, "restricted": denied}
                    if not denied:
                        item.update(metadata(entry.stat(follow_symlinks=False)))
                    next_cursor = {**info, "offset": index + 1}
                    candidate = {**result, "entries": result["entries"] + [item], "next_cursor": next_cursor}
                    if len(canonical({"ok": True, "data": candidate})) > MAX_REPLY - 1024:
                        if not result["entries"]:
                            result["entries"].append({"error": "ENTRY_TOO_LARGE", "index": index})
                            index += 1
                        result["next_cursor"] = {**info, "offset": index}
                        break
                    result["entries"].append(item)
                    index += 1
                    if len(result["entries"]) >= limit:
                        result["next_cursor"] = next_cursor
                        break
            require(identity_matches(metadata(os.fstat(fd)), info, stable=True), "SOURCE_CHANGED", "Directory changed while listing")
            return result
        finally:
            os.close(fd)
    require(view in {"stat", "text"})
    if view == "stat":
        parent, _ = open_directory(posixpath.dirname(canonical_path))
        try:
            info = metadata(os.stat(posixpath.basename(canonical_path) or ".", dir_fd=parent, follow_symlinks=False))
        finally:
            os.close(parent)
        return {"requested_path": path, "canonical_path": canonical_path, **info, "resolution_chain": links,
                "link_target": links[0]["target"] if links else None, "content_class": strict_class(content_class(path), content_class(canonical_path)),
                "verification_level": "metadata", "sha256": None}
    fd, info = open_source(path)
    try:
        label = provenance_class(info, provenance, endpoint_digest)
        require(label not in {"potcar", "large_vasp", "credential", "opaque"}, "CONTENT_READ_DENIED", "This content class cannot be previewed")
        name = posixpath.basename(canonical_path)
        require(name.upper() in TEXT_NAMES or posixpath.splitext(name.lower())[1] in TEXT_SUFFIXES,
                "CONTENT_READ_DENIED", "Unsupported text type")
        require(info["size"] <= TEXT_LIMIT, "CONTENT_READ_DENIED", "Text preview exceeds byte limit")
        data = os.read(fd, TEXT_LIMIT + 1)
        require(len(data) <= TEXT_LIMIT and b"\x00" not in data, "CONTENT_READ_DENIED", "Text preview is not bounded UTF-8")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise FileError("CONTENT_READ_DENIED", "Text preview is not UTF-8")
        require(not any(ord(c) < 32 and c not in "\n\r\t" for c in text), "CONTENT_READ_DENIED", "Binary control characters are denied")
        require(identity_matches(metadata(os.fstat(fd)), info, stable=True), "SOURCE_CHANGED", "Source changed while reading")
        return {**info, "content_class": label, "text": text, "truncated": False}
    finally:
        os.close(fd)


def root_evidence(path):
    canonical_path, links = resolve_source(path)
    fd, chain = open_directory(canonical_path)
    try:
        return {"requested_path": path, "canonical_path": canonical_path,
                "identity": metadata(os.fstat(fd)), "ancestors": chain, "resolution_chain": links}
    finally:
        os.close(fd)


def rename_noreplace(parent_fd, source, target):
    try:
        function = ctypes.CDLL(None, use_errno=True).renameat2
    except (OSError, AttributeError):
        raise FileError("REMOTE_CAPABILITY_UNAVAILABLE", "renameat2 is unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileError("DESTINATION_CONFLICT", "Destination already exists", stage="publish")
        if code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise FileError("REMOTE_CAPABILITY_UNAVAILABLE", "Filesystem does not support no-replace publication", stage="publish")
        raise FileError("ACTION_UNKNOWN", "Publication outcome requires read-only reconciliation", stage="publish", published="unknown")


def probe():
    supported = (sys.platform.startswith("linux") and sys.version_info >= (3, 9)
                 and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "fchmod", "fsync"))
                 and all(fn in os.supports_dir_fd for fn in (os.open, os.stat, os.mkdir, os.unlink, os.rmdir, os.readlink, os.symlink)))
    try:
        supported = supported and hasattr(ctypes.CDLL(None), "renameat2")
    except OSError:
        supported = False
    return {"protocol_version": PROTOCOL, "policy_version": POLICY, "supported": bool(supported),
            "platform": sys.platform, "python_version": list(sys.version_info[:3]),
            "filesystem_noreplace": "unverified", "read_only": True}


def validated_manifest(manifest):
    require(isinstance(manifest, dict))
    expected = {"protocol_version", "policy_version", "action_id", "project_id", "task_id", "job_key", "attempt_id", "scope_id", "scope_version", "endpoint", "roots", "expires_at", "max_operations", "max_total_bytes", "items", "manifest_digest"}
    require(set(manifest) == expected)
    require(manifest["protocol_version"] == PROTOCOL and manifest["policy_version"] == POLICY)
    require(manifest["manifest_digest"] == digest({k: v for k, v in manifest.items() if k != "manifest_digest"}))
    names(manifest["action_id"])
    for field in ("project_id", "task_id", "job_key", "attempt_id", "scope_id", "expires_at"):
        require(isinstance(manifest[field], str) and 0 < len(manifest[field]) <= 256)
    integer(manifest["scope_version"], 1)
    integer(manifest["max_operations"], 1, 32)
    integer(manifest["max_total_bytes"])
    require(isinstance(manifest["items"], list) and 0 < len(manifest["items"]) <= manifest["max_operations"], "BUDGET_EXCEEDED", "Operation budget exceeded")
    roots = {root["root_id"]: root for root in manifest["roots"]}
    require(len(roots) == len(manifest["roots"]) and roots)
    endpoint = manifest["endpoint"]
    require(endpoint.get("endpoint_digest") == digest({k: v for k, v in endpoint.items() if k != "endpoint_digest"}))
    require((endpoint.get("host_key") or {}).get("verification") == "known_hosts", "ENDPOINT_CHANGED", "Verified SSH host identity is required")
    total, seen = 0, set()
    for root in roots.values():
        require(root["endpoint_digest"] == endpoint["endpoint_digest"])
        integer(root["version"], 1)
        absolute(root["canonical_path"])
    for item in manifest["items"]:
        require(set(item) == {"item_id", "op", "source", "destination", "text", "mode", "on_conflict", "content_class", "names", "bytes", "source_provenance"})
        require(item["item_id"] not in seen)
        seen.add(item["item_id"])
        require(item["names"] == names(manifest["action_id"], item["item_id"]))
        require(item["op"] in {"copy", "symlink", "write_text", "mkdir"} and item["on_conflict"] == "fail")
        dest = item["destination"]
        relative(dest["relative_path"])
        require(dest["root_id"] in roots and dest["root_version"] == roots[dest["root_id"]]["version"])
        require(not credential(posixpath.join(roots[dest["root_id"]]["canonical_path"], dest["relative_path"])),
                "CONTENT_READ_DENIED", "Credential destination denied")
        require(item["mode"] == (None if item["op"] == "symlink" else 0o700 if item["op"] == "mkdir" else 0o600))
        require(item["content_class"] in CLASSES and item["content_class"] != "credential", "CONTENT_READ_DENIED", "Credential writes are denied")
        source, text = item["source"], item["text"]
        if item["op"] in {"copy", "symlink"}:
            require(isinstance(source, dict) and source["type"] == "file" and text is None)
            require(source["endpoint_digest"] == endpoint["endpoint_digest"])
            require(not credential(source["requested_path"]) and not credential(source["canonical_path"]), "CONTENT_READ_DENIED", "Credential source denied")
            expected_bytes = integer(source["size"]) if item["op"] == "copy" else len(source["canonical_path"].encode("utf-8"))
            require(item["content_class"] == strict_class(source["content_class"], content_class(dest["relative_path"])))
        elif item["op"] == "write_text":
            require(source is None and isinstance(text, str) and len(text.encode("utf-8")) <= TEXT_LIMIT)
            require(content_class(dest["relative_path"]) not in {"potcar", "credential", "large_vasp"}, "CONTENT_READ_DENIED", "POTCAR/large VASP text creation is denied")
            expected_bytes = len(text.encode("utf-8"))
        else:
            require(source is None and text is None)
            expected_bytes = 0
        require(item["bytes"] == expected_bytes)
        total += expected_bytes
    require(total <= manifest["max_total_bytes"], "BUDGET_EXCEEDED", "Byte budget exceeded")
    require(len(canonical(manifest)) <= MAX_FRAME)
    return manifest


def map_error(exc, stage="prepare"):
    if isinstance(exc, FileError):
        return exc
    if isinstance(exc, OSError):
        code = {errno.ENOENT: "SOURCE_NOT_FOUND", errno.EACCES: "REMOTE_PERMISSION_DENIED", errno.EPERM: "REMOTE_PERMISSION_DENIED",
                errno.ELOOP: "PATH_SYMLINK_ESCAPE", errno.ENOTDIR: "PATH_SYMLINK_ESCAPE", errno.EEXIST: "DESTINATION_CONFLICT"}.get(exc.errno, "REMOTE_IO_ERROR")
        return FileError(code, "Filesystem operation failed (errno=%s)" % exc.errno, stage=stage)
    return FileError("PROTOCOL_ERROR", "Invalid file protocol request", stage=stage)


def verify_root(root):
    actual = root_evidence(root["requested_path"])
    require(actual["canonical_path"] == root["canonical_path"]
            and identity_matches(actual["identity"], root["identity"])
            and actual["resolution_chain"] == root.get("resolution_chain", [])
            and len(actual["ancestors"]) == len(root["ancestors"])
            and all(a["path"] == b["path"] and identity_matches(a, b)
                    for a, b in zip(actual["ancestors"], root["ancestors"])),
            "ROOT_CHANGED", "Root path or identity changed")
    return actual


def destination_evidence(root, path):
    verify_root(root)
    parts = relative(path)
    require(not credential(posixpath.join(root["canonical_path"], path)), "CONTENT_READ_DENIED", "Credential destination denied")
    fd, _ = open_directory(root["canonical_path"])
    chain, current = [], root["canonical_path"]
    missing = []
    try:
        for index, part in enumerate(parts[:-1]):
            try:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                missing = parts[index:-1]
                break
            os.close(fd)
            fd = nxt
            current = posixpath.join(current, part)
            chain.append({"path": current, **metadata(os.fstat(fd))})
        exists = None
        if not missing:
            try:
                exists = metadata(os.stat(parts[-1], dir_fd=fd, follow_symlinks=False))
            except FileNotFoundError:
                pass
        return {"root_id": root["root_id"], "root_version": root["version"], "relative_path": path,
                "parent_chain": chain, "missing_components": missing, "parent_item_id": None,
                "target_exists": exists}
    finally:
        os.close(fd)


def hash_fd(fd, checkpoint=None):
    os.lseek(fd, 0, os.SEEK_SET)
    value = hashlib.sha256()
    while True:
        if checkpoint:
            checkpoint()
        chunk = os.read(fd, CHUNK)
        if not chunk:
            return value.hexdigest()
        value.update(chunk)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        require(written > 0, "REMOTE_IO_ERROR", "Zero-byte write")
        view = view[written:]


def exact_mode(fd, mode):
    os.fchmod(fd, mode)
    require(stat.S_IMODE(os.fstat(fd).st_mode) == mode, "REMOTE_PERMISSION_DENIED", "New object mode could not be verified")


def private_directory(name, parent):
    # The helper is single threaded. A restrictive inherited umask must not
    # remove owner access before we can open/fchmod our new directory.
    previous = os.umask(0o077)
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
    finally:
        os.umask(previous)


class FileTransaction:
    """One session, one immutable action, one prepared item at a time."""
    def __init__(self, manifest, *, remaining_seconds, clock=time.monotonic, cancel_check=None):
        self.manifest = manifest = json.loads(canonical(validated_manifest(manifest)))
        require(probe()["supported"], "REMOTE_CAPABILITY_UNAVAILABLE", "Required Linux file capabilities are unavailable")
        require(isinstance(remaining_seconds, (int, float)) and not isinstance(remaining_seconds, bool)
                and 0 < remaining_seconds <= 86400)
        self.clock, self.cancel_check = clock, cancel_check
        self.deadline = clock() + remaining_seconds
        self.roots = {root["root_id"]: root for root in manifest["roots"]}
        self.root_fds, self.receipt_fds, self.created_dirs = {}, {}, {}
        self.index, self.total_bytes, self.current = 0, 0, None
        self.stopped, self.receipts = False, []
        try:
            # Reserve the remote action namespace before any data preparation.
            # An existing namespace is never resumed as a write operation.
            for key, root in self.roots.items():
                verify_root(root)
                fd, _ = open_directory(root["canonical_path"])
                self.root_fds[key] = fd
                directory = names(manifest["action_id"])["action_directory"]
                try:
                    private_directory(directory, fd)
                except FileExistsError:
                    raise FileError("ACTION_ALREADY_EXISTS", "Action evidence already exists; use read-only reconciliation")
                receipt_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                self.receipt_fds[key] = receipt_fd
                exact_mode(receipt_fd, 0o700)
                self._receipt(key, "manifest.json", {"manifest_digest": manifest["manifest_digest"],
                    "action_id": manifest["action_id"], "endpoint_digest": manifest["endpoint"]["endpoint_digest"],
                    "root_id": key, "root_identity": root["identity"]})
                os.fsync(fd)
        except BaseException:
            self.close()
            raise

    def checkpoint(self):
        require(not self.stopped and self.clock() < self.deadline, "SCOPE_EXPIRED", "File session expired or stopped")
        if self.cancel_check:
            self.cancel_check()

    def _receipt(self, root_id, name, value):
        data = canonical(value)
        require(len(data) <= MAX_REPLY)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.receipt_fds[root_id])
        try:
            exact_mode(fd, 0o600)
            write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self.receipt_fds[root_id])

    def _parent(self, item):
        dest = item["destination"]
        root = self.roots[dest["root_id"]]
        verify_root(root)
        path = posixpath.join(root["canonical_path"], posixpath.dirname(dest["relative_path"]))
        fd, chain = open_directory(path)
        try:
            actual = {entry["path"]: entry for entry in chain}
            for expected in dest["parent_chain"]:
                require(identity_matches(actual.get(expected["path"], {}), expected), "ROOT_CHANGED", "Destination parent identity changed")
            if dest["missing_components"]:
                parent_item = dest["parent_item_id"]
                require(parent_item in self.created_dirs, "ROOT_CHANGED", "Parent mkdir has not committed")
                made = self.created_dirs[parent_item]
                require(made["canonical_path"] == path and identity_matches(metadata(os.fstat(fd)), made),
                        "ROOT_CHANGED", "Created parent identity changed")
                for made in self.created_dirs.values():
                    if made["canonical_path"] in actual:
                        require(identity_matches(actual[made["canonical_path"]], made), "ROOT_CHANGED", "Created ancestor identity changed")
            return fd, path
        except BaseException:
            os.close(fd)
            raise

    def _probe_publication(self, parent, item):
        a, b, c = item["names"]["capability_probe_names"]
        owned = {}
        try:
            for name in (a, b):
                if item["op"] == "mkdir":
                    private_directory(name, parent)
                    owned[name] = metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
                    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        exact_mode(fd, 0o700)
                    finally:
                        os.close(fd)
                elif item["op"] == "symlink":
                    os.symlink("probe-only", name, dir_fd=parent)
                else:
                    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    owned[name] = metadata(os.fstat(fd))
                    try:
                        exact_mode(fd, 0o600)
                        write_all(fd, name.encode())
                    finally:
                        os.close(fd)
                owned[name] = metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
            try:
                rename_noreplace(parent, a, b)
            except FileError as exc:
                require(exc.code == "DESTINATION_CONFLICT", "REMOTE_CAPABILITY_UNAVAILABLE", "No-replace filesystem probe failed")
            else:
                raise FileError("REMOTE_CAPABILITY_UNAVAILABLE", "Filesystem incorrectly replaced an existing probe")
            require(all(identity_matches(metadata(os.stat(name, dir_fd=parent, follow_symlinks=False)), expected, stable=True)
                        for name, expected in owned.items()), "REMOTE_CAPABILITY_UNAVAILABLE", "No-replace probe changed existing objects")
            rename_noreplace(parent, a, c)
            owned[c] = owned.pop(a)
            require(identity_matches(metadata(os.stat(c, dir_fd=parent, follow_symlinks=False)), owned[c]), "REMOTE_CAPABILITY_UNAVAILABLE", "No-replace probe identity mismatch")
        finally:
            leftovers = []
            for name, expected in owned.items():
                if not self._remove_owned(parent, name, expected):
                    leftovers.append(name)
            if leftovers:
                raise FileError("REMOTE_IO_ERROR", "Owned filesystem probe cleanup incomplete", stage="prepare", leftovers=leftovers)

    @staticmethod
    def _remove_owned(parent, name, expected):
        try:
            current = metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
            if not identity_matches(current, expected):
                return False
            (os.rmdir if current["type"] == "directory" else os.unlink)(name, dir_fd=parent)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _target_absent(self, parent, item, source=None):
        name = posixpath.basename(item["destination"]["relative_path"])
        try:
            existing = metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
        except FileNotFoundError:
            return
        if source:
            resolved = existing
            if existing["type"] == "symlink":
                try:
                    resolved = metadata(os.stat(name, dir_fd=parent, follow_symlinks=True))
                except OSError:
                    pass
            if identity_matches(resolved, source):
                raise FileError("SOURCE_EQUALS_DESTINATION", "Source and destination identify the same file")
        raise FileError("DESTINATION_CONFLICT", "Destination already exists")

    def prepare_next(self):
        self.checkpoint()
        require(self.current is None, "PROTOCOL_ERROR", "Commit or abort the current item first")
        require(self.index < len(self.manifest["items"]), "PROTOCOL_ERROR", "No remaining item")
        item = self.manifest["items"][self.index]
        parent, parent_path = self._parent(item)
        current = {"item": item, "parent_fd": parent, "parent_path": parent_path,
                   "source_fd": None, "temp_fd": None, "temporary": None, "published": False}
        self.current = current
        try:
            source = None
            if item["source"] is not None:
                source_fd, source = open_source(item["source"]["requested_path"])
                current["source_fd"] = source_fd
                source["endpoint_digest"] = self.manifest["endpoint"]["endpoint_digest"]
                source["content_class"] = provenance_class(source, item["source_provenance"], source["endpoint_digest"])
                require(source["canonical_path"] == item["source"]["canonical_path"]
                        and source["resolution_chain"] == item["source"]["resolution_chain"]
                        and source["content_class"] == item["source"]["content_class"]
                        and identity_matches(source, item["source"], stable=True), "SOURCE_CHANGED", "Source evidence changed")
            actual_bytes = (source["size"] if item["op"] == "copy" else len(source["canonical_path"].encode()) if item["op"] == "symlink"
                            else len(item["text"].encode()) if item["op"] == "write_text" else 0)
            require(actual_bytes == item["bytes"] and self.total_bytes + actual_bytes <= self.manifest["max_total_bytes"], "BUDGET_EXCEEDED", "Actual bytes exceed immutable budget")
            self._target_absent(parent, item, source)
            self._probe_publication(parent, item)
            temporary = item["names"]["staging_name"]
            sha = None
            if item["op"] == "mkdir":
                private_directory(temporary, parent)
                temp_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                current["temp_fd"] = temp_fd
                current["temporary"] = metadata(os.fstat(temp_fd))
                exact_mode(temp_fd, 0o700)
            elif item["op"] == "symlink":
                os.symlink(source["canonical_path"], temporary, dir_fd=parent)
                current["temporary"] = metadata(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
            else:
                temp_fd = os.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                current["temp_fd"] = temp_fd
                current["temporary"] = metadata(os.fstat(temp_fd))
                exact_mode(temp_fd, 0o600)
                expected_hash = hashlib.sha256()
                copied = 0
                if item["op"] == "write_text":
                    data = item["text"].encode("utf-8")
                    write_all(temp_fd, data)
                    expected_hash.update(data)
                    copied = len(data)
                else:
                    while True:
                        self.checkpoint()
                        data = os.read(current["source_fd"], CHUNK)
                        if not data:
                            break
                        copied += len(data)
                        require(copied <= item["bytes"], "SOURCE_CHANGED", "Source grew during copy")
                        write_all(temp_fd, data)
                        expected_hash.update(data)
                    require(copied == item["bytes"] and identity_matches(metadata(os.fstat(current["source_fd"])), source, stable=True), "SOURCE_CHANGED", "Source changed during copy")
                sha = hash_fd(temp_fd, self.checkpoint)
                require(sha == expected_hash.hexdigest(), "REMOTE_IO_ERROR", "Prepared content digest mismatch")
                os.fsync(temp_fd)
            current["temporary"] = metadata(os.stat(temporary, dir_fd=parent, follow_symlinks=False))
            os.fsync(parent)
            receipt = {"action_id": self.manifest["action_id"], "manifest_digest": self.manifest["manifest_digest"],
                       "item_id": item["item_id"], "state": "prepared", "source_evidence": source,
                       "temporary_evidence": current["temporary"], "target_evidence": None,
                       "content_class": item["content_class"], "bytes_processed": actual_bytes,
                       "sha256": sha, "verification_level": "remote_sha256_with_metadata_stability" if sha else "directory_identity" if item["op"] == "mkdir" else "link_target_and_metadata",
                       "remote_receipt_id": item["names"]["action_directory"] + "/" + item["names"]["prepared_name"],
                       "published": False}
            self._receipt(item["destination"]["root_id"], item["names"]["prepared_name"], receipt)
            current.update(receipt=receipt, token=uuid.uuid4().hex, prepared_at=self.clock())
            return {**receipt, "prepare_token": current["token"], "prepared_digest": digest(receipt), "commit_ttl_seconds": 30}
        except BaseException as exc:
            error = map_error(exc)
            aborted = self.abort()
            error.leftovers.extend(aborted["leftovers"])
            raise error

    def commit(self, permit, *, remaining_seconds):
        require(isinstance(remaining_seconds, (int, float)) and not isinstance(remaining_seconds, bool)
                and 0 < remaining_seconds <= 30, "SCOPE_EXPIRED", "Invalid commit lifetime")
        received_at = self.clock()
        self.checkpoint()
        current = self.current
        require(current is not None and "receipt" in current, "PROTOCOL_ERROR", "No prepared item")
        item, receipt = current["item"], current["receipt"]
        commit_deadline = min(received_at + remaining_seconds, current["prepared_at"] + 30, self.deadline)
        expected_keys = {"action_id", "manifest_digest", "item_id", "scope_id", "scope_version", "job_key", "attempt_id", "endpoint_digest", "root_id", "root_version", "prepared_digest", "prepare_token", "valid_until"}
        require(isinstance(permit, dict) and set(permit) == expected_keys)
        require(self.clock() < commit_deadline, "SCOPE_EXPIRED", "Prepared commit token expired")
        for key in ("action_id", "manifest_digest", "scope_id", "scope_version", "job_key", "attempt_id"):
            require(permit[key] == self.manifest[key], "PROTOCOL_ERROR", "Commit identity mismatch")
        require(permit["endpoint_digest"] == self.manifest["endpoint"]["endpoint_digest"]
                and permit["root_id"] == item["destination"]["root_id"]
                and permit["root_version"] == item["destination"]["root_version"]
                and permit["item_id"] == item["item_id"] and permit["prepare_token"] == current["token"]
                and permit["prepared_digest"] == digest(receipt), "PROTOCOL_ERROR", "Commit evidence mismatch")
        parent = current["parent_fd"]
        published = False
        try:
            recheck_fd, _ = self._parent(item)
            try:
                require(identity_matches(metadata(os.fstat(recheck_fd)), metadata(os.fstat(parent))), "ROOT_CHANGED", "Parent changed after prepare")
            finally:
                os.close(recheck_fd)
            if current["source_fd"] is not None:
                src_fd, actual = open_source(item["source"]["requested_path"])
                try:
                    require(actual["canonical_path"] == item["source"]["canonical_path"]
                            and actual["resolution_chain"] == item["source"]["resolution_chain"]
                            and identity_matches(actual, item["source"], stable=True)
                            and identity_matches(metadata(os.fstat(current["source_fd"])), item["source"], stable=True), "SOURCE_CHANGED", "Source changed before commit")
                finally:
                    os.close(src_fd)
            temporary = item["names"]["staging_name"]
            require(identity_matches(metadata(os.stat(temporary, dir_fd=parent, follow_symlinks=False)), current["temporary"], stable=True), "SOURCE_CHANGED", "Prepared object changed")
            self._target_absent(parent, item, item["source"])
            self.checkpoint()
            require(self.clock() < commit_deadline, "SCOPE_EXPIRED", "Commit token expired before publication")
            current["token"] = None  # consumed before the publication syscall
            target_name = posixpath.basename(item["destination"]["relative_path"])
            rename_noreplace(parent, temporary, target_name)
            published = current["published"] = True
            target = metadata(os.stat(target_name, dir_fd=parent, follow_symlinks=False))
            require(identity_matches(target, current["temporary"]), "ACTION_UNKNOWN", "Published target identity mismatch")
            check, _ = self._parent(item)
            try:
                require(identity_matches(metadata(os.fstat(check)), metadata(os.fstat(parent))), "ACTION_UNKNOWN", "Parent moved during publication")
            finally:
                os.close(check)
            os.fsync(parent)
            target.update(canonical_path=posixpath.join(current["parent_path"], target_name), endpoint_digest=self.manifest["endpoint"]["endpoint_digest"])
            result = {**receipt, "state": "committed", "target_evidence": target, "published": True,
                      "remote_receipt_id": item["names"]["action_directory"] + "/" + item["names"]["committed_name"]}
            self._receipt(item["destination"]["root_id"], item["names"]["committed_name"], result)
            if item["op"] == "mkdir":
                self.created_dirs[item["item_id"]] = target
            self.total_bytes += item["bytes"]
            self.receipts.append(result)
            self._close_current()
            self.index += 1
            return result
        except BaseException as exc:
            error = map_error(exc, "commit")
            if published or error.published == "unknown":
                error = FileError("ACTION_UNKNOWN", "Publication or committed receipt is uncertain", stage="commit", published=True if published else "unknown")
            aborted = self.abort()
            error.leftovers.extend(aborted["leftovers"])
            raise error

    def _close_current(self):
        if self.current:
            for key in ("source_fd", "temp_fd", "parent_fd"):
                if self.current.get(key) is not None:
                    os.close(self.current[key])
            self.current = None

    def abort(self):
        self.stopped = True
        leftovers = []
        if self.current:
            current = self.current
            if current["temporary"] and not current["published"]:
                name = current["item"]["names"]["staging_name"]
                if not self._remove_owned(current["parent_fd"], name, current["temporary"]):
                    leftovers.append(posixpath.join(current["parent_path"], name))
            self._close_current()
        return {"state": "aborted", "published": bool(self.receipts),
                "items": [{"item_id": r["item_id"], "state": r["state"], "remote_receipt_id": r["remote_receipt_id"]} for r in self.receipts],
                "leftovers": leftovers}

    def close(self):
        self.abort()
        for fd in list(self.receipt_fds.values()) + list(self.root_fds.values()):
            os.close(fd)
        self.receipt_fds.clear()
        self.root_fds.clear()


def read_json_at(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = metadata(os.fstat(fd))
        require(info["type"] == "file" and info["size"] <= MAX_REPLY, "PROTOCOL_ERROR", "Invalid remote receipt")
        raw = os.read(fd, MAX_REPLY + 1)
        require(len(raw) <= MAX_REPLY)
        return json.loads(raw.decode("utf-8"))
    finally:
        os.close(fd)


def reconcile(manifest):
    validated_manifest(manifest)
    roots = {root["root_id"]: root for root in manifest["roots"]}
    results = []
    for item in manifest["items"]:
        root = roots[item["destination"]["root_id"]]
        parent = receipt_fd = target_fd = None
        try:
            verify_root(root)
            root_fd, _ = open_directory(root["canonical_path"])
            try:
                receipt_fd = os.open(item["names"]["action_directory"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            finally:
                os.close(root_fd)
            header = read_json_at(receipt_fd, "manifest.json")
            require(header.get("manifest_digest") == manifest["manifest_digest"] and header.get("action_id") == manifest["action_id"]
                    and header.get("endpoint_digest") == manifest["endpoint"]["endpoint_digest"], "ACTION_UNKNOWN", "Receipt header mismatch")
            record = read_json_at(receipt_fd, item["names"]["committed_name"])
            require(record.get("manifest_digest") == manifest["manifest_digest"] and record.get("item_id") == item["item_id"]
                    and record.get("state") == "committed" and record.get("content_class") == item["content_class"],
                    "ACTION_UNKNOWN", "Committed receipt mismatch")
            target = posixpath.join(root["canonical_path"], item["destination"]["relative_path"])
            require((record.get("target_evidence") or {}).get("canonical_path") == target, "ACTION_UNKNOWN", "Receipt target mismatch")
            parent, _ = open_directory(posixpath.dirname(target))
            name = posixpath.basename(target)
            actual = metadata(os.stat(name, dir_fd=parent, follow_symlinks=False))
            require(identity_matches(actual, record["target_evidence"], stable=item["op"] != "mkdir"), "ACTION_UNKNOWN", "Current target differs from receipt")
            if item["op"] in {"copy", "write_text"}:
                target_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                require(identity_matches(metadata(os.fstat(target_fd)), actual, stable=True)
                        and hash_fd(target_fd) == record["sha256"]
                        and identity_matches(metadata(os.fstat(target_fd)), actual, stable=True), "ACTION_UNKNOWN", "Current target digest differs from receipt")
            elif item["op"] == "symlink":
                require(os.readlink(name, dir_fd=parent) == item["source"]["canonical_path"], "ACTION_UNKNOWN", "Link target differs from receipt")
            verify_root(root)
            results.append({"item_id": item["item_id"], "state": "confirmed", "remote_receipt_id": record["remote_receipt_id"],
                            "content_class": record["content_class"], "sha256": record["sha256"]})
        except Exception as exc:
            results.append({"item_id": item["item_id"], "state": "unknown", "error": map_error(exc, "reconcile").payload()})
        finally:
            for fd in (target_fd, parent, receipt_fd):
                if fd is not None:
                    os.close(fd)
    return {"state": "confirmed" if all(item["state"] == "confirmed" for item in results) else "unknown", "items": results, "read_only": True}


def _read_request(timeout=None):
    if timeout is not None:
        require(bool(select.select([sys.stdin.buffer], [], [], max(0, timeout))[0]), "SCOPE_EXPIRED", "File session input timeout")
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw:
        raise EOFError()
    require(len(raw) <= MAX_FRAME and raw.endswith(b"\n"), "PROTOCOL_ERROR", "Request frame exceeds limit or is truncated")
    return json.loads(raw.decode("utf-8"))


def _reply(data=None, error=None):
    raw = canonical({"ok": error is None, "data": data} if error is None else {"ok": False, "error": error.payload()})
    if len(raw) > MAX_REPLY:
        raw = canonical({"ok": False, "error": FileError("PROTOCOL_ERROR", "Reply exceeds frame budget").payload()})
    sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def main():
    transaction = None
    def cancellation():
        if select.select([sys.stdin.buffer], [], [], 0)[0]:
            request = _read_request()
            require(request == {"op": "abort"}, "PROTOCOL_ERROR", "Only abort is accepted during preparation")
            raise FileError("ACTION_ABORTED", "Preparation was interrupted by owner")
    try:
        while True:
            try:
                timeout = None
                if transaction:
                    timeout = min(60, transaction.deadline - transaction.clock())
                    if transaction.current and "prepared_at" in transaction.current:
                        timeout = min(timeout, transaction.current["prepared_at"] + 30 - transaction.clock())
                request = _read_request(timeout)
                require(isinstance(request, dict) and isinstance(request.get("op"), str))
                op = request["op"]
                if op == "probe" and transaction is None:
                    require(set(request) == {"op"})
                    _reply(probe())
                elif op == "inspect" and transaction is None:
                    require(set(request) <= {"op", "path", "view", "provenance", "endpoint_digest", "limit", "cursor"})
                    _reply(inspect(request["path"], request.get("view", "stat"), provenance=request.get("provenance"),
                                   endpoint_digest=request.get("endpoint_digest", ""), limit=request.get("limit", 200), cursor=request.get("cursor")))
                elif op == "source" and transaction is None:
                    require(set(request) == {"op", "path", "provenance", "endpoint_digest"})
                    fd, info = open_source(request["path"])
                    os.close(fd)
                    info["endpoint_digest"] = request["endpoint_digest"]
                    info["content_class"] = provenance_class(info, request["provenance"], request["endpoint_digest"])
                    _reply(info)
                elif op == "root" and transaction is None:
                    require(set(request) == {"op", "path"})
                    _reply(root_evidence(request["path"]))
                elif op == "destination" and transaction is None:
                    require(set(request) == {"op", "root", "path"})
                    _reply(destination_evidence(request["root"], request["path"]))
                elif op == "begin" and transaction is None:
                    require(set(request) == {"op", "manifest", "remaining_seconds"})
                    transaction = FileTransaction(request["manifest"], remaining_seconds=request["remaining_seconds"], cancel_check=cancellation)
                    _reply({"state": "ready", "manifest_digest": transaction.manifest["manifest_digest"]})
                elif op == "prepare_next" and transaction is not None:
                    require(set(request) == {"op"})
                    _reply(transaction.prepare_next())
                elif op == "commit" and transaction is not None:
                    require(set(request) == {"op", "permit", "remaining_seconds"})
                    _reply(transaction.commit(request["permit"], remaining_seconds=request["remaining_seconds"]))
                elif op == "abort" and transaction is not None:
                    require(set(request) == {"op"})
                    _reply(transaction.abort())
                    break
                elif op == "reconcile" and transaction is None:
                    require(set(request) == {"op", "manifest"})
                    _reply(reconcile(request["manifest"]))
                else:
                    raise FileError("PROTOCOL_ERROR", "Unsupported or out-of-order operation")
            except EOFError:
                break
            except Exception as exc:
                error = map_error(exc)
                if transaction:
                    error.leftovers.extend(transaction.abort()["leftovers"])
                _reply(error=error)
                break
    finally:
        if transaction:
            transaction.close()


if __name__ == "__main__":
    main()
