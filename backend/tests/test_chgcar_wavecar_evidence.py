"""CHGCAR/WAVECAR 存在性证据链集成测试（真实临时目录，不手工构造 ParsedRunData）。

覆盖：
- 实际存在的 CHGCAR/WAVECAR 以规范名写入 ``ParsedRunData.source_files``；
- ``ICHARG=11`` + CHGCAR 存在时不再误报 ``ICHARG11_CHGCAR_MISSING``；
- Line-mode + ICHARG=11 + CHGCAR 齐备时不再误报 ``KPOINTS_LINE_MODE_WITHOUT_STATIC``；
- CHGCAR 缺失时仍正确报警；
- 备份/派生变体（WAVECAR.1、CHGCAR.old、CHGCAR_sum）不算主文件证据；
- 新增证据路径仅调用 ``is_file``，不经 ``_read``/``read_text``/任何 parser。

说明（既有边界，本轮不修改）：完整 ``detect_files`` 仍会按既有规则对
≤256 MiB 的文件计算流式 SHA256 指纹，>256 MiB 不读取内容并返回 sha256=None。
本文件断言的是"新增 source_files 存在性证据路径不读取正文"，不宣称整个
诊断过程绝对不读取任何文件字节。
"""

from __future__ import annotations

import pathlib

import pytest

import app.services.diagnosis_service as svc_mod
from app.services.diagnosis_service import DiagnosisService, _load_parsed

INCAR_ICHARG11 = "SYSTEM = band from static\nICHARG = 11\nENCUT = 520\n"
INCAR_NORMAL = "SYSTEM = relax\nENCUT = 520\nIBRION = 2\nNSW = 100\n"
POSCAR_TEXT = (
    "NaCl\n1.0\n5.6 0.0 0.0\n0.0 5.6 0.0\n0.0 0.0 5.6\n"
    "Na Cl\n1 1\nDirect\n0.0 0.0 0.0\n0.5 0.5 0.5\n"
)
# 合法 VASP Line-mode KPOINTS：四行头 + 端点对 + 段间空行。
LINE_MODE_KPOINTS = (
    "Band path\n60\nLine-mode\nReciprocal\n"
    "0 0 0  ! GAMMA\n0.5 0 0  ! X\n\n0.5 0 0  ! X\n0.5 0.5 0  ! M\n"
)
UNIFORM_KPOINTS = "SCF mesh\n0\nGamma\n8 8 8\n0 0 0\n"
# CHGCAR 正文：非法 UTF-8 二进制垃圾 + 唯一哨兵串。哨兵若出现在报告正文中，
# 说明 CHGCAR 被当作文本读取/解析过。
CHGCAR_SENTINEL = b"SENTINEL_MUST_NOT_BE_PARSED"
CHGCAR_BYTES = b"\x00\xff\xfe\x80\x81\x00\x01\x02" + CHGCAR_SENTINEL + b"\xf0\x9f\xff\x00"
WAVECAR_BYTES = b"\x00\x00\xfe\xffWAVECAR-BINARY-GARBAGE\x80\x81"


def _write_run(base: pathlib.Path, files: dict) -> pathlib.Path:
    base.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        target = base / name
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return base


def _rule_ids(result) -> set:
    return {issue.rule_id for issue in result.issues}


def _count_rule(result, rule_id: str) -> int:
    return sum(1 for issue in result.issues if issue.rule_id == rule_id)


class _FakeCandidate:
    """模拟大小写敏感文件系统上并存的两个 CHGCAR/chgcar 候选。

    Windows 同一目录无法同时创建这两个独立文件，故用受控替身覆盖去重逻辑。
    """

    def __init__(self, name: str):
        self.name = name

    def is_file(self) -> bool:
        return True

    def __lt__(self, other) -> bool:
        return self.name < getattr(other, "name", "")

    def __gt__(self, other) -> bool:
        return self.name > getattr(other, "name", "")

    def __repr__(self) -> str:
        return f"<fake {self.name}>"


class TestChgcarEvidence:
    def test_c1_chgcar_present_recorded_and_no_false_alarm(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "CHGCAR": CHGCAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert "CHGCAR" in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert "ICHARG11_CHGCAR_MISSING" not in _rule_ids(result)

    def test_c2_line_mode_with_chgcar_no_false_alarm(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": LINE_MODE_KPOINTS,
            "CHGCAR": CHGCAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert parsed.kpoints.line_mode is True
        assert parsed.kpoints.mode == "Line-mode"
        assert "CHGCAR" in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert "KPOINTS_LINE_MODE_WITHOUT_STATIC" not in _rule_ids(result)

    def test_c3_missing_chgcar_still_alarms(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
        })
        parsed = _load_parsed(base, None)
        assert "CHGCAR" not in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert _count_rule(result, "ICHARG11_CHGCAR_MISSING") == 1

    def test_c3_line_mode_without_chgcar_still_alarms(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": LINE_MODE_KPOINTS,
        })
        parsed = _load_parsed(base, None)
        assert "CHGCAR" not in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert _count_rule(result, "KPOINTS_LINE_MODE_WITHOUT_STATIC") == 1

    def test_c3_icharg_not_11_without_chgcar_is_silent(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_NORMAL,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
        })
        parsed = _load_parsed(base, None)
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert "ICHARG11_CHGCAR_MISSING" not in _rule_ids(result)


class TestWavecarEvidence:
    def test_c4_wavecar_present_recorded(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_NORMAL,
            "POSCAR": POSCAR_TEXT,
            "WAVECAR": WAVECAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert "WAVECAR" in parsed.source_files

    def test_c4_wavecar_absent_not_recorded(self, tmp_path):
        base = _write_run(tmp_path, {"INCAR": INCAR_NORMAL, "POSCAR": POSCAR_TEXT})
        parsed = _load_parsed(base, None)
        assert "WAVECAR" not in parsed.source_files
        assert "CHGCAR" not in parsed.source_files

    def test_c4_both_present_recorded_once_each(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "CHGCAR": CHGCAR_BYTES,
            "WAVECAR": WAVECAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert parsed.source_files.count("CHGCAR") == 1
        assert parsed.source_files.count("WAVECAR") == 1


class TestVariantAndCaseHandling:
    def test_c5_backup_variants_are_not_primary_evidence(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "WAVECAR.1": WAVECAR_BYTES,
            "CHGCAR.old": CHGCAR_BYTES,
            "CHGCAR_sum": CHGCAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert "CHGCAR" not in parsed.source_files
        assert "WAVECAR" not in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert _count_rule(result, "ICHARG11_CHGCAR_MISSING") == 1

    def test_c6a_lowercase_name_normalized_to_canonical(self, tmp_path):
        """真实目录：仅放小写 chgcar，断言只记录一个规范名 CHGCAR。"""

        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "chgcar": CHGCAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        assert parsed.source_files.count("CHGCAR") == 1
        assert "chgcar" not in parsed.source_files
        result, _body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert "ICHARG11_CHGCAR_MISSING" not in _rule_ids(result)

    def test_c6b_case_sensitive_duplicates_appended_once(self, tmp_path, monkeypatch):
        """受控替身模拟大小写敏感文件系统上 CHGCAR 与 chgcar 并存：最多 append 一次。"""

        base = _write_run(tmp_path, {"INCAR": INCAR_ICHARG11, "POSCAR": POSCAR_TEXT})
        real_iterdir = pathlib.Path.iterdir

        def fake_iterdir(self):
            if self == base:
                return iter([_FakeCandidate("CHGCAR"), _FakeCandidate("chgcar")])
            return real_iterdir(self)

        monkeypatch.setattr(pathlib.Path, "iterdir", fake_iterdir)
        parsed = _load_parsed(base, None)
        assert parsed.source_files.count("CHGCAR") == 1
        assert "chgcar" not in parsed.source_files


class TestEvidencePathDoesNotReadContent:
    def test_c7_new_evidence_path_never_reads_chgcar_or_wavecar(self, tmp_path, monkeypatch):
        """新增 source_files 存在性证据路径仅调用 is_file，不把 CHGCAR/WAVECAR
        传给 ``_read`` 或任何 parser。

        边界：既有 ``detect_files`` 对 ≤256 MiB 文件的流式 SHA256 指纹不在本断言
        范围内（属既有检测层契约，本轮不修改）。
        """

        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": LINE_MODE_KPOINTS,
            "CHGCAR": CHGCAR_BYTES,
            "WAVECAR": WAVECAR_BYTES,
        })
        read_targets = []
        real_read = svc_mod._read

        def spy_read(base_dir, names):
            read_targets.extend(str(name) for name in names)
            return real_read(base_dir, names)

        monkeypatch.setattr(svc_mod, "_read", spy_read)
        parsed = _load_parsed(base, None)
        assert "CHGCAR" in parsed.source_files
        assert "WAVECAR" in parsed.source_files
        assert not any(name.upper() in ("CHGCAR", "WAVECAR") for name in read_targets), (
            f"_read 不应以 CHGCAR/WAVECAR 为目标，实际调用 {read_targets}"
        )

        result, body, _fix = DiagnosisService().run_diagnosis(parsed, base)
        assert "ICHARG11_CHGCAR_MISSING" not in _rule_ids(result)
        assert "KPOINTS_LINE_MODE_WITHOUT_STATIC" not in _rule_ids(result)
        # CHGCAR 正文哨兵不得出现在报告正文中（证明未被当作文本解析）。
        assert CHGCAR_SENTINEL.decode("ascii") not in body

    def test_c7b_parsed_has_no_chgcar_content_field(self, tmp_path):
        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "CHGCAR": CHGCAR_BYTES,
            "WAVECAR": WAVECAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        dumped = parsed.model_dump()
        assert "chgcar" not in dumped
        assert "wavecar" not in dumped
        assert "CHGCAR" in parsed.source_files
        assert "WAVECAR" in parsed.source_files

    def test_c8_full_chain_stays_healthy(self, tmp_path):
        """全链路健全：result/report/fix_files 正常构建，detect_files 分类行为不变。"""

        base = _write_run(tmp_path, {
            "INCAR": INCAR_ICHARG11,
            "POSCAR": POSCAR_TEXT,
            "KPOINTS": UNIFORM_KPOINTS,
            "OUTCAR": "vasp.6.3.0\n run finished.\n",
            "CHGCAR": CHGCAR_BYTES,
            "WAVECAR": WAVECAR_BYTES,
        })
        parsed = _load_parsed(base, None)
        result, body, fix_files = DiagnosisService().run_diagnosis(parsed, base)
        assert result.diagnosis_status.value == "succeeded"
        assert body
        assert isinstance(fix_files, dict)
        kinds = {f.name.upper(): f.kind for f in result.detected_run.files}
        assert kinds["CHGCAR"] == "other_big"
        assert kinds["WAVECAR"] == "other_big"
        assert "ICHARG11_CHGCAR_MISSING" not in _rule_ids(result)


@pytest.mark.parametrize("name", ["CHGCAR", "chgcar", "Chgcar"])
def test_source_files_always_canonical(tmp_path, name):
    base = _write_run(tmp_path, {"INCAR": INCAR_ICHARG11, "POSCAR": POSCAR_TEXT,
                                 name: CHGCAR_BYTES})
    parsed = _load_parsed(base, None)
    assert parsed.source_files.count("CHGCAR") == 1
    assert name not in parsed.source_files or name == "CHGCAR"
