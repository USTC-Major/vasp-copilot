# 智能模式（AI Mode）v0.2.1

AI Mode 是独立于工具箱主后端（`backend/app`）的自然语言任务规划与 HPC
执行服务，默认监听 `127.0.0.1:8500`。它可以让 LLM 提出计算计划和受限的
输入参数，但所有文件副作用与 Slurm 提交都由确定性代码校验和授权。

## 目录与启动

- `agent/`：意图、受限工具和多轮决策循环；
- `authorize/`、`consent.py`：策略判定与一次性授权状态机；
- `incar_draft.py`、`tools/draft.py`：结构化 INCAR、输入/脚本指纹与预检；
- `orchestrator.py`：作业依赖、预检、提交和监控；
- `ssh/`：严格 known_hosts 的 SSH/SFTP 适配器；
- `settings/`、`config.py`：设置、密钥状态和环境变量；
- `server.py`：FastAPI 入口。

```powershell
cd backend
powershell -ExecutionPolicy Bypass -File .\run_ai.ps1
```

也可直接运行：

```powershell
python -m uvicorn ai_mode.server:app --host 127.0.0.1 --port 8500
```

`ENABLE_AI_MODE=true` 时服务可用；为 `false` 时服务仍能启动，但业务端点
返回禁用响应。工具箱主后端不会导入本包。

## v0.2.1 安全模型

- LLM 不能调用任意本地/远端命令，也不能通用写文件或生成 shell 脚本；
- INCAR 只能通过带类型的参数条目提出，未知标签、重复标签、非有限数值和
  注入字符会被拒绝；确定性预览与最终内容 SHA-256 完全一致后才能单次确认；
- KPOINTS 只走确定性生成器；POTCAR 只作为用户文件参与存在性/指纹检查，
  其内容不会显示给 LLM；
- 提交脚本必须由用户提供，并绑定规范化路径、大小、修改时间和 SHA-256；
- 写入、上传和 `sbatch` 分别使用一次性授权；目标、内容、预检摘要或实际
  执行环境变化后旧确认失效，依赖作业不会继承旧确认自动补提；
- SFTP 上传先写同目录临时文件，核对 SHA-256 后原子改名；提交前再次执行
  硬预检；不确定的调度结果记录为 `unknown`，不会自动重试；
- SSH 使用系统或 `AI_MODE_SSH_KNOWN_HOSTS_PATH` 指定的 known_hosts，未知或
  变化的主机密钥直接拒绝；
- 密钥接口只返回 `configured/source/manageable` 状态，不提供明文读取。

页面中的运行环境标签来自实际执行适配器：`Real` 表示真实 SSH/HPC，`Fake`
表示显式注入的离线模拟器，`None` 表示没有执行后端。LLM 类型或 API key
不会改变该标签；`None` 模式不能创建提交确认卡。

## 环境变量与密钥

常用变量包括 `AI_MODE_MAX_JOBS`、`AI_MODE_POLL_INTERVAL_SECONDS`、
`AI_MODE_BILLING_ESTIMATE_ENABLED`、`AI_MODE_LLM_*`、`AI_MODE_SSH_*` 和
`AI_MODE_MP_API_KEY`。环境变量中的秘密只在当前进程读取，不会被保存回
`config.json`；SSH 密码使用系统 keyring。

已知限制：本地 LLM/Materials Project 密钥仍可由设置页写入用户目录下的
`config.json`，尚未统一迁移到 keyring；当前是单用户本地/可信网络工具，
没有登录和租户隔离；v0.2.1 未在真实 Docker、SSH、HPC 或 Slurm 环境完成
端到端实机测试。
