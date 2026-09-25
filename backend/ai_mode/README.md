# VASP-Copilot 智能模式（AI Mode）0.3.0

AI Mode 是可选自然语言客户端，默认监听 `127.0.0.1:8500`，由
`ENABLE_AI_MODE=true` 显式启用。基础计算不需要启动它：8000 Toolbox
独立持有任务执行、授权、SSH、监控和确定性报告；8500 经 `TOOLBOX_URL`
调用 8000，不自行拥有第二套执行状态。LLM 可提出计划与受限输入建议，
文件副作用及调度提交须经 Toolbox 的确定性检查和逐次授权。当前真实模型
闭环和判断质量尚未验收；已有 Windows 源码 + ParaCloud/NICHE + Si2
单步 static 的真实证据来自 AI 关闭的 Toolbox 手工路径。

## 目录与启动

- `agent/`：意图、受限工具和多轮决策循环；
- `authorize/`、`consent.py`：策略判定与一次性授权状态机；
- `incar_draft.py`、`tools/draft.py`：结构化 INCAR、输入/脚本指纹与预检；
- `orchestrator.py`、`ssh/`：历史兼容模块；当前执行归 8000 Toolbox；
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
返回禁用响应。Toolbox 基础版请使用仓库根目录的
[Windows 源码安装与基础使用](../../Windows源码安装与基础使用.md)，只启动
8000 和前端。旧 `start_services.ps1` 会尝试启动 AI，是开发便利方式。

下文标明 v0.2.x 的案例与条目按其历史时点保留，不应当作 0.3.0 的
真实模型验收或当前执行所有权说明。独立 reviewer 保持默认关闭，真实模型
提供方与远端环境尚未验证。

## 安全模型（延续 v0.2.1，补充连接身份绑定）

- LLM 不能调用任意本地/远端命令，也不能通用写文件或生成 shell 脚本；
- INCAR 只能通过带类型的参数条目提出，未知标签、重复标签、非有限数值和
  注入字符会被拒绝；确定性预览与最终内容 SHA-256 完全一致后才能单次确认；
- KPOINTS 只走确定性生成器；POTCAR 只作为用户文件参与存在性/指纹检查，
  其内容不会显示给 LLM；
- 提交脚本必须由用户提供，并绑定规范化路径、大小、修改时间和 SHA-256；
- 写入、上传和 `sbatch`/`cbatch` 分别使用一次性授权；目标、内容、预检摘要或实际
  执行环境变化后旧确认失效，依赖作业不会继承旧确认自动补提；
- SFTP 上传先写同目录临时文件，核对 SHA-256 后原子改名；提交前再次执行
  硬预检；不确定的调度结果记录为 `unknown`，不会自动重试；
- SSH 使用系统或 `AI_MODE_SSH_KNOWN_HOSTS_PATH` 指定的 known_hosts，未知或
  变化的主机密钥直接拒绝；
- SSH 可在设置页指定 `ssh_identity_file`（环境变量
  `AI_MODE_SSH_IDENTITY_FILE`）：只保存后端本机现有密钥文件的绝对路径，不上传
  或展示私钥内容。指定后只使用该密钥，不访问密码库、不搜索其他密钥、不启用
  SSH agent 或密码回退。当前不支持需要口令解锁的密钥；换电脑或容器需重新配置
  当地可访问路径。未指定则保留原密码认证流程；两种认证都保持严格主机校验。
- 密钥接口只返回 `configured/source/manageable` 状态，不提供明文读取。

页面中的运行环境标签来自实际执行适配器：`Real` 表示真实 SSH/HPC，`Fake`
表示显式注入的离线模拟器，`None` 表示没有执行后端。LLM 类型或 API key
不会改变该标签；`None` 模式不能创建提交确认卡。

## 环境变量与密钥

### v0.2.2：ParaCloud 适配

设置页“调度平台”或 `AI_MODE_SCHEDULER_BACKEND` 可显式选择 `slurm`（默认）
或 `paracloud`。ParaCloud 使用 `cbatch`、不带 `-u` 的 `cqueue`，识别
`NICHE-I...` 一类云作业号；不会在提交失败后尝试另一套命令。云作业离队后
还须取得 `cacct -j <job_id>` 的唯一历史状态与退出码，再核查实际输出。
上传、结果写回和未知状态均不能当作计算完成。回执不确定时禁止自动重提。

确认快照绑定调度平台、SSH 主机、端口、账号和密钥/known_hosts 路径；修改后
必须重新预检、生成草稿和确认。作业监控也不能静默切换到另一连接。
取消提示按平台提供命令，但不自动执行取消。脚本仍须由用户提供并明确认领。

2026-09-16 已通过本软件完成一个真实 ParaCloud Si 静态作业的授权提交、
监控、正常结束核验与报告链路。该验证包含人工准备输入/目录、纠正计划及
维护报告的步骤，不代表全程自主、多步 band 自动纠错或精度收敛验证。
平台输出格式不匹配时暂停判断并保留证据，不自动放宽校验。

常用变量包括 `AI_MODE_MAX_JOBS`、`AI_MODE_POLL_INTERVAL_SECONDS`、
`AI_MODE_BILLING_ESTIMATE_ENABLED`、`AI_MODE_LLM_*`、`AI_MODE_SSH_*` 和
`AI_MODE_MP_API_KEY`。环境变量中的秘密只在当前进程读取，不会被保存回
`config.json`；SSH 密码使用系统 keyring。

已知限制：本地 LLM/Materials Project 密钥仍可由设置页写入用户目录下的
`config.json`，尚未统一迁移到 keyring；当前是单用户本地/可信网络工具，
没有登录和租户隔离；Docker、跨机器部署和通用 Slurm 实机兼容性仍待验证。
允许的结果文本读取上限为 16 MiB，超限不会将截断内容判为成功；模型延迟
与调度排队仍需现场预留时间。环境以具体任务的 Real/Fake/None 标识及
调度/文件证据为准，工具箱离线演示不代表智能任务的运行环境。

## 独立文件 reviewer（可选）

8000 与 8500 均显式设置 `VASP_REVIEWER_ENABLED=true` 和同一份至少 32 字节的
`VASP_REVIEWER_SHARED_SECRET`；8000 另须设置固定 `VASP_REVIEWER_URL`。
回环部署可用 `http://127.0.0.1:8500`，现有 Compose 私有网络可显式用
`http://ai_mode:8500`，其他受信部署使用 HTTPS。服务间密钥不是模型 API key，
不得放入浏览器配置、公开 API 或模型输入。8500 还需 `ENABLE_AI_MODE=true`
以及现有设置中的真实 OpenAI 兼容模型地址、key、模型名；reviewer 不使用
fake 模型，未配置或调用失败时文件卡保持待人工处理。状态接口只报告 8000
本地配置是否齐备，不测试 8500 或付费模型连通。

用户只能在 Toolbox 文件页为具体 job/attempt 建立精确 reviewer 范围，并单独
确认激活。reviewer 仅核对机械文件操作的路径、范围和预算；copy 与 write_text
正文均不发送模型，正文与科学适用性仍需人工审阅。脚本、已知科学输入、
受限文件及不确定用途回人工。模型建议通过 8000 owner 的一次性绑定与原子
决议后，才可能进入原文件执行队列；scope 撤销即可停止新审查，已有执行结果
仍以原回执为准。当前仅有离线替身验证，真实提供方/远端环境尚未验证。
