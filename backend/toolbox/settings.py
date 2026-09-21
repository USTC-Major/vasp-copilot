"""Deterministic connection probes owned by Toolbox."""
from .config import ExecutionConfig

def probe_mp(cfg: ExecutionConfig, *, timeout: float = 10.0) -> dict:
    """最小真实 MP 校验：GET /materials/summary/?_limit=1，看 key 是否有效。"""
    if not cfg.mp_api_key:
        return {'ok': False, 'provider': 'mp', 'message': '未配置 Materials Project API key'}
    import httpx
    url = "https://api.materialsproject.org/materials/summary/"
    headers = {"X-API-KEY": cfg.mp_api_key, "Accept": "application/json"}
    try:
        resp = httpx.get(url, headers=headers, params={"_limit": 1},
                         timeout=timeout, follow_redirects=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "mp",
                "message": f"MP 接口请求失败（网络不可达或被拦截）：{type(exc).__name__}"}
    if resp.status_code in (401, 403):
        return {"ok": False, "provider": "mp",
                "message": "MP API key 被拒绝（401/403），请检查后重试"}
    if resp.status_code >= 400:
        return {"ok": False, "provider": "mp",
                "message": f"MP API 返回 HTTP {resp.status_code}"}
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {}
    docs = data.get("data") if isinstance(data, dict) else None
    n = len(docs) if isinstance(docs, list) else 0
    return {"ok": True, "provider": "mp",
            "message": f"MP API 连通成功（返回 {n} 条）"}



def probe_ssh(cfg: ExecutionConfig) -> dict:
    if not cfg.ssh_host:
        return {"ok": False, "provider": "ssh", "message": "未配置 SSH 主机"}
    if not cfg.ssh_username:
        return {"ok": False, "provider": "ssh", "message": "未配置 SSH 用户名"}
    from .ssh.connection import SSHManager
    from .ssh.credentials import KeyringCredentialStore
    try:
        manager = SSHManager(credentials=KeyringCredentialStore(),
                             connect_timeout=10,
                             known_hosts_path=cfg.ssh_known_hosts_path or None,
                             identity_file=cfg.ssh_identity_file or None)
        try:
            ok, msg = manager.test_connection(host=cfg.ssh_host,
                                              username=cfg.ssh_username,
                                              port=cfg.ssh_port or 22)
        finally:
            try:
                manager.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "ssh",
                "message": f"SSH 测试出错：{type(exc).__name__}"}
    return {"ok": bool(ok), "provider": "ssh", "message": msg}
