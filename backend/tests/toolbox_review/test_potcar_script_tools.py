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
        # 最重要的那条声明：本智能体不提供任何 POTCAR（避免版权问题）
        assert "不提供、不分发任何 POTCAR" in card["reason"]
        assert card["risk"] == "high"
        assert f"{ROOT}/relax/POTCAR" not in hpc.files  # 确认前不生成

        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "executed", saved
        assert f"{ROOT}/relax/POTCAR" in hpc.files
        assert "数据集 Si" in saved["result"]
        # 面向用户的回执要短：不带哈希、不带绝对路径（细节留在卡片与 flow 记录里）
        assert "SHA-256" not in saved["result"] and "/review/" not in saved["result"]
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
        # 用交互式菜单兜底：回执不含命令细节，但调用记录里能看到走了哪条路径
        assert any(command.startswith("printf") and "POTCAR" not in command
                   for command, _cwd in hpc.run_calls)


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
        # 「复制即认领」：批准复制卡本身就把该脚本登记为本次提交脚本（不再需要额外认领）
        attestation = flow["script_attestations"]["relax"]
        assert attestation["sha256"] == digest
        assert attestation["claimed_by"] == "script_deploy"
        assert attestation["script_name"] == "run.sh"
        assert attestation["directory"] == f"{ROOT}/relax"
        assert attestation["source"] == "remote"


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
    """目录里已是同一份副本：不再写入，但照样出一张卡——批准即完成认领。"""
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/run.sh"] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "deploy_submit_script", {"job_key": "relax"})
        card = out["pending"]
        assert card and "不再写入" in card["summary"]
        writes_before = list(hpc.write_calls)
        saved = resolve_card(api, pid, tid, card["card_id"], True)["card"]
        assert saved["state"] == "executed", saved
        assert hpc.write_calls == writes_before          # 没有重复写入
        assert "没有重复写入" in saved["result"]
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        assert flow["script_attestations"]["relax"]["claimed_by"] == "script_deploy"


def test_deploy_card_attests_so_precheck_needs_no_extra_claim(tmp_path):
    """复制即认领：批准复制卡后，预检不该再要求用户额外认领一次。"""
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[f"{ROOT}/relax/POTCAR"] = VALID_INPUTS["POTCAR"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        card = call_tool(api, pid, tid, "deploy_submit_script",
                         {"job_key": "relax"})["pending"]
        resolve_card(api, pid, tid, card["card_id"], True)
        out = call_tool(api, pid, tid, "precheck", {"job_key": "relax"})
        assert out["ok"] is True, out.get("result")
        assert "尚未由用户认领" not in str(out.get("result"))


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


def test_template_identical_script_is_auto_attested_by_precheck(tmp_path):
    """用户在设置里配了模板 → 与模板逐字节一致的脚本**直接算已认领**，
    预检不该再让用户去手工点认领（用户已明确：开关打开＝默认认领）。"""
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[f"{ROOT}/relax/POTCAR"] = VALID_INPUTS["POTCAR"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        # 用户此前已经自己复制过（脚本就在作业目录里，没有任何认领记录）
        hpc.files[f"{ROOT}/relax/run.sh"] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "precheck", {"job_key": "relax"})
        assert out["ok"] is True, out.get("result")
        assert "已自动认领" in str(out.get("result"))
        assert "尚未" not in str(out.get("result"))
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        attestation = flow["script_attestations"]["relax"]
        assert attestation["claimed_by"] == "template_match"
        assert attestation["sha256"] == hashlib.sha256(TEMPLATE_BYTES).hexdigest()


def test_template_auto_attest_requires_switch_and_matching_bytes(tmp_path):
    """开关没开、模板没配、或脚本与模板不一致 → 一律不自动认领。"""
    # ① 开关没开：即使脚本与模板一致也不认领
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[f"{ROOT}/relax/POTCAR"] = VALID_INPUTS["POTCAR"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/run.sh"] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "precheck", {"job_key": "relax"})
        assert out["ok"] is False
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        assert not (flow.get("script_attestations") or {}).get("relax")

    # ② 开关打开但脚本与模板不一致：同样不认领
    hpc = VaspkitHPC()
    with _setup(tmp_path / "b", hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[f"{ROOT}/relax/POTCAR"] = VALID_INPUTS["POTCAR"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/run.sh"] = b"#!/bin/bash\necho someone-else\n"
        out = call_tool(api, pid, tid, "precheck", {"job_key": "relax"})
        assert out["ok"] is False
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        assert not (flow.get("script_attestations") or {}).get("relax")


def test_precheck_draft_no_longer_asks_user_to_claim_when_template_matches(tmp_path):
    """draft 也不该再出「认领卡」：模板一致的脚本已由系统自动认领。"""
    hpc = VaspkitHPC()
    with _setup(tmp_path, hpc, allow_script_deploy=True,
                submit_script_template=TEMPLATE) as (_c, api, hpc, pid, tid):
        _remote_inputs(hpc)
        hpc.files[f"{ROOT}/relax/POTCAR"] = VALID_INPUTS["POTCAR"]
        hpc.files[TEMPLATE] = TEMPLATE_BYTES
        hpc.files[f"{ROOT}/relax/run.sh"] = TEMPLATE_BYTES
        out = call_tool(api, pid, tid, "draft", {"job_key": "relax"})
        assert out["pending"] is None or out["pending"].get("kind") != "script_attestation"
        flow = api.app.state.toolbox.require_task(pid, tid)["flow"]
        assert flow["script_attestations"]["relax"]["claimed_by"] == "template_match"


def test_script_template_normalization():
    assert normalize_submit_script_template("/home/u/tpl/run.sh") == "/home/u/tpl/run.sh"
    for bad in ("", "run.sh", "C:/tpl/run.sh", "/home/u/run.txt",
                "/home/u/../run.sh", "/home/u/tpl/", "/home/u/.hidden.sh"):
        assert normalize_submit_script_template(bad) == ""
