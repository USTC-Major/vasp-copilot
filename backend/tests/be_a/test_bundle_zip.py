"""验收 10/13/17：BundleBuilder manifest + 确定性 zip；同输入字节级一致。"""

import json
import zipfile

import pytest

from backend.app.generators.archive import (
    FIXED_TIMESTAMP,
    FIXED_ZIP_DATE_TIME,
    BundleBuilder,
)
from backend.app.workflow.pipeline import WorkflowGenerationPipeline

SAMPLE_FILES = {
    "b.txt": "bee",
    "a.txt": "alpha",
    "01_relax/INCAR": "SYSTEM = demo\nENCUT = 520\n",
}


class TestBundleBuilder:
    def test_manifest_lists_all_files_sorted_with_hashes(self):
        result = BundleBuilder().build("wf_x", SAMPLE_FILES, revision=1)
        paths = [entry["path"] for entry in result.manifest.files]
        assert paths == sorted(SAMPLE_FILES)
        assert len(result.manifest.files) == len(SAMPLE_FILES)
        for entry in result.manifest.files:
            assert len(entry["sha256"]) == 64
            assert entry["size_bytes"] > 0

    def test_zip_entries_sorted_with_fixed_mtime(self):
        result = BundleBuilder().build("wf_x", SAMPLE_FILES)
        with zipfile.ZipFile(__import__("io").BytesIO(result.zip_bytes)) as bundle:
            infos = bundle.infolist()
            assert [info.filename for info in infos] == sorted(SAMPLE_FILES)
            for info in infos:
                assert info.date_time == FIXED_ZIP_DATE_TIME

    def test_manifest_timestamp_is_fixed(self):
        result = BundleBuilder().build("wf_x", SAMPLE_FILES)
        assert result.manifest.created_at == FIXED_TIMESTAMP

    def test_same_input_twice_is_byte_identical(self):
        first = BundleBuilder().build("wf_x", SAMPLE_FILES)
        second = BundleBuilder().build("wf_x", SAMPLE_FILES)
        assert first.zip_bytes == second.zip_bytes
        assert first.manifest.bundle_sha256 == second.manifest.bundle_sha256

    def test_bundle_hash_sensitive_to_content(self):
        before = BundleBuilder().build("wf_x", SAMPLE_FILES)
        changed = dict(SAMPLE_FILES)
        changed["a.txt"] = "alpha!"
        after = BundleBuilder().build("wf_x", changed)
        assert before.manifest.bundle_sha256 != after.manifest.bundle_sha256

    def test_unsafe_paths_rejected(self):
        with pytest.raises(ValueError):
            BundleBuilder().build("wf_x", {"../escape.txt": "x"})
        with pytest.raises(ValueError):
            BundleBuilder().build("wf_x", {"/abs/path.txt": "x"})
        with pytest.raises(ValueError):
            BundleBuilder().build("wf_x", {"a//b.txt": "x"})


class TestPipelineDeterminism:
    def test_same_request_twice_byte_identical(self, fe2o3_request):
        pipeline = WorkflowGenerationPipeline()
        first = pipeline.generate(fe2o3_request)
        second = pipeline.generate(fe2o3_request)
        assert first.bundle.zip_bytes == second.bundle.zip_bytes
        assert first.bundle.manifest.bundle_sha256 == second.bundle.manifest.bundle_sha256

    def test_zip_roundtrip_matches_manifest(self, fe2o3_request):
        import hashlib
        import io

        result = WorkflowGenerationPipeline().generate(fe2o3_request)
        with zipfile.ZipFile(io.BytesIO(result.bundle.zip_bytes)) as bundle:
            names = set(bundle.namelist())
            manifest_paths = {entry["path"] for entry in result.bundle.manifest.files}
            assert names == manifest_paths | {"workflow_manifest.json"}
            for entry in result.bundle.manifest.files:
                data = bundle.read(entry["path"])
                assert hashlib.sha256(data).hexdigest() == entry["sha256"]

    def test_manifest_embedded_in_zip_is_valid_json(self, nacl_request):
        import hashlib

        result = WorkflowGenerationPipeline().generate(nacl_request)
        text = result.bundle.files["workflow_manifest.json"].decode("utf-8")
        body = json.loads(text)
        assert body["workflow_id"] == "wf_nacl"
        assert body["revision"] == 1
        # 内嵌 manifest 描述除自身外的全部文件，且逐文件 hash 与 zip 内容一致
        embedded_paths = {entry["path"] for entry in body["files"]}
        assert embedded_paths == set(result.bundle.files) - {"workflow_manifest.json"}
        for entry in body["files"]:
            data = result.bundle.files[entry["path"]]
            assert hashlib.sha256(data).hexdigest() == entry["sha256"]
