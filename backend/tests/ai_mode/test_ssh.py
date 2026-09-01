"""M6 超算连接层 SSH 测试：内存 Fake 客户端/SFTP，不触碰真实网络。

验证范围：凭据存取、账号切换、连接生命周期（复用/关闭/回收）、
run 原语、test_connection、SFTP 传输原语、认证错误脱敏。
"""

import pytest

from ai_mode.ssh import (
    SSHManager,
    MemoryCredentialStore,
    KeyringCredentialStore,
    account_key,
)
from ai_mode.ssh.errors import (
    SSHUnavailableError,
    SSHAuthError,
    SSHExecuteError,
    SSHSFTPError,
)


class FakeStream:
    def __init__(self, data: str = "", exit_code: int = 0):
        self._data = data.encode("utf-8")
        self._pos = 0
        self.channel = _Channel(exit_code)

    def read(self, n: int = -1):
        if self._pos >= len(self._data):
            return b""
        if n is None or n < 0:
            rest = self._data[self._pos:]
            self._pos = len(self._data)
            return rest
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _Channel:
    def __init__(self, exit_code):
        self._exit = exit_code

    def recv_exit_status(self):
        return self._exit

class _SftpFile:
    def __init__(self, data: bytearray, mode: str):
        self._data = data
        self._mode = mode
        self._pos = 0

    def read(self, n: int = -1):
        if "r" not in self._mode:
            raise OSError("未以读模式打开")
        if self._pos >= len(self._data):
            return b""
        if n is None or n < 0:
            rest = bytes(self._data[self._pos:])
            self._pos = len(self._data)
            return rest
        chunk = bytes(self._data[self._pos:self._pos + n])
        self._pos += len(chunk)
        return chunk

    def write(self, data: bytes) -> int:
        if "w" not in self._mode and "a" not in self._mode:
            raise OSError("未以写模式打开")
        if self._mode == "wb":
            self._data[:] = b""
            self._pos = 0
        self._data[self._pos:self._pos] = data
        self._pos += len(data)
        return len(data)

    def flush(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSFTP:
    def __init__(self):
        self.files = {}
        self.dirs = set()

    def listdir(self, path):
        prefix = (path.rstrip("/") + "/") if path else ""
        names = set()
        for full_path in list(self.files) + list(self.dirs):
            if full_path.startswith(prefix):
                rest = full_path[len(prefix):]
                names.add(rest.split("/", 1)[0] if "/" in rest else rest)
        return sorted(names)

    def listdir_attr(self, path):
        result = []
        base = (path.rstrip("/") + "/") if path else ""
        for name in self.listdir(path):
            full = base + name
            is_dir = full in self.dirs or any(
                d.startswith(full + "/") for d in self.dirs)
            result.append(_FakeStat(
                0 if is_dir else len(self.files.get(full, b"")),
                mode=0o040755 if is_dir else 0o100644,
                filename=name))
        return result

    def stat(self, path):
        if path in self.files:
            return _FakeStat(len(self.files[path]), mode=0o100644)
        if path in self.dirs:
            return _FakeStat(0, mode=0o040755)
        raise FileNotFoundError(path)

    def open(self, path, mode="rb"):
        if "r" in mode and path not in self.files:
            raise FileNotFoundError(path)
        if path not in self.files:
            self.files[path] = bytearray()
        return _SftpFile(self.files[path], mode)

    def mkdir(self, path):
        if path in self.files:
            raise OSError("文件已存在")
        self.dirs.add(path)

    def close(self):
        pass


class _FakeStat:
    def __init__(self, size, mode=0o100644, filename=None):
        self.st_size = size
        self.st_mtime = 0
        self.st_mode = mode
        self.filename = filename


class FakeClient:
    def __init__(self):
        self._alive = True
        self._closed = False
        self._sftp = None
        self.command_results = {}
        self.exec_error = None
        self.connect_error = None
        self.connect_kwargs = None
        self._transport = _FakeTransport(self)

    def get_transport(self):
        return self._transport

    def connect(self, **kwargs):
        if self.connect_error is not None:
            raise self.connect_error
        self.connect_kwargs = kwargs
        self._alive = True

    def exec_command(self, command, **kw):
        if self.exec_error is not None:
            raise self.exec_error
        last = command.rstrip()
        base = last.split("&&")[-1].strip() if "&&" in last else last.strip()
        code, out, err = self.command_results.get(base, (0, "", ""))
        return (object(), FakeStream(out, code), FakeStream(err, code))

    def open_sftp(self):
        if self._sftp is None:
            self._sftp = FakeSFTP()
        return self._sftp

    def close(self):
        self._closed = True
        self._alive = False

    def _is_active(self):
        return self._alive and not self._closed


class _FakeTransport:
    def __init__(self, owner):
        self._owner = owner

    def set_keepalive(self, seconds):
        pass

    def is_active(self):
        return self._owner._is_active()


class FakeClientFactory:
    def __init__(self):
        self.created = []

    def __call__(self):
        client = FakeClient()
        self.created.append(client)
        return client


# -------------------- 凭据 --------------------


def test_memory_credentials_roundtrip():
    store = MemoryCredentialStore()
    assert store.get_password("h", "u") is None
    store.set_password("h", "u", "s3cret")
    assert store.get_password("h", "u") == "s3cret"
    store.set_password("h", "u", "new")
    assert store.get_password("h", "u") == "new"
    store.delete_password("h", "u")
    assert store.get_password("h", "u") is None
    store.delete_password("h", "u")  # 幂等


def test_account_key_format():
    assert account_key("login.hpc", "alice") == "ssh://alice@login.hpc"


def test_keyring_store_interface():
    store = KeyringCredentialStore()
    assert callable(store.get_password)
    assert callable(store.set_password)
    assert callable(store.delete_password)


# -------------------- 账号切换 --------------------


def test_switch_requires_valid_account():
    mgr = SSHManager(client_factory=FakeClient)
    with pytest.raises(ValueError):
        mgr.switch(host="", username="u")
    with pytest.raises(ValueError):
        mgr.switch(host="h", username="")


def test_switch_stores_password_and_active():
    mgr = SSHManager(client_factory=FakeClient)
    mgr.switch(host="hpc", username="alice", password="pw1")
    assert mgr.active == {"host": "hpc", "username": "alice", "port": 22}
    assert mgr.credentials.get_password("hpc", "alice") == "pw1"


def test_switch_closes_previous_connection():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw1")
    mgr.connect()
    assert mgr.connected
    mgr.switch(host="hpc2", username="bob", password="pw2")
    assert mgr.active == {"host": "hpc2", "username": "bob", "port": 22}
    assert not mgr.connected
    assert factory.created[0]._closed


def test_forget_clears_connection():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    mgr.forget()
    assert not mgr.connected
    with pytest.raises(SSHUnavailableError):
        mgr.connect()


# -------------------- 连接 --------------------


def test_connect_without_account_raises():
    mgr = SSHManager(client_factory=FakeClient)
    with pytest.raises(SSHUnavailableError):
        mgr.connect()


def test_connect_pulls_password_from_credentials():
    store = MemoryCredentialStore()
    store.set_password("hpc", "alice", "s3cret!")
    factory = FakeClientFactory()
    mgr = SSHManager(credentials=store, client_factory=factory)
    mgr.switch(host="hpc", username="alice")  # 不传密码 -> 从凭据读取
    mgr.connect()
    kwargs = factory.created[0].connect_kwargs
    assert kwargs["password"] == "s3cret!"
    assert kwargs["hostname"] == "hpc"
    assert kwargs["username"] == "alice"


def test_auth_error_redacted():
    store = MemoryCredentialStore()
    store.set_password("hpc", "alice", "S1kr3t!!")

    class ConnectingClient(FakeClient):
        def connect(self, **kwargs):
            raise AuthSimFailure("auth failed")

    class AuthSimFailure(Exception):
        pass

    def _factory():
        return ConnectingClient()

    # 把异常类名改成辨识度高的，便于 _guess_error_type 识别
    AuthSimFailure.__name__ = "AuthenticationException"
    factory_cls = _AuthBoom(AuthSimFailure)
    mgr = SSHManager(credentials=store, client_factory=factory_cls)
    mgr.switch(host="hpc", username="alice")
    with pytest.raises(SSHAuthError) as exc:
        mgr.connect()
    assert "S1kr3t" not in str(exc.value)
    assert not mgr.connected


class _AuthBoom:
    def __init__(self, exc_cls):
        self._exc = exc_cls

    def __call__(self):
        client = FakeClient()

        def boom(**kwargs):
            raise self._exc("auth failure")

        client.connect = boom
        return client


def test_connect_reuses_connection():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    c1 = mgr.connect()
    c2 = mgr.connect()
    assert c1 is c2
    assert len(factory.created) == 1


def test_close_releases_connection():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    assert mgr.connected
    mgr.close()
    assert not mgr.connected
    mgr.close()  # 幂等


# -------------------- 执行原语 --------------------


def test_run_returns_code_out_err():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].command_results = {"pwd": (0, "/home/alice/calc\n", "")}
    code, out, err = mgr.run("pwd")
    assert code == 0
    assert out.strip() == "/home/alice/calc"
    assert err == ""


def test_run_with_cwd_prefixes_command():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].command_results = {"cat INCAR": (0, "ENCUT=520\n", "")}
    code, out, _ = mgr.run("cat INCAR", cwd="/calc")
    assert code == 0
    assert "ENCUT" in out


def test_run_nonzero_exit():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].command_results = {"squeue": (1, "", "error: no jobs")}
    code, _, err = mgr.run("squeue")
    assert code == 1
    assert "no jobs" in err


def test_run_output_truncated_to_limit():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory, max_output_bytes=128)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].command_results = {"x": (0, "y" * 10000, "")}
    _, out, _ = mgr.run("x")
    assert len(out) <= 128 + 1


def test_exec_command_error_reports():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].exec_error = RuntimeError("boom")
    with pytest.raises(SSHExecuteError):
        mgr.run("pwd")


def test_test_connection_ok():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    mgr.connect()
    factory.created[-1].command_results = {"whoami": (0, "alice\n", "")}
    ok, msg = mgr.test_connection()
    assert ok is True
    assert "alice" in msg


# -------------------- SFTP --------------------


def _sftp_mgr():
    factory = FakeClientFactory()
    mgr = SSHManager(client_factory=factory)
    mgr.switch(host="hpc", username="alice", password="pw")
    return mgr, factory


def test_write_read_roundtrip():
    mgr, _ = _sftp_mgr()
    n = mgr.write_file("/calc/INCAR", b"ENCUT=520\n")
    assert n == len(b"ENCUT=520\n")
    assert mgr.read_file("/calc/INCAR") == b"ENCUT=520\n"


def test_list_and_stat():
    mgr, _ = _sftp_mgr()
    mgr.write_file("/calc/INCAR", b"x")
    assert "INCAR" in mgr.list_dir("/calc")
    st = mgr.stat("/calc/INCAR")
    assert st["size"] == 1
    assert mgr.stat("/calc/MISSING") is None


def test_mkdir_idempotent():
    mgr, _ = _sftp_mgr()
    mgr.mkdir("/calc/sub")
    mgr.mkdir("/calc/sub")  # 幂等
    assert "sub" in mgr.list_dir("/calc")


def test_read_missing_file_raises():
    mgr, _ = _sftp_mgr()
    with pytest.raises(SSHSFTPError):
        mgr.read_file("/calc/absent")
def test_list_dir_info_marks_dirs():
    mgr, _ = _sftp_mgr()
    mgr.write_file("/calc/INCAR", b"x")
    mgr.mkdir("/calc/sub")
    infos = {e["name"]: e for e in mgr.list_dir_info("/calc")}
    assert infos["INCAR"]["is_dir"] is False
    assert infos["sub"]["is_dir"] is True
    assert infos["INCAR"]["size"] == 1

# -------------------- 默认客户端工厂 --------------------


def test_default_client_factory_host_key_policy(monkeypatch):
    """默认工厂应为首次连接自动记录主机密钥（TOFU），避免因 known_hosts
        缺失把可建立的连接误报为失败。"""
    pytest.importorskip("paramiko")
    import paramiko as _pk
    from ai_mode.ssh.connection import _default_client_factory

    captured = {}

    def fake_set_policy(self, policy):
        captured["policy"] = policy

    monkeypatch.setattr(_pk.SSHClient, "set_missing_host_key_policy", fake_set_policy)
    client = _default_client_factory()
    assert captured["policy"].__class__.__name__ == "AutoAddPolicy"