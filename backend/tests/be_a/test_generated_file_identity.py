"""Preview identities stay tied to workflow/path/bytes across regeneration."""
from __future__ import annotations

import json

import pytest

from backend.app.core.errors import ConflictError, NotFoundError
from backend.app.core.file_identity import generated_file_id, is_bound_generated_file_id
from backend.app.schemas.generation import ParameterPatch
from backend.app.services.file_store import FileStore
from backend.app.services.workflow_service import WorkflowService
from backend.app.workflow.pipeline import WorkflowGenerationPipeline


def leaves(tree):
    if tree["type"] == "file":
        yield tree
    for child in tree.get("children", []):
        yield from leaves(child)


def test_identity_binds_workflow_full_path_and_bytes():
    original = generated_file_id("wf_a", "01_relax/POSCAR", b"NaCl\n")
    assert is_bound_generated_file_id(original)
    assert original == generated_file_id("wf_a", "01_relax/POSCAR", b"NaCl\n")
    assert len({original,
                generated_file_id("wf_b", "01_relax/POSCAR", b"NaCl\n"),
                generated_file_id("wf_a", "02_static/POSCAR", b"NaCl\n"),
                generated_file_id("wf_a", "01_relax/POSCAR", b"Si\n")}) == 4
    # JSON array encoding avoids ambiguous concatenated tuples.
    assert generated_file_id("wf_ab", "c", b"x") != generated_file_id("wf_a", "bc", b"x")


def test_multiple_workflows_and_same_name_steps_keep_separate_bytes(tmp_path, nacl_request):
    store = FileStore(tmp_path / "files")
    service = WorkflowService(file_store=store)
    a = service.generate(nacl_request)
    b_request = nacl_request.model_copy(update={"workflow_id": "wf_b", "sample_name": "workflow B"})
    b = service.generate(b_request)
    a_nodes = {node["relative_path"]: node for node in leaves(a["file_tree"])}
    b_nodes = {node["relative_path"]: node for node in leaves(b["file_tree"])}
    assert {node["file_id"] for node in a_nodes.values()}.isdisjoint(node["file_id"] for node in b_nodes.values())
    assert a_nodes["01_relax/POSCAR"]["file_id"] != a_nodes["02_static/POSCAR"]["file_id"]
    assert store.get_file(a_nodes["01_relax/POSCAR"]["file_id"]).path.read_bytes().startswith(b"NaCl\n")
    assert store.get_file(b_nodes["01_relax/POSCAR"]["file_id"]).path.read_bytes().startswith(b"workflow B\n")


def test_changed_regeneration_keeps_old_reference_and_restart_bytes(tmp_path, nacl_request):
    store = FileStore(tmp_path / "files")
    service = WorkflowService(file_store=store)
    first = service.generate(nacl_request)
    first_nodes = {node["relative_path"]: node for node in leaves(first["file_tree"])}
    old_id = first_nodes["01_relax/INCAR"]["file_id"]
    old_bytes = store.get_file(old_id).path.read_bytes()
    old_zip = service.get_artifact(nacl_request.workflow_id).zip_bytes
    patched_request = nacl_request.model_copy(update={"patches": [ParameterPatch(
        patch_id="change-encut", parameter="ENCUT", operation="replace", value=650,
        confirmed_by_user=True, reason="Identity regression only",
    )]})
    changed = service.generate(patched_request)
    changed_nodes = {node["relative_path"]: node for node in leaves(changed["file_tree"])}
    new_id = changed_nodes["01_relax/INCAR"]["file_id"]
    assert new_id != old_id
    assert store.get_file(old_id).path.read_bytes() == old_bytes
    assert b"ENCUT = 650" in store.get_file(new_id).path.read_bytes()
    assert changed_nodes["01_relax/POSCAR"]["file_id"] == first_nodes["01_relax/POSCAR"]["file_id"]
    restored_store = FileStore(tmp_path / "files")
    assert restored_store.get_file(old_id).path.read_bytes() == old_bytes
    assert restored_store.get_file(new_id).path.read_bytes() != old_bytes
    repeated = service.generate(nacl_request)
    assert repeated["file_tree"] == first["file_tree"]
    assert service.get_artifact(nacl_request.workflow_id).zip_bytes == old_zip


def test_ids_only_change_response_metadata_not_zip_bytes(monkeypatch, nacl_request):
    pipeline = WorkflowGenerationPipeline()
    current = pipeline.generate(nacl_request)
    tree = pipeline._build_file_tree

    def legacy_tree(workflow_id, files):
        result = tree(workflow_id, files)
        def relabel(node, counter):
            if node.type == "file":
                counter[0] += 1
                node.file_id = f"file_{counter[0]:02d}"
            for child in node.children:
                relabel(child, counter)
        relabel(result, [0])
        return result
    monkeypatch.setattr(pipeline, "_build_file_tree", legacy_tree)
    legacy = pipeline.generate(nacl_request)
    assert legacy.bundle.zip_bytes == current.bundle.zip_bytes
    assert legacy.bundle.files == current.bundle.files
    assert legacy.bundle.manifest == current.bundle.manifest
    assert legacy.file_tree != current.file_tree


@pytest.mark.parametrize("legacy_id", ["file_01", "file_g1_bad", "file_g1_" + "g" * 64, "file_g2_" + "a" * 64])
def test_legacy_generated_records_fail_closed_after_persisted_reload(tmp_path, legacy_id):
    root = tmp_path / "files"
    original = FileStore(root)
    original.register_file(legacy_id, "POSCAR", "generated", b"old ambiguous bytes\n")
    index_before = (tmp_path / "structs.index.json").read_bytes()
    restored = FileStore(root)
    with pytest.raises(NotFoundError) as error:
        restored.get_file(legacy_id)
    assert error.value.code == "FILE_NOT_FOUND"
    assert error.value.message == "旧版生成文件预览无法确认归属，请重新生成工作流"
    assert (root / legacy_id).read_bytes() == b"old ambiguous bytes\n"
    assert (tmp_path / "structs.index.json").read_bytes() == index_before
    assert legacy_id in json.loads(index_before)["files"]


def test_generated_registration_refuses_rebinding_but_ordinary_registration_unchanged(tmp_path):
    store = FileStore(tmp_path / "files")
    identity = generated_file_id("wf_a", "INCAR", b"ENCUT = 520\n")
    stored = store.register_file(identity, "INCAR", "generated", b"ENCUT = 520\n")
    original_index = (tmp_path / "structs.index.json").read_bytes()
    with pytest.raises(ConflictError) as error:
        store.register_file(identity, "INCAR", "generated", b"ENCUT = 650\n")
    assert error.value.code == "GENERATED_FILE_ID_CONFLICT"
    assert stored.path.read_bytes() == b"ENCUT = 520\n"
    assert (tmp_path / "structs.index.json").read_bytes() == original_index
    assert store.register_file(identity, "INCAR", "generated", b"ENCUT = 520\n").sha256 == stored.sha256
    ordinary = store.register_file("file_custom_upload", "POSCAR", "poscar", b"first")
    store.register_file(ordinary.file_id, "POSCAR", "poscar", b"second")
    assert store.get_file(ordinary.file_id).path.read_bytes() == b"second"
