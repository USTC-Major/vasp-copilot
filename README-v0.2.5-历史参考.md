# 历史参考：v0.2.5 时点的 README

> 以下为 2026-09-18 旧版 README 原文，包含当时的功能、API、部署与验证记录；它不是 0.3.0-rc.1 的安装说明或当前验收结论。当前说明请返回 [README](./README.md)。

# VASP-Copilot v0.2.5（VASP-Doctor × Workflow Builder）

> 当前分支正在进行 D0 解耦重构，尚未发布。下列 v0.2.5 发布记录属于历史版本，不能作为本分支验收结果。
> 本分支把任务执行、授权、SSH、监控与确定性报告迁至 8000 的 Toolbox；8500 为可选 AI 客户端。重构后的验证结果另见验收回执。

VASP 计算**诊断**（vasp-doctor）与**工作流生成**（vasp-copilot / Workflow Builder）一体化后端 + 前端源码包。

> v0.2.5 修复 v0.2.4 的源码归档校验清单换行问题，保留全部应用功能。
> 采用固定换行配置的二进制归档校验，并新增跨换行回归测试。
> 旧 v0.2.4 tag 不变，其错误清单请勿用于验包。详见 [CHANGELOG.md](./CHANGELOG.md) `[0.2.5]`。

- 当前稳定版本：[v0.2.5](https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.2.5)
- 完整更新记录：[CHANGELOG.md](./CHANGELOG.md)

- 上传一个 VASP 运行目录 zip，依次完成：`安全解压 → 文件识别 → 解析 → 规则诊断 → 修复建议 → Markdown 报告 → （可选）LLM 通俗解释与追问`；
- 也可基于结构文件（POSCAR/CONTCAR/CIF）通过 AI 规划或手工确认生成完整 VASP 输入工作流（relax/static/dos/band 的 INCAR/KPOINTS/POSCAR/submit.sh 与运行说明），产物为确定性 zip 包。
- 诊断完全**规则化、可追溯**，存在未解决 Critical/High 时阻断继续下一步（安全第一）。

---

## 1. 包内容结构

```
./
├── backend/                     # 后端（Python/FastAPI，一体化：doctor + copilot）
│   ├── app/                     #   工具箱主后端（诊断/工作流/结构/材料，端口 8000）
│   ├── toolbox/                 #   独立执行核心：任务/授权/SSH/调度/监控/报告，由 8000 托管
│   ├── ai_mode/                 #   可选聊天与模型后端，HTTP 调用 Toolbox（端口 8500）
│   ├── examples/sample_run/     #   可直接上传的演示 VASP 运行目录
│   ├── scripts/                 #   smoke_test / export_openapi / collect_metrics / demo_fake_hpc / style_compare
│   ├── tests/                   #   pytest 全量测试（含 be_a 生成链路 golden 产物）
│   ├── requirements.txt         #   依赖清单
│   ├── Dockerfile  .dockerignore
│   ├── run.ps1  run.sh          #   一键启动（Windows / Linux-macOS）
│   ├── run_ci.ps1  run_ci.sh    #   一键 CI（全量测试 → OpenAPI 导出 → 冒烟）
│   ├── setup_env.ps1            #   Windows 一键环境准备（可选）
│   └── .env.example             #   后端环境变量示例（无秘密）
├── frontend/                    # 前端（React 19 + TypeScript + Vite + Ant Design）
│   ├── src/                     #   页面/组件/mocks/类型
│   ├── src/components/ai/       #   智能模式 UI（聊天/规划/进度/设置）
│   ├── public/                  #   静态资源与 mockServiceWorker
│   ├── Dockerfile  nginx.conf   #   前端镜像：Node 构建 → nginx 托管 + API 反代
│   ├── package.json  package-lock.json  vite.config.ts  tsconfig*.json
│   └── README.md                #   Vite 模板默认说明（非本交付文档）
├── docker-compose.yml           # 默认 frontend + 8000；ai profile 按需启用 8500
├── start_services.ps1           # Windows 一键启动/守护（三服务）
├── start_services.sh            # Linux/macOS 一键启动/守护（三服务）
├── .env.example                 # 根环境变量示例（Docker 用；无秘密）
├── README.md                    # 本文档
└── SHA256SUMS.txt               # 包内全部文件校验和（格式：<sha256>  <路径>）
```

> 本包为**源码交付**：不含 `node_modules/`、`dist/`、虚拟环境与运行数据，安装依赖后即可运行。

## 2. 快速开始

### 2.0 服务与启动方式

| 服务 | 端口 | 说明 |
|---|---|---|
| 前端 | 5173 | 浏览器入口 http://127.0.0.1:5173 |
| 工具箱主后端 | 8000 | 诊断 / 工作流 / 结构 / 材料，以及唯一任务执行与监控服务 |
| 智能模式后端（可选） | 8500 | 聊天与模型，经 HTTP 调用 8000（详见第 2.4 节） |

只使用 Toolbox 时，按 2.1、2.2 节启动 8000 与前端即可，不要求模型配置或 8500 服务。现有一键脚本仍启动三服务：

```powershell
# Windows（PowerShell，在仓库根目录）
powershell -ExecutionPolicy Bypass -File .\start_services.ps1          # 缺哪个补哪个
powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -Status  # 查看状态
powershell -ExecutionPolicy Bypass -File .\start_services.ps1 -Watch   # 守护：掉线自动拉起
```

```bash
# Linux / macOS（在仓库根目录）
bash start_services.sh             # 缺哪个补哪个
bash start_services.sh --status    # 查看状态
bash start_services.sh --watch     # 守护：掉线自动拉起
```

Docker Compose 默认启动 Toolbox 与前端，AI 按需启用：

```bash
docker compose up --build -d                  # Toolbox + 前端
docker compose --profile ai up --build -d     # 另外启用 AI 服务
docker compose ps
```

启动后浏览器打开 **http://127.0.0.1:5173**。真实计算还需配置 SSH、调度系统、输入文件和用户提供的提交脚本；服务启动不代表计算环境已就绪。

Toolbox 手动入口为 `/toolbox/projects`：创建项目和任务、选择工作区、填写计算步骤、登记已有输入，按预览逐次确认文件写入或上传。认领自己提供的脚本并通过预检后，批准具体提交；之后在同一任务页查看作业号、监控、诊断与报告。执行配置入口为 `/toolbox/settings`。此路径不调用 AI 模型。

结构生成 ZIP 到 Toolbox 工作区的交接、POTCAR/脚本准备和固定结果文件下载，见 [首版基础计算与结果取回](./首版基础计算与结果取回.md)。任务终态后可在当前任务页分别下载提交目录现有的 OUTCAR、OSZICAR、CONTCAR（每文件最多 32 MiB），或保存确定性报告；文件哈希只证明传输完整，不证明该文件由本次计算生成或科学收敛。

### 2.1 后端（本机）

要求：Python 3.10+。

```bash
cd backend
pip install -r requirements.txt        # 安装依赖（pymatgen 等）
```

Windows 一键环境准备（可选，自动建 .venv 并装依赖）：

```powershell
powershell -ExecutionPolicy Bypass -File backend\setup_env.ps1
```

启动（二选一）：

```bash
# Windows
cd backend
powershell -ExecutionPolicy Bypass -File .\run.ps1          # 默认 127.0.0.1:8000
# Linux / macOS
./run.sh
```

或直接：

```bash
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后验证：

- 健康检查：http://127.0.0.1:8000/health
- OpenAPI 文档：http://127.0.0.1:8000/api/v1/openapi.json （浏览器 /docs 可交互调试）
- 全链路冒烟：`python scripts/smoke_test.py`（上传→诊断→报告→预览→解释→下载修复）

### 2.2 前端（本机）

要求：Node.js 20.19+ 或 22.12+（与 `package-lock.json` 中 Vite 8 的运行时要求一致）。

```bash
cd frontend
npm ci
npm run dev                          # Vite dev server，默认 http://localhost:5173
```

- dev server 已配置代理：`/api/v1/*` → `http://127.0.0.1:8000`，后端启动后即可直连；
- 无后端时可加 `?mock=1` 使用内置 MSW mock 演示。

### 2.3 Docker Compose（容器化，AI 可选）

```bash
# 仓库根目录；.env 为可选（不存在也能启动，仅使用默认值）
docker compose up --build -d
docker compose --profile ai up --build -d  # 可选 AI 客户端
docker compose ps                    # backend healthcheck 通过后为 healthy
docker compose logs -f backend
docker compose down                  # 停止（保留数据卷）
docker compose down -v               # 彻底清理（连同全部数据）
```

执行服务必须单 worker 运行；同一数据根只能有一个 8000 执行进程，重复启动会被进程锁拒绝。AI 服务也使用单 worker。不要用多个 worker 绕过数据锁。

8000 与 8500 使用相同 `VASP_AI_HOME` 根目录，但分别写 `execution_store.json` 和 `chat_store.json`。首次启动从旧 `ai_store.json` 只读导入对应数据，保留原文件；旧待批准动作需要重新申请，不自动重放未知提交。执行设置存入 `toolbox_config.json`，旧 `config.json` 作为兼容输入。迁移前应备份整个数据根；损坏数据应排查并恢复备份，不能删除原文件后当作空项目继续。

### 2.4 智能模式（可选客户端）

智能模式让用户用**自然语言**规划并在逐次确认下推进 VASP 计算流程：

> 选择工作区 → 规划 → 准备输入 → 预检 → 单次确认调度提交 → Toolbox 后台监控 → 下游重新预检/确认 → 查错 → 结果报告

- 双工作区：本地工作区与超算工作区（SSH）分别指定，真实计算与提交发生在超算侧；
- 依赖链：多作业（如 relax → relax/static → relax/static/dos）自动排序；前序完成后下游作业重新预检并等待新的单次确认，失败则级联阻断；
- 安全边界：LLM 不可自由执行本地/远端命令，也不能代写提交脚本；用户提供的 `*.sh` 必须按路径、大小和 SHA-256 认领；INCAR 仅接受结构化提案并经差异预览与精确确认；每次 `sbatch`/`cbatch` 都需要与当前预检快照绑定的一次性确认；
- 后台监控：由 8000 Toolbox 执行，按可配置间隔（默认 60s）采集状态；聊天结束或 8500 停止不应中断监控。停止跟踪不会取消远端作业。报告只能表达已取得的证据，不能将离队或流程结束等同于科学结果可靠；
- 交互恢复：后台生成状态与消息持久化，页面重载/流式断连后可重新同步；项目额外设置提供可编辑的常用计算模板。
- 调度适配：设置页显式选择 Slurm 或 ParaCloud，并可指定后端本机 SSH 私钥；连接身份变更须重新预检和确认，不自动猜测或切换平台。
- 结果核验与恢复：结合调度退出码和实际输出判断结束；失败诊断提供受控恢复入口，重试仍需预检与授权，不保证任意计算自动修复。

8500 通过 `TOOLBOX_URL` 连接执行服务，本机默认 `http://127.0.0.1:8000`，Compose 中为 `http://backend:8000`。Toolbox 不可用时应明确报错，不回退为另一套本地执行。

Fake/Mock 适配器用于隔离验证；未配置真实 SSH 时不能把模拟结果写成真实提交。原诊断侧 Fake HPC 演示与 Toolbox 的真实任务记录不是同一条执行链。

#### 智能聊天导入 MP 结构

在智能设置中保存 MP API key，并为任务选择本地工作区。可对 AI 说：
“搜索 BaTiO3 的 Materials Project 候选，列出材料 ID 和空间群号，先不要导入”。
选择具体材料后，再要求“导入选定的 MP 材料 ID，生成 POSCAR 确认卡，不上传、不提交”。
也可直接指定已知的材料 ID。

AI 使用专用 `mp_search` / `mp_import_poscar` 工具；结构由后端确定性转换，
确认前不写文件。默认目标为任务本地工作区根目录 `POSCAR`，也可指定已规划作业目录；
覆盖已有文件会在卡片中明示。拒绝、过期或文件变化后旧确认不能写入。
获取结构仅访问 MP 官方固定 HTTPS 端点，不接受任意 URL 或命令，
不把密钥发送给 LLM。无序/部分占位、异常结构或超出大小限制时明确报错。
上传超算仍需单独确认。AI 任务与 Toolbox 任务共用执行服务的 MP 配置；原材料搜索页面的 `MP_API_KEY` 配置仍单独保留。

### 2.5 五分钟演示路径

1. 启动三服务（2.0 节），打开 http://127.0.0.1:5173；
2. **诊断链路**：进入诊断页，上传 `backend/examples/sample_run/` 打包的 zip（或用 `demo_cases/failed_runs/` 里的故障样例，如 `scf_reached_nelm`），查看规则诊断报告与修复建议；
3. **智能模式**：进入智能模式 → 新建项目 → 新建任务 → 确认任务显示的实际运行环境为 Fake/Real → 输入目标（如「对 Si 做结构优化，然后算态密度」）→ 审核结构化输入提案、用户提交脚本及预检摘要 → 对每次写入/上传/调度提交分别作一次性确认；依赖作业完成后需重新预检并确认；
4. **全链路冒烟**：`cd backend && python scripts/smoke_test.py`（上传 → 诊断 → 报告 → 预览 → 下载修复）。

## 3. 配置说明（.env.example）

根目录与 `backend/` 各有一个 `.env.example`（**无秘密**，仅默认值与注释）。需要自定义时复制为 `.env` 再修改：

```bash
cd backend
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/macOS
```

常用项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `TTL_SECONDS` | 86400 | 诊断记录/上传数据过期时间 |
| `MAX_UPLOAD_BYTES` | 209715200 | 上传压缩包上限 200MB |
| `MAX_UNCOMPRESSED_BYTES` | 1572864000 | 解压后总大小上限 |
| `CORS_ORIGINS` | http://localhost:5173,http://127.0.0.1:5173 | 前端跨域白名单 |
| `ENABLE_LLM` | false | LLM 通俗解释总开关（默认关闭） |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 示例 | 填写真实值后才启用 LLM |
| `ENABLE_LOCAL_FAKE_HPC` | true | 本地 Fake HPC 桥接演示 |
| `ENABLE_BAND_WORKFLOW` | false | band 工作流开关（关闭时请求 band 返回 409） |
| `ENABLE_POTCAR_ASSEMBLY` | false | POTCAR 组装（安全红线，默认关闭） |
| `MP_API_KEY` / `ENABLE_MATERIALS_PROJECT` | 空 / false | Materials Project 数据库搜索（可选） |
| `VASP_AI_HOME` | `~/.vasp-ai` | 执行库与聊天库的兼容根目录；两服务各写自己的文件 |
| `TOOLBOX_URL` | `http://127.0.0.1:8000` | 可选 8500 服务调用 Toolbox 的地址；Compose 覆盖为容器地址 |

> 安全提示：真实 API key 只应填入本地 `.env`，**不要提交到仓库/交付包**。

## 4. 测试与 CI

```bash
cd backend
python -B -m pytest tests -q              # 全量测试（doctor 诊断 + BE-A 生成 + workflow API）
python scripts/export_openapi.py          # 导出 backend/openapi.json（供前端 TS 类型）
```

v0.2.5 历史发布验证（不是当前 D0 分支验收结果）：

- 后端测试：`1175 passed, 0 failed`（含工具箱主后端、ai_mode 与 9 项发布完整性回归，46 warnings）；
- 前端测试：`61 passed, 0 failed`；
- lint 0 errors（保留既有 Fast Refresh warnings）；Vite production build 成功，页面级代码分包；
- v0.2.4 的端到端冒烟已通过，本版未改动应用功能；`SHA256SUMS.txt` 共 525 项，按最终 Git 归档字节逐项校验。

MP 合约、响应边界、结构转换、密钥隔离和单次确认已通过隔离回归测试；
本轮环境未配置真实 MP API key，因此未做真实联网结构下载验收。

Compose/YAML、端口和持久化映射已完成静态校验；真实 docker compose build/up 尚待具备 Docker 的环境验证。

已通过应用完成一个真实 ParaCloud Si 静态作业的授权提交、监控、输出核验与报告链路。输入/目录准备、计划纠正及报告更正包含人工维护步骤；该案例不等于全程自主、多步 band 自动纠错或计算精度验证。

一键 CI（Windows / Linux）：

```bash
powershell -ExecutionPolicy Bypass -File backend\run_ci.ps1   # Windows
./backend/run_ci.sh                                            # Linux/macOS
```

## 5. 功能特性速览

- **诊断链路**：上传 zip → 安全解压（防路径逃逸/zip bomb）→ 文件识别（INCAR/OUTCAR/OSZICAR/POSCAR/CONTCAR/CIF/KPOINTS/日志）→ 规则诊断（证据+严重度）→ 修复建议 → Markdown 报告 → 下一步门控；
- **CIF 转换**：通过 pymatgen 保留真实晶格与原子坐标；无效、缺坐标、无序、部分占据及多结构 CIF 采用 fail-closed 处理，不生成占位坐标；
- **OSZICAR 诊断**：区分真实电子迭代与离子步汇总，支持 DAV/RMM/CG/DMP/SDA，并为 NELM、NSW 与 SCF 震荡提供对应文件证据；
- **KPOINTS 科学语义**：自动规则网格按标准 KPPA÷原子数确定目标总 K 点数，依据倒易晶格分配各方向网格并默认 Γ 中心；仍需按具体体系审阅网格比例与收敛性。显式 Monkhorst-Pack 输入/渲染保持支持，band 路径使用合法的 VASP Line-mode/Reciprocal 格式；
- **续算文件证据**：诊断记录 CHGCAR/WAVECAR 的存在性、大小与哈希，但不读取其二进制正文或交给 LLM；
- **工作流生成**：`POST /api/v1/workflows/plan|generate`，支持 AI 规划（自然语言→DAG，LLM 不稳定自动降级）与手工确认；产物含 workflow_plan.json / workflow_manifest.json / README_run_order.md / INPUT_CHECK_REPORT.md / POTCAR_REQUIRED.md 与各 step 输入文件，zip 字节级可复现；
- **DFT+U 参数确认**：DFT+U 默认关闭；U/J/L 由用户填写并逐条明确确认；用户修改 element/L/U/J 后原有确认自动失效，必须重新确认；生成前展示最终参数摘要，摘要与实际 API payload 使用同一快照；
- **scheduler 参数一致性**：scheduler 参数从 plan 到 generate replay 保持一致（节点数、每节点任务数、墙钟时间、VASP 可执行文件提示）；
- **页面级懒加载**：工作流、诊断与 HPC 页面按路由分包加载，减小首屏体积；
- **HPC 桥接**：P1 Fake 适配器完整状态机（plan → preflight → 授权部署 → 提交 → 回收），只生成不执行、argv 白名单、幂等防重放；
- **LLM 解释与对话**：`POST /api/v1/chat` 通用多轮对话（模型设置界面配置，默认关闭）；agent/handle 自然语言映射为诊断工具；
- **plots 输出**：SCF 曲线只使用真实电子迭代能量；证据不足时返回空序列、不伪造曲线；磁矩以结构化序列供前端直接绘图。
- **AI Mode 受控写入与提交**：INCAR/KPOINTS 分别走结构化校验器与确定性生成器；提交脚本必须由用户提供并认领；授权卡单次有效且绑定目标、内容哈希、预检摘要和实际运行环境。

工作流产物的字节可复现性以相同规范输入和经验证的依赖环境为前提：仓库内置 recipe YAML 在 checkout 时固定为 LF，loader 仍对实际读取的原始字节计算 SHA-256；ZIP 条目固定 Unix 主机元数据。已有 Windows 工作树在新增 `.gitattributes` 后可能仍保留旧 CRLF，需在保存未提交改动后用干净检出或新 clone 获取规范字节。既有保存的计划和归档不会自动重写；不同压缩库版本的 ZIP 字节一致性不在此保证范围内。

## 6. 安全边界与已知限制

- POTCAR：本项目**不下载、不内置、不拼接**（`ENABLE_POTCAR_ASSEMBLY=false`，VASP 许可证限制）；无 POTCAR 时生成 POTCAR_REQUIRED.md 且全部 step `runnable=false`；
- 工具箱存储为**内存 + TTL 临时文件**，AI 项目/任务另行持久保存；公网/多人使用前必须补认证、CSRF 与租户隔离；
- 受限文件（WAVECAR/CHGCAR/POTCAR 等）预览一律拒绝；OUTCAR 预览限 500 行；
- `vasp_binary_hint` 已有前端 token 白名单（仅允许安全的可执行文件名或 POSIX 路径，拒绝 shell 运算符）；后端 SchedulerSettings 的 shell 字段统一服务端校验仍是启用真实 HPC 自动提交前的阻塞项；
- AI Mode 禁止 LLM 任意执行命令、通用写文件或代写 shell 脚本；INCAR/KPOINTS、上传和提交分别受结构化校验、哈希绑定与一次性确认约束；
- SSH 只接受系统或用户明确配置的 known_hosts，未知/变化主机密钥 fail closed；密钥 API 不提供明文回显，环境变量秘密不会持久化到 config.json；
- `Real`/`Fake`/`None` 表示实际 HPC 执行后端，不表示 LLM 类型；`None` 不会生成提交确认卡；
- 主工作流的确定性生成链路仍为零 LLM、零 HPC、零网络，可离线运行；AI Mode 的自然语言规划可调用用户配置的 LLM，但所有副作用由确定性边界控制；
- 当前仍是单用户、本地/可信网络定位；虽已验证一个人工协助的真实 ParaCloud Si 静态案例，Docker、跨机器部署及通用 Slurm 实机兼容性仍待验证。
- 显式 SSH 私钥不支持口令解锁，换电脑需重新配置本机路径；结果核验文本上限 16 MiB，超限保留异常证据而不把截断文本判为成功。
- 模型响应、排队与结果回传耗时不可控；环境以具体智能任务的 Real/Fake/None 标识及调度/文件证据为准，工具箱离线演示不代表全局环境。

## 7. 完整性校验（SHA256SUMS.txt）

包内 `SHA256SUMS.txt` 记录了 **Git 源码归档中除自身外全部归档文件**的 SHA-256 校验和，不包含运行数据和未跟踪文件。请在 GitHub Source code 归档解压后核对；Windows checkout 若由 `core.autocrlf` 转为 CRLF，字节会与归档不同，不能直接据此认定源码被篡改。v0.2.4 的清单存在发布错误，请改用 v0.2.5。

维护者生成流程：先暂存全部发布文件，再执行 `python backend/scripts/release_checksums.py --write --ref INDEX`；暂存新清单并提交后，执行 `python backend/scripts/release_checksums.py --ref HEAD`。工具强制 `core.autocrlf=false` 和 `core.eol=lf`，直接读取二进制归档，不修改用户全局 Git 设置。最后必须下载 GitHub ZIP 和 tar.gz，分别用 `--archive <路径>` 校验通过后才发布 Release；清单不包括自身。

```bash
# 解压后，在包根目录执行
sha256sum -c SHA256SUMS.txt        # Linux/macOS
# Windows（PowerShell）：
Get-FileHash -Algorithm SHA256 <file>   # 与清单逐项比对
```

## 8. 打包信息

- 当前稳定版本：v0.2.5
- 发布页面：https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.2.5
- GitHub 自动提供 Source code (zip) 与 Source code (tar.gz)
- 发布源码归档不包含虚拟环境、`node_modules`、缓存、私有输入或运行数据
- `SHA256SUMS.txt` 按 Git 归档中的原始文件字节生成，覆盖源码、测试、前端与 demo case 文件；条目数与结果见第 4 节发布验证
- 所有路径均为相对路径，无绝对路径/符号链接
