"""M9 工具层测试：vaspkit 探测/技能 + SLURM 模板 + 提交草稿（纯内存，不碰网络）。"""

import json

import pytest

from ai_mode.jobs import Job, JobStatus, Scheduler
from ai_mode import paths
from ai_mode.tools import (
    DIRECTIVE_ALLOWLIST,
    SubmissionDraft,
    SubmissionDraftBuilder,
    default_directives,
    make_draft_only_submitter,
    parse_version,
    potcar_menu_code,
    probe_and_store,
    probe_vaspkit,
    render_sbatch,
    store_path,
    submit_command,
    validate_directives,
    vasp_input_menu_code,
)
from ai_mode.tools.draft import SUBMIT_BIN


class FakeToolRunner:
    """内存版 run：按脚本顺序返回预设应答。签名兼容 (cmd, **kw) -> (code, out, err)。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.asked = []

    def __call__(self, command, **kwargs):
        self.asked.append(command)
        if self.responses:
            return self.responses.pop(0)
        return 0, "", ""


# ---------------- vaspkit 探测 ----------------
def test_probe_found_flat_menu():
    """老版本：菜单扁平，POTCAR 行直接出现在同一份输出里。"""
    runner = FakeToolRunner([
        (0, "/usr/bin/vaspkit\n", ""),                       # which
        (0, "VASPKIT Standard Edition 1.3.2\n"
            " 101) 102) 301) 401) Submit 501) 700) 711)\n"
            " 401) Generate POTCAR file\n", ""),             # 主菜单
    ])
    skill = probe_vaspkit(runner)
    assert skill.found is True
    assert skill.path == "/usr/bin/vaspkit"
    assert skill.version == "1.3.2"
    assert "structure" in skill.tasks and "kpoints" in skill.tasks
    assert skill.potcar_code == "401"


def test_probe_found_two_level_menu_like_1_5_1():
    """实测 1.5.1：`-v`/`-h` 是非法参数，POTCAR 在「VASP Input-Files Generator」二级菜单。"""
    main = ("VASPKIT Standard Edition 1.5.1 (27 Jan. 2024)\n"
            " 01) VASP Input-Files Generator    02) Mechanical Properties\n"
            " 0)  Quit\n")
    sub = (" ==================== VASP Input Files Options ===================\n"
           " 101) Customize INCAR File\n"
           " 102) Generate KPOINTS File for SCF Calculation\n"
           " 103) Generate POTCAR File with Default Setting\n"
           " 104) Generate POTCAR File with User Specified Potential\n")
    runner = FakeToolRunner([(0, "/opt/vaspkit\n", ""),   # which
                             (0, main, ""),               # printf '0\n' | vaspkit
                             (0, sub, "")])               # printf '01\n0\n' | vaspkit
    skill = probe_vaspkit(runner)
    assert skill.version == "1.5.1"
    assert skill.potcar_code == "103" and skill.potcar_only is True
    assert skill.tasks["potcar"] == ["103"]
    assert runner.asked[2] == "printf '01\\n0\\n' | /opt/vaspkit"


def test_potcar_menu_code_prefers_default_setting():
    text = (" 103) Generate POTCAR File with Default Setting\n"
            " 104) Generate POTCAR File with User Specified Potential\n")
    assert potcar_menu_code(text) == ("103", True)
    assert potcar_menu_code(
        " 104) Generate POTCAR File with User Specified Potential\n") == ("104", True)
    assert potcar_menu_code(
        " 103) Generate VASP input files (INCAR/KPOINTS/POTCAR)\n") == ("103", False)
    assert parse_version("| VASPKIT Standard Edition 1.5.1 (27 Jan. 2024) |") == "1.5.1"
    assert vasp_input_menu_code(" 01) VASP Input-Files Generator   02) Mechanical\n") == "01"


def test_probe_not_found():
    runner = FakeToolRunner([(127, "", "command not found")])
    skill = probe_vaspkit(runner)
    assert skill.found is False or skill.found is False  # 保持 found=False


def test_probe_found_but_version_fails():
    runner = FakeToolRunner([(0, "/opt/vaspkit/bin/vaspkit\n", ""),  # which
                             (0, "", ""),                            # 主菜单为空
                             (1, "", "no such option"),              # -h 失败
                             (0, "301\n", ""),                       # -v
                             ])
    skill = probe_vaspkit(runner)
    assert skill.found is True
    assert skill.version == ""
    assert "kpoints" in skill.tasks


def test_probe_exception_is_not_found():
    def boom(command, **kw):
        raise RuntimeError("net down")
    skill = probe_vaspkit(boom)
    assert skill.found is False


def test_probe_and_store_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    runner = FakeToolRunner([(0, "/bin/vaspkit\n", ""),
                             (0, "VASPKIT Standard Edition 2.1.5\n 101) 401)\n", "")])
    skill = probe_and_store(runner, root=tmp_path)
    skill_path = store_path(tmp_path)
    assert skill_path.is_file()
    assert skill.found is True and skill.version == "2.1.5"
    loaded = json.loads(skill_path.read_text(encoding="utf-8"))
    assert loaded["path"] == "/bin/vaspkit"
    from ai_mode.tools.vaspkit import VaspkitSkill
    restored = VaspkitSkill.from_dict(loaded)
    assert restored.found and restored.tasks["structure"] == ["101"]


def test_store_path_defaults_under_skills():
    assert store_path().name == "vaspkit.json"
    assert store_path().parent.parent == paths.home_dir()


def test_task_detection_ignores_when_no_numbers():
    runner = FakeToolRunner([(0, "/bin/vaspkit\n", ""), (0, "", ""), (0, "", ""),
                             (0, "Help info", "")])
    skill = probe_vaspkit(runner)
    assert skill.tasks == {}
    assert "未探测到具体任务号" in skill.notes


# ---------------- SLURM 模板 ----------------
def test_directives_valid():
    d = {"nodes": "2", "time": "01:00:00", "partition": "gpu",
         "output": "run.out", "job-name": "relax_1"}
    assert validate_directives(d) == []


def test_directives_with_prefix_ok():
    d = {"--nodes": "1", "--time": "12:00:00"}
    assert validate_directives(d) == []
    assert "--nodes=1" in render_sbatch(d, body="srun vasp_std")


@pytest.mark.parametrize("directives,needle", [
    ({"bogus": "1"}, "未知 sbatch 指令"),
    ({"nodes": "0"}, "非法指令值"),
    ({"nodes": "abc"}, "非法指令值"),
    ({"time": "1:00"}, "非法指令值"),
    ({"job-name": "a;b"}, "非法指令值"),
    ({"output": "/tmp/x"}, "非法指令值"),
    ({"output": "../x"}, "非法指令值"),
    ({"time": "10:00:00\nrm -rf /"}, "非法指令值(含控制字符)"),
])
def test_directives_invalid(directives, needle):
    issues = validate_directives(directives)
    assert any(needle in issue for issue in issues)


def test_render_sbatch_header_and_body():
    text = render_sbatch({"nodes": "1"}, body="srun vasp_std\necho done",
                         extra_comments="job j1")
    assert text.startswith("#!/bin/bash\n# job j1")
    assert "#SBATCH --nodes=1" in text
    assert "srun vasp_std" in text and "echo done" in text
    assert text.endswith("\n")


def test_render_sbatch_rejects_invalid():
    with pytest.raises(ValueError):
        render_sbatch({"nodes": "0"})


def test_default_directives():
    d = default_directives()
    assert d["nodes"] == "1"
    d2 = default_directives("myjob")
    assert d2["job-name"] == "myjob"


def test_allowlist_has_basics():
    assert "job-name" in DIRECTIVE_ALLOWLIST
    assert "time" in DIRECTIVE_ALLOWLIST


# ---------------- 提交草稿 ----------------
def _job(job_id="j1", name="relax_1", workdir="/home/u/calc"):
    return Job(job_id=job_id, name=name, workdir=workdir)


def test_draft_build_script():
    b = SubmissionDraftBuilder()
    draft = b.build(_job())
    assert draft.job_id == "j1"
    assert draft.calc_dir == "/home/u/calc"
    assert draft.script_name == "submit_j1.sh"
    assert draft.submit_cmd == [SUBMIT_BIN, "submit_j1.sh"]
    assert "#!/bin/bash" in draft.script_text
    assert "#SBATCH --job-name=relax_1" in draft.script_text
    assert "srun vasp_std" in draft.script_text
    assert "j1.out" in draft.script_text and "j1.err" in draft.script_text


def test_draft_never_executes():
    b = SubmissionDraftBuilder()
    j = _job()
    d = b.build(j)
    # 纯文本生成：不触发任何 runner/远端；job 未被标记提交
    assert j.status == JobStatus.DRAFT
    assert d.submit_cmd[0] == SUBMIT_BIN


def test_make_submitter_writes_preview_only(tmp_path):
    b = SubmissionDraftBuilder()
    sub = make_draft_only_submitter(b, write_dir=tmp_path)
    job = _job()
    result = sub(job)
    assert result is None                                    # 不返回 slurm_id（不真提交）
    assert job.slurm_id is None
    preview = tmp_path / "submit_j1.sh"
    assert preview.is_file() and "#SBATCH" in preview.read_text(encoding="utf-8")
    assert job.extra["draft"]["job_id"] == "j1"


def test_build_without_job_id_raises():
    b = SubmissionDraftBuilder()
    with pytest.raises(ValueError):
        b.build(Job(job_id="", name="x"))


def test_draft_dict_roundtrip():
    d = SubmissionDraftBuilder().build(_job())
    restored = SubmissionDraft.from_dict(d.to_dict())
    assert restored.job_id == d.job_id
    assert restored.script_text == d.script_text
    assert restored.submit_cmd == d.submit_cmd


def test_submit_command_binary():
    assert submit_command("run.sh") == ["sbatch", "run.sh"]
