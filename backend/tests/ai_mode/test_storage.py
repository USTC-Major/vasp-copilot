"""本地化存储骨架测试。"""

from ai_mode.config import load_settings
from ai_mode.storage import ensure_layout


def test_ensure_layout_creates_dirs_and_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.delenv("ENABLE_AI_MODE", raising=False)
    dirs = ensure_layout()
    assert (tmp_path / "sessions").is_dir()
    assert (tmp_path / "skills").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "config.json").is_file()
    # data_dir == home
    cfg = load_settings()
    assert cfg.data_dir == tmp_path.resolve()


def test_ensure_layout_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    first = ensure_layout()
    second = ensure_layout()
    assert first == second
    # 不重复改写已存在的配置文件
    mtime = (tmp_path / "config.json").stat().st_mtime
    ensure_layout()
    assert (tmp_path / "config.json").stat().st_mtime == mtime