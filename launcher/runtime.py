"""Launcher-only local hosting; no scientific logic, credentials, or installation."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import importlib.util
import json
import os
import socket
import errno
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

HEALTH_PATH = "/__launcher__/health"
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade"}

class PortInUse(Exception):
    """Only a failed local socket bind can enable bounded launcher retry."""

def bind_owned(sock, address):
    try:
        sock.bind(address)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048:
            raise PortInUse() from None
        raise


def fingerprint(root: Path) -> str:
    files = ["backend/app/main.py", "backend/ai_mode/server.py", "frontend/dist/index.html"]
    return ".".join(hashlib.sha256((root / p).read_bytes()).hexdigest()[:12] for p in files)


def identity(args) -> dict:
    return {"kind": args.kind, "pid": os.getpid(), "token": args.token,
            "root": str(args.root.resolve()), "fingerprint": fingerprint(args.root)}


def make_handler(args):
    dist = (args.root / "frontend/dist").resolve()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass  # Requests may contain sensitive queries; never log them.

        def reply(self, status, payload, content_type="application/json"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def handle_request(self):
            # Prevent DNS rebinding / foreign Host access; only serve this loopback origin.
            if self.headers.get("Host", "") not in {
                f"127.0.0.1:{args.port}", f"localhost:{args.port}"}:
                self.reply(403, {"error": {"code": "LOCALHOST_ONLY"}})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"}:
                self.reply(403, {"error": {"code": "LOCALHOST_ONLY"}})
                return
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc:
                self.reply(400, {})
                return
            path = parsed.path
            if path == HEALTH_PATH:
                if self.command != "GET" or self.headers.get("X-Launcher-Token") != args.token:
                    self.reply(404, {})
                    return
                self.reply(200, identity(args))
                return
            if path == "/api/v1" or path.startswith("/api/v1/"):
                self.proxy(args.toolbox_port)
                return
            if path == "/ai/v1" or path.startswith("/ai/v1/"):
                if not args.enable_ai:
                    self.reply(503, {"mode": "ai", "error": {"code": "AI_SERVICE_DISABLED",
                               "message": "启动器未启动智能模式服务", "retryable": False}})
                else:
                    self.proxy(args.ai_port)
                return
            if self.command not in {"GET", "HEAD"}:
                self.reply(405, {})
                return
            relative = unquote(path).lstrip("/")
            candidate = (dist / relative).resolve()
            try:
                candidate.relative_to(dist)
            except ValueError:
                self.reply(403, {})
                return
            if candidate.is_dir():
                candidate = (candidate / "index.html").resolve()
                try:
                    candidate.relative_to(dist)
                except ValueError:
                    self.reply(403, {})
                    return
            if not candidate.is_file():
                # SPA paths work, missing assets remain a real 404.
                if Path(relative).suffix or relative.startswith("assets/"):
                    self.reply(404, {})
                    return
                candidate = dist / "index.html"
            # Apply the containment check to the final SPA fallback as well.
            candidate = candidate.resolve()
            try:
                candidate.relative_to(dist)
            except ValueError:
                self.reply(403, {})
                return
            import mimetypes
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.reply(200, candidate.read_bytes(), content_type)

        def proxy(self, port):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
            try:
                # Browser requests have known lengths. Reject ambiguous/chunked request bodies.
                if self.headers.get("Transfer-Encoding"):
                    self.reply(400, {"error": {"code": "UNSUPPORTED_TRANSFER_ENCODING"}})
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 256 * 1024 * 1024:
                    self.reply(413, {})
                    return
                body = self.rfile.read(size) if size else None
                blocked = HOP_HEADERS | {x.strip().lower() for x in self.headers.get("Connection", "").split(",")}
                headers = {k: v for k, v in self.headers.items() if k.lower() not in blocked | {"host", "x-launcher-token"}}
                headers["Host"] = f"127.0.0.1:{port}"
                connection.request(self.command, self.path, body=body, headers=headers)
                response = connection.getresponse()
                self.send_response(response.status)
                response_blocked = HOP_HEADERS | {x.strip().lower() for x in (response.getheader("Connection") or "").split(",")}
                for key, value in response.getheaders():
                    if key.lower() not in response_blocked:
                        self.send_header(key, value)
                # Close-delimited streaming preserves SSE and downloads without buffering.
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                if self.command != "HEAD":
                    while chunk := response.read1(64 * 1024):
                        self.wfile.write(chunk)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (OSError, ValueError, http.client.HTTPException):
                # Never include URLs, payloads, keys or exception text in errors.
                if 'response' not in locals():
                    self.reply(502, {"error": {"code": "LOCAL_SERVICE_UNAVAILABLE",
                               "message": "本地服务不可达，请在启动器检查状态", "retryable": True}})
                self.close_connection = True
            finally:
                connection.close()

        do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = handle_request

    return Handler


def watch_stop(path: Path, stop, done: threading.Event):
    while not done.wait(0.15):
        if path.exists():
            stop()
            return


def dependency_check(args):
    modules = ["fastapi", "uvicorn", "multipart", "jinja2", "pydantic", "pydantic_settings",
               "httpx", "yaml", "numpy", "pymatgen", "openai", "paramiko", "keyring", "dotenv"]
    from importlib import import_module
    missing = []
    for name in modules:
        try:
            import_module(name)
        except Exception:
            missing.append(name)
    result = {"ok": sys.version_info >= (3, 10) and not missing,
              "python_supported": sys.version_info >= (3, 10), "missing": missing}
    args.result_file.write_text(json.dumps(result), encoding="utf-8")


def run(args):
    args.root = args.root.resolve()
    os.chdir(args.root / "backend")
    sys.path[:0] = [str(args.root), str(args.root / "backend")]
    if args.isolated:
        # Explicit test profile: no project .env, inherited credentials or shared keyring.
        keep = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC",
                "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PYTHONPYCACHEPREFIX"}
        for key in list(os.environ):
            if key.upper() not in keep:
                del os.environ[key]
        os.environ.update(HOME=args.ai_home, USERPROFILE=args.ai_home,
                          VASP_AI_HOME=args.ai_home, DATA_DIR=args.data_dir,
                          ENABLE_LLM="false", ENABLE_MATERIALS_PROJECT="false",
                          VASP_REVIEWER_ENABLED="false")
        import keyring
        from keyring.backend import KeyringBackend
        class TestKeyring(KeyringBackend):
            priority = 1
            def get_password(self, service, username):
                return None
            def set_password(self, service, username, password):
                raise RuntimeError("Shared keyring is disabled in the isolated test profile")
            def delete_password(self, service, username):
                raise RuntimeError("Shared keyring is disabled in the isolated test profile")
        keyring.set_keyring(TestKeyring())
    done = threading.Event()
    if args.kind == "web":
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args), bind_and_activate=False)
        server.allow_reuse_address = False
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            bind_owned(server.socket, ("127.0.0.1", args.port))
            server.server_name = "127.0.0.1"
            server.server_port = args.port
            server.server_activate()
        except BaseException:
            server.server_close()
            raise
        server.daemon_threads = True
        threading.Thread(target=watch_stop, args=(args.stop_file, server.shutdown, done), daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.15)
        finally:
            done.set()
            server.server_close()
        return
    from dotenv import dotenv_values
    # Read existing backend .env only, never duplicate/persist its contents.
    for key, value in ({} if args.isolated else dotenv_values(args.root / "backend/.env")).items():
        if value is not None:
            os.environ.setdefault(key, value)
    if args.ai_home:
        os.environ["VASP_AI_HOME"] = args.ai_home
    if args.data_dir:
        os.environ["DATA_DIR"] = args.data_dir
    os.environ["ENABLE_AI_MODE"] = "true" if args.kind == "ai" else "false"
    # Only the external reviewer is outside the launcher stack. Existing model configuration stays intact.
    os.environ["VASP_REVIEWER_ENABLED"] = "false"
    os.environ["TOOLBOX_URL"] = f"http://127.0.0.1:{args.toolbox_port}"
    # Bind and retain this exact socket through startup; bind errors stay machine-readable.
    bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            bound.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        bind_owned(bound, ("127.0.0.1", args.port))
        bound.listen(128)
    except BaseException:
        bound.close()
        raise
    import uvicorn
    from importlib import import_module
    app = import_module("app.main" if args.kind == "toolbox" else "ai_mode.server").app

    @app.get(HEALTH_PATH, include_in_schema=False)
    def launcher_health(request: __import__("fastapi").Request):
        from fastapi.responses import JSONResponse
        if request.headers.get("X-Launcher-Token") != args.token:
            return JSONResponse({}, status_code=404)
        return identity(args)

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="critical",
                            access_log=False, timeout_graceful_shutdown=5)
    server = uvicorn.Server(config)
    threading.Thread(target=watch_stop, args=(args.stop_file, lambda: setattr(server, "should_exit", True), done), daemon=True).start()
    try:
        server.run(sockets=[bound])
    finally:
        done.set()
        bound.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--kind", choices=["web", "toolbox", "ai", "check"], required=True)
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--toolbox-port", type=int, default=8000)
    parser.add_argument("--ai-port", type=int, default=8500)
    parser.add_argument("--enable-ai", action="store_true")
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--token", default="")
    parser.add_argument("--ai-home", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args()
    if args.isolated and (not args.ai_home or not args.data_dir or not Path(args.ai_home).is_absolute() or not Path(args.data_dir).is_absolute()):
        parser.error("isolated profile requires absolute home and data directories")
    try:
        dependency_check(args) if args.kind == "check" else run(args)
    except Exception as exc:
        # Safe machine-readable failure category only; no traceback/configuration.
        if args.result_file:
            conflict = isinstance(exc, PortInUse)
            args.result_file.write_text(json.dumps({"ok": False, "error_type": type(exc).__name__,
                                                   "code": "PORT_IN_USE" if conflict else "START_FAILED"}), encoding="utf-8")
        sys.exit(1)


if __name__ == "__main__":
    main()
