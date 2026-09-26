"""POTCAR（vaspkit）与提交脚本模板复制：开关、卡片、哈希校验与拒绝覆盖。"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager

from fastapi.testclient import TestClient

from backend.tests.toolbox_review.conftest import (
    ApiHarness,
    FakeHPC,
    call_tool,
    create_isolated_app,
    create_project_task,
    resolve_card,
)
from backend.tests.valid_vasp_inputs import FILES as VALID_INPUTS
from backend.toolbox.config import (normalize_auto_approve_kinds,
                                    normalize_submit_script_template)

ROOT = "/review/calc"
VASP_BIN = "/opt/vaspkit/bin/vaspkit"
MENU = (
    "             VASPKIT 1.5                      \n"
    " 103) Generate POTCAR file from POSCAR (need POTCAR library)\n"
    " 102) Generate KPOINTS file                       \n"
    " 501) Submit VASP job                             \n"
)
TEMPLATE = "/review/templates/run.sh"
TEMPLATE_BYTES = b"#!/bin/bash\n#SBATCH --job-name=demo\ncp ../CONTCAR POSCAR\n"


class VaspkitHPC(FakeHPC):
    """在 FakeHPC 上模拟 vaspkit：探测菜单，并按菜单号在 cwd 里写出 POTCAR。"""

    def __init__(self, *, found: bool = True, menu: str = MENU,
                 potcar: bytes = VALID_INPUTS["POTCAR"], task_supported: bool = True):
        super().__init__()
        self.found = found
        self.menu = menu
        self.potcar = potcar
        self.task_supported = task_supported

    def run(self, command, *, cwd=None, timeout=None):
        del timeout
        self.run_calls.append((command, cwd))
        if "which vaspkit" in command:
            return (0, f"{VASP_BIN}\n", "") if self.found else (1, "", "")
        if command.startswith(f"{VASP_BIN} -v"):
            return 0, "VASPKIT 1.5 (2023)\n", ""
        if command.startswith(f"{VASP_BIN} -h"):
            return 0, self.menu, ""
        if command.startswith("echo 0 |"):
            return 0, self.menu, ""
        if command.startswith(f"{VASP_BIN} -task"):
            if not self.task_supported:
                return 1, "", "unknown option -task"
            assert cwd, "vaspkit 必须在作业目录里运行"
            self.files[f"{cwd}/POTCAR"] = self.potcar
            return 0, "POTCAR generated\n", ""
        if command.startswith("printf") and "|" in command and cwd:
            self.files[f"{cwd}/POTCAR"] = self.potcar
            return 0, "POTCAR generated\n", ""
        return 0, "", ""


@contextmanager
def _setup(tmp_path, hpc, **config):
    root = tmp_path / "owner"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("INCAR", "POSCAR", "KPOINTS"):
        (workspace / name).write_bytes(VALID_INPUTS[name])
    app, fake = create_isolated_app(root, hpc, **config)
    with TestClient(app) as client:
        api = ApiHarness(client=client, app=app, root=root,
                         workspace=workspace, hpc=fake)
        project, task = create_project_task(api, hpc_workspace=ROOT)
        pid, tid = project["id"], task["id"]
        call_tool(api, pid, tid, "plan", {"strategy": "t", "jobs": [
            {"key": "relax", "label": "结构优化", "kind": "relax", "requires": []}]})
        yield client, api, fake, pid, tid


def _remote_inputs(hpc, job_dir=f"{ROOT}/relax"):
    for name in ("INCAR", "POSCAR", "KPOINTS"):
        hpc.files[f"{job_dir}/{name}"] = VALID_INPUTS[name]


# ---------------- POTCAR：开关与前置条件 ----------------
def test_potcar_tool_requires_the_global_switch(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc) as (_client, api, hpc, pid, tid):
        _remote_inputs(hpc)
        out = call_tool(api, pid, tid, "generate_potcar", {"job_key": "relax"})
        assert out["ok"] is False and out["pending"] is None
        assert out["error"]["code"] == "AI_TOOL_NOT_ALLOWED"
        assert hpc.run_calls == []


def test_potcar_tool_reports_missing_vaspkit(tmp_path):
    hpc = VaspkitHPC(found=False)
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        out = call_tool(api, pid, tid, "generate_potcar", {"job_key": "relax"})
        assert out["ok"] is False
        assert out["error"]["code"] == "VASPKIT_NOT_FOUND"
        assert not [command for command, _cwd in hpc.run_calls if "-task" in command]


def test_potcar_tool_reports_unreadable_menu(tmp_path):
    hpc = VaspkitHPC(menu="no numbered menu here\n")
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        out = call_tool(api, pid, tid, "generate_potcar", {"job_key": "relax"})
        assert out["ok"] is False
        assert out["error"]["code"] == "VASPKIT_MENU_UNKNOWN"


# ---------------- POTCAR：卡片与执行 ----------------
def test_potcar_card_then_generation_records_datasets_and_hash(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (client, api, hpc, pid, tid):
        _remote_inputs(hpc)
        card = call_tool(api, pid, tid, "generate_potcar", {"job_key": "relax"})["pending"]
        assert card and card["kind"] == "potcar_generate"
        assert "菜单号 103" in card["summary"]
        assert "Si" in card["summary"] and "vaspkit 自己决定" in card["summary"]
        assert card["risk"] == "high"
        assert f"{ROOT}/relax/POTCAR" not in hpc.files  # 确认前不生成

        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "executed", saved
        assert f"{ROOT}/relax/POTCAR" in hpc.files
        assert "数据集 Si" in saved["result"] and "SHA-256" in saved["result"]
        assert "Synthetic test metadata" not in saved["result"]  # 不回显 POTCAR 内容
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        record = flow["potcar_generations"]["relax"]
        assert record["datasets"] == ["Si"] and record["species"] == ["Si"]
        assert record["sha256"] == hashlib.sha256(VALID_INPUTS["POTCAR"]).hexdigest()
        assert client.app is not None


def test_potcar_generation_falls_back_to_the_interactive_menu(tmp_path):
    """老版本 vaspkit 不支持 -task 时，退回「菜单号 + 默认回车」，产物仍要校验。"""
    hpc = VaspkitHPC(task_supported=False)
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        card = call_tool(api, pid, tid, "generate_potcar",
                         {"job_key": "relax"})["pending"]
        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "executed", saved
        assert f"{ROOT}/relax/POTCAR" in hpc.files
        assert "菜单 103" in saved["result"]


def test_potcar_generation_refuses_when_poscar_changed(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        card = call_tool(api, pid, tid, "generate_potcar",
                         {"job_key": "relax"})["pending"]
        hpc.files[f"{ROOT}/relax/POSCAR"] = VALID_INPUTS["POSCAR"].replace(b"Si", b"Ge")
        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "failed"
        assert "POSCAR 在确认后发生变化" in saved["result"]
        assert f"{ROOT}/relax/POTCAR" not in hpc.files


def test_potcar_generation_rejects_mismatched_potcar(tmp_path):
    """vaspkit 产物与 POSCAR 元素不一致时必须失败，不能当成功上报。"""
    bad = b"TITEL = PAW_PBE Ge\nVRHFIN =Ge:\nx\nEnd of Dataset\n"
    hpc = VaspkitHPC(potcar=bad)
    with _setup(tmp_path, hpc, allow_potcar_assembly=True) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        card = call_tool(api, pid, tid, "generate_potcar",
                         {"job_key": "relax"})["pending"]
        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "failed"
        assert "未通过校验" in saved["result"]


def test_potcar_is_never_auto_approved(tmp_path):
    """POTCAR 属科学输入：免批开关写满也必须逐次确认（白名单根本不接收它）。"""
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_potcar_assembly=True,
                auto_approve_kinds=("copy_inputs", "generate_kpoints", "hpc_upload",
                                    "potcar_generate")) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        out = call_tool(api, pid, tid, "generate_potcar", {"job_key": "relax"})
        assert out["pending"] and out["pending"]["kind"] == "potcar_generate"
        assert f"{ROOT}/relax/POTCAR" not in hpc.files
    assert normalize_auto_approve_kinds(["potcar_generate", "script_deploy"]) == []


def test_remote_path_bounds_helper():
    from backend.toolbox.commands import ToolExecutor

    assert ToolExecutor._remote_under("/a/b/c", "/a/b") is True
    assert ToolExecutor._remote_under("/a/b", "/a/b") is True
    assert ToolExecutor._remote_under("/a/bc", "/a/b") is False
    assert ToolExecutor._remote_under("/x/y", "/a/b") is False


# ---------------- 提交脚本模板 ----------------
def test_script_deploy_requires_switch(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        assert out["ok"] is False and out["error"]["code"] == "AI_TOOL_NOT_ALLOWED"
        assert hpc.files.get(f"{ROOT}/relax/run.sh") is None


def test_script_deploy_requires_a_configured_template(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True) as (_c, api, hpc, pid, tid):
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        assert out["ok"] is False
        assert out["error"]["code"] == "SCRIPT_TEMPLATE_NOT_CONFIGURED"


def test_script_template_must_exist_on_the_remote(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        assert out["ok"] is False
        assert out["error"]["code"] == "SCRIPT_TEMPLATE_MISSING"


def test_script_deploy_card_copies_bytes_verbatim(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        card = call_tool(api, pid, tid, "deploy_submit_script",
                         {"job_key": "relax"})["pending"]
        assert card["kind"] == "script_deploy"
        assert "逐字节复制" in card["summary"] and TEMPLATE in card["summary"]
        assert f"{ROOT}/relax/run.sh" not in hpc.files  # 确认前不写

        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "executed", saved
        assert hpc.files[f"{ROOT}/relax/run.sh"] == TEMPLATE_BYTES
        digest = hashlib.sha256(TEMPLATE_BYTES).hexdigest()
        assert digest[:16] in saved["result"]
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        assert flow["script_deploys"]["relax"]["sha256"] == digest


def test_script_deploy_never_overwrites_an_existing_script(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/user.sh"] = b"#!/bin/bash\necho user\n"
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        assert out["ok"] is False and out["pending"] is None
        assert out["error"]["code"] == "SCRIPT_ALREADY_PRESENT"
        assert hpc.files[f"{ROOT}/relax/user.sh"] == b"#!/bin/bash\necho user\n"


def test_script_deploy_is_idempotent_for_the_same_copy(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/run.sh"] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        assert out["ok"] is True and out["pending"] is None
        assert "没有重复复制" in out["result"]


def test_script_deploy_refuses_when_template_changed(tmp_path):
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        card = call_tool(api, pid, tid, "deploy_submit_script",
                         {"job_key": "relax"})["pending"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES + b"# changed\n"
        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "failed"
        assert "模板脚本在确认后发生变化" in saved["result"]
        assert f"{ROOT}/relax/run.sh" not in hpc.files


def test_script_template_normalization():
    assert normalize_submit_script_template("/home/u/tpl/run.sh") == "/home/u/tpl/run.sh"
    for bad in ("", "run.sh", "C:/tpl/run.sh", "/home/u/run.txt",
                "/home/u/../run.sh", "/home/u/tpl/", "/home/u/.hidden.sh"):
        assert normalize_submit_script_template(bad) == ""
