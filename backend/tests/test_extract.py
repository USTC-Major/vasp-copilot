from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.core.errors import PayloadTooLargeError, SecurityError, UnsupportedMediaTypeError
from app.security.extract import SafeArchiveExtractor


def extractor(**kw):
    base = dict(max_upload_bytes=64 * 1024 * 1024,
                max_uncompressed_bytes=8 * 1024 * 1024,
                max_file_count=2000,
                max_uncompression_ratio=100.0)
    base.update(kw)
    return SafeArchiveExtractor(**base)


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def test_valid_zip_extracts(tmp_path):
    data = _zip([("INCAR", "SYSTEM = test\n"), ("OSZICAR", "  1 F= -1\n")])
    res = extractor().extract(data, tmp_path / "run")
    names = sorted(fp.name for fp in res.files)
    assert names == ["INCAR", "OSZICAR"]


def test_path_traversal_rejected(tmp_path):
    data = _zip([("../escape.txt", "boom"), ("INCAR", "SYSTEM = x\n")])
    with pytest.raises(SecurityError):
        extractor().extract(data, tmp_path / "run")


def test_absolute_path_rejected(tmp_path):
    data = _zip([("/etc/passwd", "boom")])
    with pytest.raises(SecurityError):
        extractor().extract(data, tmp_path / "run")


def test_symlink_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "target")
    with pytest.raises(SecurityError):
        extractor().extract(buf.getvalue(), tmp_path / "run")


def test_not_a_zip_rejected_spoofed_ext(tmp_path):
    data = b"PK\x03\x04 not really a zip but magic ok" + b"\x00" * 16
    with pytest.raises(UnsupportedMediaTypeError):
        extractor().extract(data, tmp_path / "run")


def test_wrong_magic_rejected(tmp_path):
    with pytest.raises(UnsupportedMediaTypeError):
        extractor().extract(b"GIF89a......", tmp_path / "run")


def test_too_many_files_rejected(tmp_path):
    data = _zip([(f"f{i}.txt", "x") for i in range(5)])
    with pytest.raises(PayloadTooLargeError):
        extractor(max_file_count=3).extract(data, tmp_path / "run")


def test_uncompressed_size_limit(tmp_path):
    data = _zip([("big.txt", "a" * 1000)])
    with pytest.raises(PayloadTooLargeError):
        extractor(max_uncompressed_bytes=100).extract(data, tmp_path / "run")


def test_nested_directories_allowed(tmp_path):
    data = _zip([("sub/dir/deep/INCAR", "SYSTEM=x\n")])
    res = extractor().extract(data, tmp_path / "run")
    assert len(res.files) == 1
