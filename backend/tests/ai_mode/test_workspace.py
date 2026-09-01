# -*- coding: utf-8 -*-
"""M14 增强：本地工作区只读快照测试（真实读盘、有界、跳过无关目录）。"""

from ai_mode.workspace import snapshot_hpc_workspace, snapshot_workspace


def _write(root, rel, text):
    fp = root / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(text, encoding="utf-8")
    return fp


def test_snapshot_lists_files_and_previews(tmp_path):
    incar = _write(tmp_path, "INCAR", "SYSTEM = fe2o3\nENCUT = 520\n")
    _write(tmp_path, "readme.txt", "hello workspace")
    _write(tmp_path, "sub/KPOINTS", "3 3 1\nG\n")
    found, text = snapshot_workspace(tmp_path)
    assert found is True
    assert "[工作区快照]" in text
    assert str(tmp_path) in text
    assert "INCAR" in text and "readme.txt" in text and "sub/KPOINTS" in text
    assert "SYSTEM = fe2o3" in text        # 正文进了预览
    assert "--- 文件预览: INCAR ---" in text


def test_priority_files_first(tmp_path):
    _write(tmp_path, "zzz.txt", "zz")
    _write(tmp_path, "INCAR", "SYSTEM = x")
    found, text = snapshot_workspace(tmp_path)
    assert found is True
    incar_idx = text.index("INCAR")
    zzz_idx = text.index("zzz.txt")
    assert incar_idx < zzz_idx              # INCAR 优先级更高


def test_missing_or_empty_workspace_reports_honestly(tmp_path):
    found, text = snapshot_workspace("")
    assert found is False and "未设置" in text
    found2, text2 = snapshot_workspace(str(tmp_path / "missing"))
    assert "不可访问" in text2 or "不是目录" in text2


def test_skips_hidden_and_dependency_dirs(tmp_path):
    _write(tmp_path, ".hidden.txt", "secret")
    _write(tmp_path, ".git/config", "repo")
    _write(tmp_path, "node_modules/pkg/index.js", "module")
    _write(tmp_path, "KPOINTS", "3 3 1\nG\n")
    found, text = snapshot_workspace(tmp_path)
    assert found is True
    assert "KPOINTS" in text
    assert ".hidden.txt" not in text
    assert ".git" not in text
    assert "node_modules" not in text


class _FakeHpc:
    """同签名假 SSHManager（list_dir_info/read_file），离线测超算快照。"""

    def __init__(self, dirs, files):
        self._dirs = dirs      # {绝对路径: [{name,is_dir,size}, ...]}
        self._files = files    # {绝对路径: bytes}

    def list_dir_info(self, remote):
        if remote not in self._dirs:
            raise RuntimeError(f"no such directory: {remote}")
        return self._dirs[remote]

    def read_file(self, remote, *, max_bytes=None):
        if remote not in self._files:
            raise RuntimeError(f"no such file: {remote}")
        data = self._files[remote]
        return data[:max_bytes] if max_bytes else data


def test_snapshot_hpc_lists_and_previews():
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={
            root: [
                {"name": "INCAR", "is_dir": False, "size": 20},
                {"name": "relax", "is_dir": True, "size": 0},
                {"name": ".secrets", "is_dir": True, "size": 0},
            ],
            f"{root}/relax": [
                {"name": "POSCAR", "is_dir": False, "size": 10},
                {"name": "slurm.log", "is_dir": False, "size": 3},
            ],
        },
        files={f"{root}/INCAR": "SYSTEM = fe2o3\nENCUT = 520\n".encode()},
    )
    found, text = snapshot_hpc_workspace(hpc, root)
    assert found is True
    assert "[超算工作区快照] /remote/work" in text
    assert "INCAR" in text
    assert "relax/POSCAR" in text                 # 子目录被递归列出
    assert ".secrets" not in text                 # 隐藏目录被跳过
    assert "SYSTEM = fe2o3" in text               # 关键文件进了预览
    assert "--- 超算文件预览: INCAR ---" in text


def test_snapshot_hpc_unavailable_reports_honestly():
    found, text = snapshot_hpc_workspace(None, "/remote/work")
    assert found is False and "未连接超算" in text
    found2, text2 = snapshot_hpc_workspace(_FakeHpc({}, {}), "")
    assert found2 is False and "未设置" in text2
    found3, text3 = snapshot_hpc_workspace(_FakeHpc({}, {}), "/remote/missing")
    assert found3 is True and "不可访问" in text3


def test_snapshot_hpc_filters_binary_and_empty_dir():
    root = "/remote/work"
    hpc = _FakeHpc(
        dirs={root: [
            {"name": "plot.png", "is_dir": False, "size": 99999},
        ]},
        files={},
    )
    found, text = snapshot_hpc_workspace(hpc, root)
    assert found is True
    assert "已过滤 1 个" in text                  # 二进制扩展名被过滤
    assert "plot.png" not in text
    assert "（目录为空或无可见文件）" in text      # 过滤后无可见条目则如实说明


def test_snapshot_bounded_not_infinite(tmp_path):
    for i in range(50):
        _write(tmp_path, f"f{i:02d}.txt", "x" * 500)
    found, text = snapshot_workspace(tmp_path, max_entries=10,
                                     max_preview_bytes=100,
                                     preview_total_cap=200, total_cap=500)
    assert found is True
    assert len(text) <= 500
    assert text.rstrip().endswith("（快照过大已截断）") or "快照" in text


def test_m40_skips_ms_engineering_noise(tmp_path):
    """M40：过滤 Materials Studio 工程/参考文档/Office 锁文件/三维模型噪音。"""
    _write(tmp_path, "POSCAR", "Fe2O3\n1.0\n1 0 0\n0 1 0\n0 0 1\nFe O\n1 3\nCartesian\n")
    _write(tmp_path, "INCAR", "SYSTEM = fe2o3\n")
    _write(tmp_path, "5_MnO2_ZPO_Files/Modules/SMXViewer3d_x.xml",
           "<EXTENSION><STATE/></EXTENSION>")
    _write(tmp_path, "5_MnO2_ZPO_Files/Documents/prepare/MnO2.xsd", "<xsd/>")
    _write(tmp_path, "参考/paper.pdf", "%PDF-1.4 fake")
    _write(tmp_path, "model.stp", "ISO-10303-21;")
    _write(tmp_path, "~$计算 1.pptx", "lock")
    found, text = snapshot_workspace(tmp_path)
    assert found is True
    assert "INCAR" in text and "POSCAR" in text
    assert "Modules" not in text and "prepare" not in text
    assert "参考" not in text and "paper" not in text
    assert ".stp" not in text and "~$" not in text


def test_m40_folds_large_non_priority_dir(tmp_path):
    """M40：非关键大目录折叠为一行概览，不逐条铺满。"""
    for i in range(20):
        _write(tmp_path, f"many/a{i:02d}.txt", "x" * 50)
    _write(tmp_path, "INCAR", "SYSTEM = fe2o3\n")
    found, text = snapshot_workspace(tmp_path)
    assert found is True
    assert "INCAR" in text
    assert "该目录共 20 个非关键文件" in text
    assert "many/a00.txt" not in text      # 折叠后不逐条列出
    assert "a00.txt" in text               # 只作示例
