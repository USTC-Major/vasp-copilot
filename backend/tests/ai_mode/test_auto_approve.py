"""免批范围回归：全局设置开启后，仅白名单内的机械操作自动执行。"""
import json
from types import SimpleNamespace

import pytest

from ai_mode.config import AiModeConfig
from backend.tests.toolbox.legacy_bridge import ProjectStore, ToolExecutor
from backend.toolbox.config import load_settings as toolbox_load
from backend.toolbox.config import normalize_auto_approve_kinds


def make_ctx(tmp_path, monkeypatch, kinds=None):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    store = ProjectStore(tmp_path / "home")
    pid = store.create_project("免批范围")["id"]
    root = tmp_path / "workspace"
    root.mkdir()
    tid = store.create_task(pid, goal="Si", local_workspace=str(root))["id"]
    cfg = AiModeConfig(data_dir=tmp_path / "data", auto_approve_kinds=list(kinds or []))
    ex = ToolExecutor(store=store, project_id=pid, task_id=tid, cfg=cfg)
    return SimpleNamespace(store=store, pid=pid, tid=tid, root=root, ex=ex, cfg=cfg)


@pytest.fixture
def bare(tmp_path, monkeypatch):
    return make_ctx(tmp_path, monkeypatch)


@pytest.fixture
def authorized(tmp_path, monkeypatch):
    return make_ctx(tmp_path, monkeypatch,
                    ["copy_inputs", "generate_kpoints", "hpc_upload"])


def plan_relax(ctx):
    ctx.store.update_task(ctx.pid, ctx.tid, flow={"plan": {"jobs": [{"key": "relax"}]}})


GENERATE = {"job_key": "relax", "grid": [4, 4, 4], "centering": "Gamma"}


def test_generate_kpoints_runs_without_card_when_authorized(authorized):
    plan_relax(authorized)
    result = authorized.ex.handle("generate_kpoints", dict(GENERATE))
    assert result.startswith("[AUTO_APPROVED]"), result
    assert (authorized.root / "relax" / "KPOINTS").is_file()


def test_generate_kpoints_still_asks_when_not_authorized(bare):
    plan_relax(bare)
    result = bare.ex.handle("generate_kpoints", dict(GENERATE))
    assert result.startswith("__CONSENT_PENDING__"), result
    assert not (bare.root / "relax" / "KPOINTS").exists()


def test_scientific_card_ignores_scope(authorized):
    """科学输入永不免批：即使开了范围，INCAR 写入仍然出卡。"""
    plan_relax(authorized)
    result = authorized.ex.handle("propose_incar",
                                  {"job_key": "relax",
                                   "entries": [{"tag": "ENCUT", "value": 520}]})
    assert result.startswith("__CONSENT_PENDING__"), result
    assert not (authorized.root / "relax" / "INCAR").exists()


def test_script_and_submit_are_never_in_scope(bare):
    """白名单外的一切（脚本、提交、结构导入等）不接受免批配置。"""
    assert normalize_auto_approve_kinds(["submit", "draft", "propose_incar",
                                         "retry_job"]) == []
    assert normalize_auto_approve_kinds(["copy_inputs", "submit"]) == ["copy_inputs"]
    # 上传属于机械传输，可免批；顺序固定为白名单顺序
    assert normalize_auto_approve_kinds(["hpc_upload", "copy_inputs"]) == \
        ["copy_inputs", "hpc_upload"]


def test_copy_inputs_runs_without_card_when_authorized(authorized):
    plan_relax(authorized)
    for name in ("INCAR", "POSCAR", "KPOINTS"):
        (authorized.root / name).write_text(f"{name}\n", encoding="utf-8")
    authorized.ex.handle("get_state", {})
    flow = authorized.store.get_task(authorized.pid, authorized.tid)["flow"]
    artifact_ids = list(flow["artifacts"].keys())
    assert len(artifact_ids) >= 3
    result = authorized.ex.handle("copy_inputs",
                                  {"artifact_ids": artifact_ids, "job_key": "relax"})
    assert result.startswith("[AUTO_APPROVED]"), result
    assert (authorized.root / "relax" / "INCAR").is_file()


def test_unknown_kinds_in_settings_file_are_ignored(tmp_path, monkeypatch):
    """配置文件里写入未知类型 → 加载时归一化为空，退回逐项确认。"""
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path / "home"))
    path = tmp_path / "home" / "toolbox_config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auto_approve_kinds": ["copy_inputs", "submit"]}),
                    encoding="utf-8")
    cfg = toolbox_load(config_path=path)
    assert cfg.auto_approve_kinds == ["copy_inputs"]
