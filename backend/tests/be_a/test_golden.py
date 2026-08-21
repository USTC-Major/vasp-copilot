"""黄金产物测试：Fe2O3（磁性+DFT+U）与 NaCl（简单非金属）全流程字节级比对。

重新生成黄金产物：
    BEA_UPDATE_GOLDEN=1 python -m pytest backend/tests/be_a/test_golden.py -q
"""

import os
from pathlib import Path

import pytest

from backend.app.workflow.pipeline import WorkflowGenerationPipeline

GOLDEN_ROOT = Path(__file__).resolve().parent / "golden"


def _golden_dir(case: str) -> Path:
    return GOLDEN_ROOT / case


def _write_golden(case: str, files) -> None:
    base = _golden_dir(case)
    for relative_path, data in files.items():
        target = base / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _assert_matches_golden(case: str, files) -> None:
    base = _golden_dir(case)
    expected_paths = {
        str(path.relative_to(base)).replace("\\", "/")
        for path in base.rglob("*")
        if path.is_file() and path.name != "bundle.zip"
    }
    assert expected_paths == set(files), (
        f"{case}: 产物文件集合与黄金产物不一致；"
        f"多出={set(files) - expected_paths}，缺失={expected_paths - set(files)}"
    )
    for relative_path, data in files.items():
        expected = (base / relative_path).read_bytes()
        assert data == expected, f"{case}/{relative_path} 与黄金产物不一致（确定性被破坏）"


@pytest.mark.parametrize("case,fixture", [("fe2o3", "fe2o3_request"), ("nacl", "nacl_request")])
def test_golden_bundle(case, fixture, request):
    generate_request = request.getfixturevalue(fixture)
    result = WorkflowGenerationPipeline().generate(generate_request)
    if os.environ.get("BEA_UPDATE_GOLDEN") == "1":
        _write_golden(case, result.bundle.files)
        pytest.skip(f"golden updated for {case}")
    assert _golden_dir(case).exists(), (
        f"缺少黄金产物 {case}：请运行 "
        "BEA_UPDATE_GOLDEN=1 python -m pytest backend/tests/be_a/test_golden.py -q"
    )
    _assert_matches_golden(case, result.bundle.files)


def test_golden_zip_is_byte_identical(request):
    fe2o3_request = request.getfixturevalue("fe2o3_request")
    result = WorkflowGenerationPipeline().generate(fe2o3_request)
    golden_zip = _golden_dir("fe2o3") / "bundle.zip"
    if os.environ.get("BEA_UPDATE_GOLDEN") == "1":
        golden_zip.parent.mkdir(parents=True, exist_ok=True)
        golden_zip.write_bytes(result.bundle.zip_bytes)
        pytest.skip("golden zip updated")
    if not golden_zip.exists():
        pytest.skip("golden zip 尚未生成")
    assert result.bundle.zip_bytes == golden_zip.read_bytes()
