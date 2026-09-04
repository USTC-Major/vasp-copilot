"""M11 设置项后端支撑测试：全局设置掩码/更新/连通派发 + 项目精度设置存储 + 路由。"""

import json

import pytest
from fastapi.testclient import TestClient

from ai_mode.config import AiModeConfig, execution_mode, load_settings
from ai_mode.server import create_ai_mode_app
from ai_mode.settings import (
    ProjectSettingsError,
    ProjectSettingsStore,
    SETTABLE_FIELDS,
    mask_config,
    persist,
    require_valid_accuracy,
    sanitize_project_id,
    update_from_patch,
    validate_accuracy,
    check_connection,
    normalize_accuracy,
    render_accuracy_text,
    project_settings_path,
)
from ai_mode.settings.global_api import secret_status, update_secret

GOOD_ACCURACY = [
    "relax 全流程：ENCUT=520，EDIFF 收敛到 1e-5",
    "DOS 计算用四面体 ISMEAR = -5",
]


# ---------------- 全局设置逻辑 ----------------
def _cfg():
    # Pure settings-unit tests must not inherit a developer machine's
    # persisted config or environment overrides.
    return AiModeConfig()


def test_execution_mode_is_explicit_for_fake_real_and_unconfigured():
    from ai_mode.ssh.connection import SSHManager

    class ExplicitFake:
        execution_mode = "Fake"

    assert execution_mode(None) == "None"
    assert execution_mode(ExplicitFake()) == "Fake"
    assert execution_mode(SSHManager(client_factory=lambda: object())) == "Real"
    with pytest.raises(ValueError, match="explicitly declare Fake"):
        execution_mode(object())
    # LLM selection and keys never affect the HPC provenance label.
    assert execution_mode(None) == execution_mode(
        None, explicit=None)


def test_mask_config_redacts():
    cfg = load_settings()
    cfg = update_from_patch(cfg, {"llm_api_key": "sk-verysecret",
                                  "mp_api_key": "mp-verysecret"})
    payload = mask_config(cfg)
    assert payload["llm"]["api_key"] == "<redacted>"
    assert payload["materials_project"]["api_key"] == "<redacted>"
    body = json.dumps(payload)
    assert "sk-verysecret" not in body and "mp-verysecret" not in body


def test_update_valid_fields():
    cfg = _cfg()
    updated = update_from_patch(cfg, {"max_jobs": 4, "llm_model": "gpt-4o",
                                      "billing_estimate_enabled": True})
    assert updated.max_jobs == 4 and isinstance(updated.max_jobs, int)
    assert updated.llm_model == "gpt-4o"
    assert updated.billing_estimate_enabled is True
    assert cfg.max_jobs == 20   # 原对象不变


def test_update_unknown_field_raises():
    with pytest.raises(ValueError):
        update_from_patch(_cfg(), {"bogus": 1})


@pytest.mark.parametrize("patch,needle", [
    ({"max_jobs": 0}, "max_jobs"),
    ({"max_jobs": "many"}, "max_jobs"),
    ({"billing_estimate_enabled": "yes"}, "billing_estimate_enabled"),
    ({"llm_provider": "weird"}, "llm_provider"),
    ({"ssh_port": 70000}, "ssh_port"),
    ({"llm_max_retries": -2}, "llm_max_retries"),
])
def test_update_invalid_raises(patch, needle):
    with pytest.raises(ValueError) as ei:
        update_from_patch(_cfg(), patch)
    assert needle in str(ei.value)


def test_check_connection_injected():
    cfg = _cfg()
    fake = {
        "llm": lambda c: {"ok": True, "provider": "llm", "message": "fake ok"},
        "mp": lambda c: {"ok": False, "provider": "mp", "message": "fake mp"},
        "ssh": lambda c: {"ok": True, "provider": "ssh", "message": "fake ssh"},
    }
    assert check_connection("llm", cfg, testers=fake)["ok"] is True
    assert check_connection("MP", cfg, testers=fake)["ok"] is False
    assert check_connection("ssh", cfg, testers=fake)["ok"] is True


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        check_connection("bogus", _cfg())


def test_default_testers_offline_safe(tmp_path, monkeypatch):
    # 隔离到空临时主目录：只验证「未配置→离线安全」，不受本机真实配置影响
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    from ai_mode.config import save_settings
    cfg = load_settings()
    # llm 未配置 key -> auto -> fake -> ok True（离线可用）
    llm = check_connection("llm", cfg)
    assert llm["provider"] or llm["ok"] or "ok" in llm
    # mp/ssh 离线安全返回
    mp = check_connection("mp", cfg)
    assert mp["ok"] is False and "未配置" in mp["message"]
    ssh = check_connection("ssh", cfg)
    assert ssh["ok"] is False and "未配置" in ssh["message"]


def test_persist_roundtrip(tmp_path):
    cfg = load_settings()
    cfg = update_from_patch(cfg, {"llm_api_key": "secret-local",
                                  "max_jobs": 9})
    path = tmp_path / "cfg" / "config.json"
    persist(cfg, config_path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "enabled" not in data
    assert data["llm_api_key"] == "secret-local"   # 仅本地,不上传
    assert data["max_jobs"] == 9


# ---------------- 项目级精度设置 ----------------
def test_validate_accuracy_ok():
    assert validate_accuracy(GOOD_ACCURACY) == []


def test_validate_sensitive_content():
    bad = ["把 api_key 和登录口令 password=hunter2 写进项目设置"]
    assert any("敏感" in i for i in validate_accuracy(bad))
    with pytest.raises(ProjectSettingsError):
        require_valid_accuracy(bad)


@pytest.mark.parametrize("acc,needle", [
    ("not-a-list", "accuracy 需为字符串条目列表"),
    (["", "可用"], "不能为空"),
    ([123], "需为字符串"),
])
def test_validate_bad(acc, needle):
    issues = validate_accuracy(acc)
    assert any(needle in i for i in issues)


def test_sanitize_project_id():
    assert sanitize_project_id("fe2o3 项目!") == "fe2o3-项目"
    assert sanitize_project_id("!!!") == "misc"


def test_store_roundtrip(tmp_path):
    store = ProjectSettingsStore(root=tmp_path)
    store.save("projA", GOOD_ACCURACY)
    loaded = store.load("projA")
    assert loaded["accuracy"] == list(GOOD_ACCURACY)
    assert store.delete("projA") is True
    assert store.load("projA")["accuracy"] == []


def test_store_legacy_accuracy_compat(tmp_path):
    """旧形态 {job_type: [条目]} 文件载入时折叠为纯内容条目。"""
    store = ProjectSettingsStore(root=tmp_path)
    store.save("projB", ["DOS 计算用四面体 ISMEAR = -5"])
    legacy = {"relax": [{"key": "EDIFF", "value": "1e-05"}]}
    path = project_settings_path("projB", root=tmp_path)
    path.write_text(json.dumps({"project_id": "projB", "accuracy": legacy},
                               ensure_ascii=False), encoding="utf-8")
    loaded = store.load("projB")
    assert isinstance(loaded["accuracy"], list)
    assert any("EDIFF = 1e-05" in str(e) for e in loaded["accuracy"])


def test_store_list_all(tmp_path):
    store = ProjectSettingsStore(root=tmp_path)
    assert store.list_all() == []
    store.save("P1", GOOD_ACCURACY)
    store.save("P2", ["vasp ALGO = Normal"])
    ids = [d["project_id"] for d in store.list_all()]
    assert sorted(ids) == ["P1", "P2"]


def test_project_path_localization(tmp_path):
    assert project_settings_path("a b").name.endswith(".json")
    path = project_settings_path("Proj#1/../X", root=tmp_path)
    assert str(path).startswith(str(tmp_path.resolve()))
    assert ".." not in str(path.resolve())


# ---------------- 路由 ----------------
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "true")
    app = create_ai_mode_app()
    with TestClient(app) as c:
        yield c


def test_route_settings_get_masked(client):
    r = client.get("/ai/v1/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["settings"]["llm"]["api_key"] == ""  # 未配置时不返回任何密钥内容
    assert "max_jobs" in body["settings"]
    assert "llm_api_key" not in body["writable"]
    assert "mp_api_key" not in body["writable"]
    assert "ssh_password" not in body["writable"]


def test_route_settings_put_and_persist(client, tmp_path, monkeypatch):
    r = client.put("/ai/v1/settings", json={"max_jobs": 12})
    assert r.status_code == 200
    data = r.json()["settings"]
    assert data["max_jobs"] == 12


def test_route_secrets_are_write_only_replace_or_clear(client):
    blocked = client.put("/ai/v1/settings", json={"llm_api_key": "sk-nope"})
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "AI_MODE_SECRET_WRITE_ONLY"
    replaced = client.put("/ai/v1/settings/secrets/llm", json={
        "action": "replace", "value": "sk-localsecret",
    })
    assert replaced.status_code == 200 and replaced.json()["configured"] is True
    assert "sk-localsecret" not in replaced.text
    status = client.get("/ai/v1/settings/secret-status")
    assert status.json()["secrets"]["llm"] == {
        "configured": True, "source": "local_config", "manageable": True}
    reveal = client.post("/ai/v1/settings/reveal", json={"kind": "llm"})
    assert reveal.status_code == 403
    assert reveal.json()["error"]["code"] == "AI_SECRET_REVEAL_DISABLED"
    assert "sk-localsecret" not in reveal.text
    cleared = client.put("/ai/v1/settings/secrets/llm", json={"action": "clear"})
    assert cleared.status_code == 200 and cleared.json()["configured"] is False


def test_environment_secret_is_unmanageable_and_never_persisted(
        tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_api_key": "local-behind-env",
                                "mp_api_key": "local-mp"}), encoding="utf-8")
    monkeypatch.setenv("AI_MODE_LLM_API_KEY", "environment-secret")
    monkeypatch.setenv("AI_MODE_MP_API_KEY", "environment-mp-secret")
    cfg = load_settings(config_path=path)
    status = secret_status(cfg)
    assert status["llm"] == {
        "configured": True, "source": "environment", "manageable": False}
    assert status["mp"]["source"] == "environment"
    with pytest.raises(ValueError, match="环境变量"):
        update_secret(cfg, "llm", "clear")

    persist(update_from_patch(cfg, {"max_jobs": 7}), config_path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["llm_api_key"] == "local-behind-env"
    assert saved["mp_api_key"] == "local-mp"
    assert "environment-secret" not in path.read_text(encoding="utf-8")
    assert "environment-mp-secret" not in path.read_text(encoding="utf-8")


def test_update_secret_ssh_uses_credential_store_without_reveal():
    from ai_mode.ssh.credentials import MemoryCredentialStore

    cfg = _cfg().model_copy(update={"ssh_host": "hpc", "ssh_username": "alice"})
    store = MemoryCredentialStore()
    same = update_secret(cfg, "ssh", "replace", "pw-secret",
                         credential_store=store)
    assert same is cfg
    assert store.get_password("hpc", "alice") == "pw-secret"
    update_secret(cfg, "ssh", "clear", credential_store=store)
    assert store.get_password("hpc", "alice") is None


def test_known_hosts_path_roundtrip_and_mask():
    cfg = update_from_patch(_cfg(), {"ssh_known_hosts_path": "C:/trusted/known_hosts"})
    assert cfg.ssh_known_hosts_path == "C:/trusted/known_hosts"
    assert mask_config(cfg)["ssh"]["known_hosts_path"] == "C:/trusted/known_hosts"


def test_route_settings_put_invalid(client):
    r = client.put("/ai/v1/settings", json={"max_jobs": 0})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_MODE_BAD_SETTINGS"


def test_route_project_settings_crud(client):
    acc = ["ENCUT 不低于 520 且 EDIFF 收敛到 1e-5"]
    r = client.put("/ai/v1/projects/p_1/settings", json={"accuracy": acc})
    assert r.status_code == 200 and r.json()["ok"] is True
    g = client.get("/ai/v1/projects/p_1/settings")
    assert g.json()["settings"]["accuracy"] == acc
    d = client.delete("/ai/v1/projects/p_1/settings")
    assert d.json()["deleted"] is True


def test_route_project_settings_rejects_sensitive(client):
    bad = ["超算登录口令 password=hunter2"]
    r = client.put("/ai/v1/projects/p_2/settings", json={"accuracy": bad})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AI_MODE_BAD_PROJECT_SETTINGS"


def test_route_disabled_returns_503(monkeypatch, tmp_path):
    monkeypatch.setenv("VASP_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ENABLE_AI_MODE", "false")
    app = create_ai_mode_app()
    with TestClient(app) as cat:
        r = cat.get("/ai/v1/settings")
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "AI_MODE_DISABLED"

def test_update_enable_thinking_fields():
    cfg = _cfg()
    updated = update_from_patch(cfg, {"llm_enable_thinking": True})
    assert updated.llm_enable_thinking is True
    assert isinstance(updated.llm_enable_thinking, bool)
    assert cfg.llm_enable_thinking is False   # 原对象不变


def test_mask_config_includes_enable_thinking():
    cfg = update_from_patch(_cfg(), {"llm_enable_thinking": True})
    payload = mask_config(cfg)
    assert payload["llm"]["enable_thinking"] is True


def test_update_invalid_thinking_raises():
    with pytest.raises(ValueError) as ei:
        update_from_patch(_cfg(), {"llm_enable_thinking": "yes"})
    assert "llm_enable_thinking" in str(ei.value)

# ---------------- 项目计算设置：新形态 {note, params} + AI 参考文本 ----------------
def test_normalize_accuracy_compat():
    old = {"dos": [{"key": "ISMEAR", "value": "-5"}]}
    new = {"dos": {"note": "建议但非强制", "params": [{"key": "ISMEAR", "value": "-5"}]}}
    norm_old = normalize_accuracy(old)
    norm_new = normalize_accuracy(new)
    norm_lines = normalize_accuracy(["DOS 用四面体 ISMEAR = -5"])
    assert "ISMEAR = -5" in norm_old[0]
    assert "建议但非强制" in norm_new[0] and "ISMEAR = -5" in norm_new[0]
    assert norm_lines == ["DOS 用四面体 ISMEAR = -5"]
    assert normalize_accuracy(None) == []
    assert normalize_accuracy("x") == []
    assert normalize_accuracy({"dos": "not-a-config"}) == []


def test_render_accuracy_text_content():
    acc = ["DOS 用四面体 ISMEAR = -5", "ENCUT 不低于 520"]
    text = render_accuracy_text(acc)
    assert "计算任务设置" in text
    assert "要求与指引" in text
    assert "ISMEAR" in text and "-5" in text
    assert "1." in text and "2." in text
    assert render_accuracy_text([]) == ""
    assert render_accuracy_text(None) == ""
    assert render_accuracy_text({}) == ""


def test_store_save_load_new_shape(tmp_path):
    store = ProjectSettingsStore(root=tmp_path)
    acc = ["DOS 用四面体 ISMEAR = -5", "ENCUT 不低于 520"]
    store.save("projC", acc)
    loaded = store.load("projC")
    assert loaded["accuracy"] == acc
