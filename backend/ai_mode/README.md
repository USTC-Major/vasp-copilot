# 智能模式（ai_mode）工程骨架

完全独立于工具箱（backend/app.*）的第二条能力线；本包不 import 工具箱任何代码。

## 目录
- `gate.py`    独立开关 ENABLE_AI_MODE（默认关）
- `paths.py`   本地数据目录 ~/.vasp-ai（VASP_AI_HOME 可覆盖；测试/多实例）
- `config.py`  配置加载（默认 < ~/.vasp-ai/config.json < 环境变量 AI_MODE_*）
- `storage.py` 本地化布局幂等创建（sessions/skills/logs + 兜底 config.json）
- `server.py` 独立 FastAPI 入口（create_ai_mode_app()）

## 启动
```
backend> powershell -ExecutionPolicy Bypass -File .\run_ai.ps1        # 默认 8500 端口
backend> ../.venv/Scripts/python.exe -m uvicorn ai_mode.server:app --host 127.0.0.1 --port 8500
```

## 开关
- `ENABLE_AI_MODE=true`  -> 完整可用（端点返回配置汇总，密钥掩码）
- `ENABLE_AI_MODE=false` -> 默认；仍可启动服务器但 /ai/v1/config 返回 503 禁用信封
工具箱服务从不 import 本包，开关关闭时零影响。

## 环境变量（AI_MODE_*）
`AI_MODE_MAX_JOBS`（默认 20）、`AI_MODE_POLL_INTERVAL_SECONDS`（默认 60）、
`AI_MODE_BILLING_ESTIMATE_ENABLED`、`AI_MODE_LLM_*`、`AI_MODE_SSH_*`、`AI_MODE_MP_API_KEY`。
私人信息只存本地 ~/.vasp-ai/config.json，不进项目文件、不上传。