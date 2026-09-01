"""M032/M33 本地/超算工作区「图形化浏览点选 + 新建文件夹」后端测试。

覆盖：
- browse_local：空 path 返回盘符/主目录起点；有效目录列出条目并按目录优先
  排序；隐藏/系统目录与进不去的目录被过滤；不存在返回 notice 信封（200 不抛）。
- browse_hpc：内存 FakeSSH 注入 SSHManager 同签名能力，空 path 返回 / 与
  主目录起点、过滤点开头隐藏目录与无关目录。
- mkdir_local / mkdir_hpc：合法名建目录成功且可被重新列出；非法名返回
  ok=False + 中文提示。
- 路由：/ai/v1/browse/local|hpc 与 /ai/v1/browse/local|hpc/mkdir 的
  启用 / 未配置超算 400 面。


- pick 路由：/ai/v1/browse/local/pick 由后端弹系统原生目录选择窗（子进程 Tk），
  覆盖成功 / 取消 / 无效路径 / 弹窗异常四类返回面。
"""

import pytest
from fastapi.testclient import TestClient

from ai_mode.browse import (
    browse_hpc,
    browse_local,
    local_roots,
    mkdir_hpc,
    mkdir_local,
)
from ai_mode.server import create_ai_mode_app
from ai_mode.ssh.errors import SSHSFTPError


class FakeSSH:
    """具备 SSHManager 同签名浏览/建目录能力的假超算层（离线测试）。"""

    def __init__(self):
        self.tree = {
            "/": {"a": "dir", "b.txt": "file", ".cache": "dir",
                  "lost+found": "dir"},
            "/a": {"c": "dir", "INCAR": "file"},
        }
        self.made = []

    def list_dir_info(self, path):
        key = path.rstrip("/") or "/"
        if key not in self.tree:
            raise SSHSFTPError("列目录失败: /oops")
        return [{"name": name, "is_dir": typ == "dir", "size": 0}
                for name, typ in sorted(self.tree[key].items())]

    def stat(self, path):
        key = path.rstrip("/") or "/"
        return {"size": 0, "mtime": 0} if key in self.tree else None

    def run(self, command):
        if command == "pwd":
            return 0, "/home/alice\n", ""
        return 0, "", ""

    def mkdir(self, remote):
        self.made.append(remote)
        key = remote.rstrip("/") or "/"
        self.tree.setdefault(key, {})


def test_local_roots_contain_home():
    roots = local_roots()
    assert roots
    assert all(r["is_dir"] for r in roots)


def test_browse_local_lists_entries(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "b").mkdir()
    data = browse_local(str(tmp_path))
    assert data["path"] == str(tmp_path)
    names = [e["name"] for e in data["entries"]]
    assert "sub" in names and "a.txt" in names and "b" in names
    assert names.index("sub") < names.index("a.txt")
    assert names.index("b") < names.index("a.txt")
    assert data["parent"] is not None


def test_browse_local_filters_hidden_and_unreadable(tmp_path, monkeypatch):
    import ai_mode.browse as browse_module

    (tmp_path / ".git").mkdir()
    (tmp_path / "$Recycle.Bin").mkdir()
    (tmp_path / "blocked").mkdir()
    (tmp_path / "visible").mkdir()

    def fake_readable(path):
        return "blocked" not in path

    monkeypatch.setattr(browse_module, "_dir_readable", fake_readable)
    names = [e["name"] for e in browse_local(str(tmp_path))["entries"]]
    assert "visible" in names
    assert ".git" not in names
    assert "$Recycle.Bin" not in names
    assert "blocked" not in names


def test_browse_local_missing_returns_notice(tmp_path):
    data = browse_local(str(tmp_path / "nope"))
    assert data["notice"]
    assert data["entries"] == []


def test_browse_local_root_returns_roots():
    data = browse_local("")
    assert data["path"] == ""
    assert "roots" in data and data["roots"]


def test_browse_hpc_empty_returns_roots():
    data = browse_hpc(FakeSSH(), "")
    names = [r["name"] for r in data["roots"]]
    assert "/" in names
    assert "/home/alice" in names


def test_browse_hpc_lists_entries():
    data = browse_hpc(FakeSSH(), "/a")
    assert data["path"] == "/a"
    names = [e["name"] for e in data["entries"]]
    assert "c" in names and "INCAR" in names


def test_browse_hpc_filters_hidden_and_junk():
    data = browse_hpc(FakeSSH(), "/")
    names = [e["name"] for e in data["entries"]]
    assert "a" in names and "b.txt" in names
    assert ".cache" not in names
    assert "lost+found" not in names


def test_browse_hpc_missing_returns_notice():
    data = browse_hpc(FakeSSH(), "/oops")
    assert data["notice"]
    assert data["entries"] == []


def test_mkdir_local_creates(tmp_path):
    result = mkdir_local(str(tmp_path), "newdir")
    assert result["ok"] is True
    assert (tmp_path / "newdir").is_dir()
    names = [e["name"] for e in browse_local(str(tmp_path))["entries"]]
    assert "newdir" in names


def test_mkdir_local_rejects_bad_names(tmp_path):
    for bad in ("", "a/b", "..", "a\\b", "x" * 121):
        result = mkdir_local(str(tmp_path), bad)
        assert result["ok"] is False
        assert result["notice"]


def test_mkdir_hpc_creates():
    ssh = FakeSSH()
    result = mkdir_hpc(ssh, "/a", "newdir")
    assert result["ok"] is True
    assert result["path"] == "/a/newdir"
    assert "/a/newdir" in ssh.made


def test_mkdir_hpc_rejects_bad_names():
    ssh = FakeSSH()
    result = mkdir_hpc(ssh, "/a", "../b")
    assert result["ok"] is False
    assert result["notice"]
    assert ssh.made == []


@pytest.fixture
def enabled_client(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    app = create_ai_mode_app()
    with TestClient(app) as client:
        yield client


def test_browse_local_route(enabled_client, tmp_path):
    r = enabled_client.get("/ai/v1/browse/local", params={"path": str(tmp_path)})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "local"
    assert body["path"] == str(tmp_path)
    assert isinstance(body["entries"], list)


def test_browse_local_route_roots(enabled_client):
    r = enabled_client.get("/ai/v1/browse/local")
    assert r.status_code == 200
    assert "roots" in r.json()


def test_browse_hpc_route_returns_notice_when_unconfigured(enabled_client):
    r = enabled_client.get("/ai/v1/browse/hpc", params={"path": "/"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_MODE_HPC_UNCONFIGURED"


def test_browse_hpc_route_with_fake_ssh(enabled_client, monkeypatch):
    import ai_mode.browse as browse_module

    class FakeManager(FakeSSH):
        def close(self):
            pass

    def fake_factory(cfg):
        return FakeManager()

    monkeypatch.setattr(browse_module, "create_hpc_ssh", fake_factory)
    r = enabled_client.get("/ai/v1/browse/hpc", params={"path": "/a"})
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["name"] == "INCAR" for e in entries)
    r2 = enabled_client.get("/ai/v1/browse/hpc")
    assert r2.status_code == 200
    roots = r2.json()["roots"]
    assert any(rr["name"] == "/home/alice" for rr in roots)


def test_mkdir_local_route(enabled_client, tmp_path):
    r = enabled_client.post("/ai/v1/browse/local/mkdir",
                            json={"path": str(tmp_path), "name": "dir1"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "local"
    assert body["ok"] is True
    assert (tmp_path / "dir1").is_dir()
    bad = enabled_client.post("/ai/v1/browse/local/mkdir",
                              json={"path": str(tmp_path), "name": "a/b"})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False


def test_mkdir_hpc_route_unconfigured(enabled_client):
    r = enabled_client.post("/ai/v1/browse/hpc/mkdir",
                            json={"path": "/", "name": "dir1"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_MODE_HPC_UNCONFIGURED"


def test_mkdir_hpc_route_with_fake_ssh(enabled_client, monkeypatch):
    import ai_mode.browse as browse_module

    class FakeManager(FakeSSH):
        def close(self):
            pass

    def fake_factory(cfg):
        return FakeManager()

    monkeypatch.setattr(browse_module, "create_hpc_ssh", fake_factory)
    r = enabled_client.post("/ai/v1/browse/hpc/mkdir",
                            json={"path": "/a", "name": "newdir"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "hpc"
    assert body["ok"] is True

def test_local_pick_program_compiles():
    import ai_mode.browse as browse_module
    compile(browse_module._PICK_PROGRAM, "<pick>", "exec")


def test_local_pick_route_ok(enabled_client, tmp_path, monkeypatch):
    import ai_mode.browse as browse_module
    target = tmp_path / "work"
    target.mkdir()
    monkeypatch.setattr(browse_module, "pick_local_directory",
                        lambda initial=None: str(target))
    r = enabled_client.post("/ai/v1/browse/local/pick", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["path"] == str(target)


def test_local_pick_route_passes_initial_and_cancel(enabled_client, monkeypatch):
    import ai_mode.browse as browse_module
    seen = {}

    def fake(initial=None):
        seen["initial"] = initial
        return ""

    monkeypatch.setattr(browse_module, "pick_local_directory", fake)
    r = enabled_client.post("/ai/v1/browse/local/pick",
                            json={"initial_dir": "D:\\calc"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["notice"] == "已取消选择"


def test_local_pick_route_invalid_path(enabled_client, tmp_path, monkeypatch):
    import ai_mode.browse as browse_module
    monkeypatch.setattr(browse_module, "pick_local_directory",
                        lambda initial=None: str(tmp_path / "nope"))
    r = enabled_client.post("/ai/v1/browse/local/pick", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["notice"]


def test_local_pick_route_generic_error(enabled_client, monkeypatch):
    import ai_mode.browse as browse_module

    def boom(initial=None):
        raise RuntimeError("no display")

    monkeypatch.setattr(browse_module, "pick_local_directory", boom)
    r = enabled_client.post("/ai/v1/browse/local/pick", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "RuntimeError" in body["notice"]


def test_local_pick_route_dialog_error(enabled_client, monkeypatch):
    import ai_mode.browse as browse_module

    def boom(initial=None):
        raise browse_module.BrowseDialogError("headless: no Tk")

    monkeypatch.setattr(browse_module, "pick_local_directory", boom)
    r = enabled_client.post("/ai/v1/browse/local/pick", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "headless" in body["notice"]

