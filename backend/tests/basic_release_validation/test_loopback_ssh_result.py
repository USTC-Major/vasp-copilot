"""Real loopback SSH/known_hosts path to the production fixed result reader."""

from __future__ import annotations

import hashlib
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import paramiko
import pytest

from backend.toolbox.ssh.connection import SSHManager
from backend.toolbox.ssh.errors import SSHHostKeyMismatchError, SSHHostKeyUnknownError
from backend.toolbox.ssh.remote_files import RemoteFiles, helper_command


pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="requires the actual POSIX fixed helper over loopback SSH"
)


class _FixedHelperServer(paramiko.ServerInterface):
    def __init__(self, client_key: paramiko.PKey):
        self.client_key = client_key
        self.auth_count = 0
        self.exec_ready = threading.Event()

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_publickey(self, username, key):
        self.auth_count += 1
        if username == "local-fixture" and key.asbytes() == self.client_key.asbytes():
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        if command != helper_command().encode("utf-8"):
            return False
        self.exec_ready.set()
        return True


class _Loopback:
    def __init__(self, tmp_path: Path):
        self.tmp_path = tmp_path
        self.host_key = paramiko.RSAKey.generate(2048)
        self.client_key = paramiko.RSAKey.generate(2048)
        self.identity = tmp_path / "fixture_identity"
        self.client_key.write_private_key_file(str(self.identity))
        self.identity.chmod(0o600)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.servers: list[_FixedHelperServer] = []
        self.transports: list[paramiko.Transport] = []
        self.processes: list[subprocess.Popen] = []
        self.errors: list[str] = []
        self.thread = threading.Thread(target=self._serve, name="fixture-ssh", daemon=True)
        self.thread.start()

    @property
    def auth_count(self):
        with self.lock:
            return sum(server.auth_count for server in self.servers)

    def known_hosts(self, path: Path, *, key: paramiko.PKey | None):
        line = (f"[127.0.0.1]:{self.port} {key.get_name()} {key.get_base64()}\n"
                if key is not None else "")
        path.write_text(line, encoding="ascii")
        path.chmod(0o600)
        return str(path)

    def manager(self, known_hosts_path: str):
        manager = SSHManager(known_hosts_path=known_hosts_path,
                             identity_file=str(self.identity), connect_timeout=5)
        assert manager.client_factory is None
        manager.switch(host="127.0.0.1", port=self.port, username="local-fixture")
        target = {"scheduler": "fixture", "host": "127.0.0.1", "port": self.port,
                  "username": "local-fixture", "identity_file": str(self.identity),
                  "known_hosts_path": known_hosts_path}
        return manager, RemoteFiles(manager, scheduler_target=target)

    def _serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(connection)
            server = _FixedHelperServer(self.client_key)
            with self.lock:
                self.transports.append(transport)
                self.servers.append(server)
            try:
                transport.add_server_key(self.host_key)
                transport.start_server(server=server)
                channel = None
                deadline = time.monotonic() + 5
                while transport.is_active() and not self.stop.is_set() and time.monotonic() < deadline:
                    channel = transport.accept(0.2)
                    if channel is not None:
                        break
                if channel is not None:
                    channel.settimeout(5)
                    if server.exec_ready.wait(5):
                        self._run_helper(channel)
                    channel.close()
            except (EOFError, OSError, paramiko.SSHException):
                # A rejected client closes before authentication; this is expected.
                pass
            except Exception as exc:
                self.errors.append(type(exc).__name__)
            finally:
                transport.close()

    def _run_helper(self, channel):
        source = Path(__file__).resolve().parents[2] / "toolbox" / "ssh" / "file_helper.py"
        process = subprocess.Popen(
            [sys.executable, "-I", "-u", "-c", source.read_text(encoding="utf-8")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.processes.append(process)

        def feed_input():
            try:
                while not self.stop.is_set():
                    data = channel.recv(65536)
                    if not data:
                        break
                    process.stdin.write(data)
                    process.stdin.flush()
            except (EOFError, OSError):
                pass
            finally:
                process.stdin.close()

        feeder = threading.Thread(target=feed_input, name="fixture-ssh-stdin", daemon=True)
        feeder.start()
        try:
            # Drain stdout through EOF; a child may exit before its final frame
            # has been consumed by this bridge.
            while not self.stop.is_set():
                if not select.select([process.stdout], [], [], 0.1)[0]:
                    continue
                data = os.read(process.stdout.fileno(), 65536)
                if not data:
                    break
                channel.sendall(data)
            if process.poll() is None:
                process.wait(timeout=3)
            if not channel.closed:
                channel.send_exit_status(process.returncode)
        finally:
            channel.close()
            feeder.join(timeout=3)
            if feeder.is_alive():
                self.errors.append("helper input feeder did not exit")
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            process.stdout.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        with self.lock:
            for transport in self.transports:
                transport.close()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()
        assert all(process.poll() is not None for process in self.processes)
        assert not self.errors, self.errors


@pytest.fixture
def loopback(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)
    monkeypatch.delenv("SSH_AGENT_PID", raising=False)
    service = _Loopback(tmp_path)
    try:
        yield service
    finally:
        service.close()


def test_loopback_known_hosts_reads_exact_result(loopback, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    payload = b"synthetic-OUTCAR\x00\xff" * 4096
    (results / "OUTCAR").write_bytes(payload)
    trust = loopback.known_hosts(tmp_path / "known_hosts", key=loopback.host_key)
    manager, files = loopback.manager(trust)
    try:
        assert files.endpoint()["host_key"]["verification"] == "known_hosts"
        content, sha256 = files.read_result(str(results), "OUTCAR")
        assert content == payload
        assert sha256 == hashlib.sha256(payload).hexdigest()
        assert loopback.auth_count == 1
    finally:
        manager.close()


def test_loopback_bad_or_unknown_host_key_rejected_before_auth(loopback, tmp_path):
    for mode in ("mismatch", "unknown"):
        wrong_key = paramiko.RSAKey.generate(2048) if mode == "mismatch" else None
        trust = loopback.known_hosts(tmp_path / f"known_hosts_{mode}", key=wrong_key)
        manager, files = loopback.manager(trust)
        try:
            expected = SSHHostKeyMismatchError if mode == "mismatch" else SSHHostKeyUnknownError
            with pytest.raises(expected):
                files.read_result(str(tmp_path), "OUTCAR")
            assert loopback.auth_count == 0
        finally:
            manager.close()
