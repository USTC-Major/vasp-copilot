"""Generate/verify checksums from archive bytes, never a text-mode checkout.

Stage release inputs, then run --write --ref INDEX; stage SHA256SUMS.txt,
commit, and run --ref HEAD. Independently check downloaded GitHub zip/tar.gz
with --archive PATH before publishing the release. No extraction is used.
"""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import zipfile

MANIFEST = "SHA256SUMS.txt"


def archive_files(raw: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}

    def add(name: str, data: bytes) -> None:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or name in files:
            raise ValueError(f"unsafe or duplicate archive path: {name}")
        files[name] = data

    if zipfile.is_zipfile(io.BytesIO(raw)):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    if (member.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("symlinks are not supported in release checksums")
                    add(member.filename, archive.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
            for member in archive:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ValueError("non-regular archive member")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError("unreadable archive member")
                add(member.name, stream.read())
    # GitHub adds a repository/commit root directory. Local git archive does not.
    if MANIFEST not in files:
        roots = {name.split("/", 1)[0] for name in files}
        if len(roots) == 1 and all("/" in name for name in files):
            files = {name.split("/", 1)[1]: data for name, data in files.items()}
    return files


def manifest_bytes(files: dict[str, bytes]) -> bytes:
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                   for name, data in sorted(files.items()) if name != MANIFEST).encode("utf-8")


def verify(files: dict[str, bytes]) -> dict[str, int]:
    if MANIFEST not in files:
        raise ValueError("missing SHA256SUMS.txt")
    entries: dict[str, str] = {}
    for line in files[MANIFEST].decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match or match[2] in entries or match[2] == MANIFEST:
            raise ValueError("invalid or duplicate manifest entry")
        entries[match[2]] = match[1]
    expected = set(files) - {MANIFEST}
    missing, extra = expected - set(entries), set(entries) - expected
    mismatches = [name for name in expected & set(entries)
                  if hashlib.sha256(files[name]).hexdigest() != entries[name]]
    if missing or extra or mismatches:
        raise ValueError(f"entries={len(entries)} missing={len(missing)} extra={len(extra)} "
                         f"mismatches={len(mismatches)} sample={mismatches[:3]}")
    return {"entries": len(entries), "mismatches": 0}


def git_archive(repo: Path, ref: str) -> bytes:
    if ref == "INDEX":
        ref = subprocess.check_output(["git", "write-tree"], cwd=repo).decode("ascii").strip()
    # Binary stdout must never pass through PowerShell text pipes or a tar extractor.
    return subprocess.check_output(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf",
                                    "archive", "--format=zip", ref], cwd=repo)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ref", default="HEAD")
    group.add_argument("--archive", type=Path)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    files = archive_files(args.archive.read_bytes() if args.archive else git_archive(args.repo, args.ref))
    if args.write:
        if args.archive or args.ref != "INDEX":
            parser.error("--write requires --ref INDEX, not a downloaded archive or old commit")
        data = manifest_bytes(files)
        (args.repo / MANIFEST).write_bytes(data)
        print(f"generated {len(data.splitlines())} entries from staged archive bytes")
    else:
        print(verify(files))


if __name__ == "__main__":
    main()
