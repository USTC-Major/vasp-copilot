"""Release integrity must survive CRLF checkouts and binary/UTF-8 data."""
import io
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest

from backend.scripts.release_checksums import MANIFEST, archive_files, git_archive, manifest_bytes, verify


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_downloaded_archive_prefix_and_exact_bytes(kind):
    files = {"a.txt": b"first\nlast\n", "binary.bin": b"\x00\r\n\xff", "中文.txt": "文本\n".encode()}
    files[MANIFEST] = manifest_bytes(files)
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in files.items():
                archive.writestr("project-v1/" + name, data)
    else:
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, data in files.items():
                item = tarfile.TarInfo("project-v1/" + name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
    assert archive_files(buffer.getvalue()) == files
    assert verify(archive_files(buffer.getvalue())) == {"entries": 3, "mismatches": 0}


@pytest.mark.parametrize("fault", ["crlf", "missing", "extra", "duplicate", "bad-line", "no-manifest"])
def test_bad_manifest_fails_closed(fault):
    files = {"a.txt": b"one\ntwo\n"}
    files[MANIFEST] = manifest_bytes(files)
    if fault == "crlf": files["a.txt"] = b"one\r\ntwo\r\n"
    if fault == "missing": files["b.txt"] = b"not-listed"
    if fault == "extra": del files["a.txt"]
    if fault == "duplicate": files[MANIFEST] *= 2
    if fault == "bad-line": files[MANIFEST] = b"garbage\n"
    if fault == "no-manifest": del files[MANIFEST]
    with pytest.raises(ValueError):
        verify(files)


def test_staged_bytes_not_crlf_worktree(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.PIPE)
    git("init", "-q")
    git("config", "core.autocrlf", "true")
    (tmp_path / "sample.txt").write_bytes(b"one\r\ntwo\r\n")
    (tmp_path / ".gitattributes").write_bytes(b"*.bin binary\n")
    (tmp_path / "sample.bin").write_bytes(b"\x00\r\n\xff")
    (tmp_path / MANIFEST).write_bytes(b"")
    git("add", ".")
    files = archive_files(git_archive(tmp_path, "INDEX"))
    assert files["sample.txt"] == b"one\ntwo\n"
    assert files["sample.bin"] == b"\x00\r\n\xff"
    assert (tmp_path / "sample.txt").read_bytes() == b"one\r\ntwo\r\n"
    (tmp_path / MANIFEST).write_bytes(manifest_bytes(files))
    git("add", MANIFEST)
    assert verify(archive_files(git_archive(tmp_path, "INDEX")))["entries"] == 3
    tree = git("write-tree").decode().strip()
    converted = archive_files(git("archive", "--format=zip", tree))
    assert converted["sample.txt"] == b"one\r\ntwo\r\n"
    with pytest.raises(ValueError, match="mismatches="):
        verify(converted)
