# VASP-Copilot v0.2.0（VASP-Doctor × Workflow Builder）

VASP 计算**诊断**（vasp-doctor）与**工作流生成**（vasp-copilot / Workflow Builder）一体化后端 + 前端源码包。

> v0.2.0 新增**智能模式（AI Mode）**与参赛部署链路（三服务 Compose、
> 一键启动脚本、Nginx 反代），详见 [CHANGELOG.md](./CHANGELOG.md) `[0.2.0]`
> 与本文 2.0/2.4/2.5 节。

- 当前稳定版本：[v0.2.0](https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.2.0)
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
│   ├── ai_mode/                 #   智能模式后端（自然语言驱动的 VASP 计算闭环，端口 8500）
│   │   ├── api/v1/              #   接口路由（diagnosis/files/llm/chat/materials/structure/workflows/agent）
│   │   ├── agent/  diagnostics/ #   诊断侧：Agent 编排、规则引擎与修复
│   │   ├── parsers/ report/     #   解析器、Markdown 报告
│   │   ├── workflow/ recipes/   #   copilot 侧：工作流规划、Recipe 组合
│   │   ├── generators/          #   INCAR/KPOINTS/POSCAR/submit.sh 生成器
│   │   ├── hpc/                 #   Fake HPC 桥接适配器（演示，默认开启）
│   │   ├── llm/                 #   LLM 解释抽象（默认关闭）
│   │   ├── schemas/ security/   #   契约、安全解压
│   │   └── services/ core/      #   服务编排、配置与错误
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
├── docker-compose.yml           # 容器化一键部署（三服务：frontend + 8000 + 8500，含健康检查）
├── start_services.ps1           # Windows 一键启动/守护（三服务）
├── start_services.sh            # Linux/macOS 一键启动/守护（三服务）
├── .env.example                 # 根环境变量示例（Docker 用；无秘密）
├── README.md                    # 本文档
└── SHA256SUMS.txt               # 包内全部文件校验和（格式：<sha256>  <路径>）
```

> 本包为**源码交付**：不含 `node_modules/`、`dist/`、虚拟环境与运行数据，安装依赖后即可运行。

## 2. 快速开始

### 2.0 一键启动（推荐：三服务全起，智能模式开箱即用）

| 服务 | 端口 | 说明 |
|---|---|---|
| 前端 | 5173 | 浏览器入口 http://127.0.0.1:5173 |
| 工具箱主后端 | 8000 | 诊断 / 工作流 / 结构 / 材料 |
| 智能模式后端 | 8500 | 自然语言驱动的 VASP 计算闭环（详见第 2.4 节） |

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

或用 Docker Compose 一条命令起完整三件套（前端为 nginx 托管的构建产物，已反代两个后端）：

```bash
docker compose up --build -d       # 健康检查通过后 frontend 才启动
docker compose ps                  # 三个服务均 healthy/running
```

启动后浏览器打开 **http://127.0.0.1:5173** 即可使用全部功能（含智能模式）。

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

### 2.3 Docker Compose（容器化，三服务）

```bash
# 仓库根目录；.env 为可选（不存在也能启动，仅使用默认值）
docker compose up --build -d
docker compose ps                    # backend healthcheck 通过后为 healthy
docker compose logs -f backend
docker compose down                  # 停止（保留数据卷）
docker compose down -v               # 彻底清理（连同全部数据）
```

### 2.4 智能模式（AI Mode，本项目的核心亮点）

智能模式让用户用**自然语言**驱动完整的 VASP 计算闭环：

> 规划 → 建目录 → 预检 → 生成输入 → 确认 sbatch → 提交 → 后台监控/自动补提 → 查错 → 结果报告

- 双工作区：本地工作区与超算工作区（SSH）同名对应，计算与提交都发生在超算侧；本地 Fake HPC 可离线演示全流程；
- 依赖链：多作业（如 relax → relax/static → relax/static/dos）自动排序、前序完成自动补提，失败自动级联阻断；
- 安全边界：本地绝不写提交脚本（*.sh 由用户提供或经确认写入超算）；sbatch 必须经用户确认卡片；AI 生成的命令经策略门卫分级（红线直接拒、高风险需确认、日常操作免打扰）；
- 后台监控：提交后无需人工盯梢，按可配置间隔（默认 60s，设置页可改）自动推进，全部完成后自动生成报告；作业失败时报告开头点名失败作业与原因；
- 进度页：「查看当前进度」实时展示作业依赖链、等待队列、预检问题与计算报告。

无超算环境时智能模式内置本地 Fake HPC 适配器（默认开启），可完整演示从规划到报告的全流程。

### 2.5 五分钟演示路径

1. 启动三服务（2.0 节），打开 http://127.0.0.1:5173；
2. **诊断链路**：进入诊断页，上传 `backend/examples/sample_run/` 打包的 zip（或用 `demo_cases/failed_runs/` 里的故障样例，如 `scf_reached_nelm`），查看规则诊断报告与修复建议；
3. **智能模式**：进入智能模式 → 新建项目 → 新建任务 → 在设置里确认「本地 Fake HPC」开启 → 对话框输入目标（如「对 Si 做结构优化，然后算态密度」）→ 依次确认规划、输入文件与 sbatch 确认卡 → 之后无需操作，作业链自动推进直到报告生成；
4. **进度页**：点击聊天区上方「查看当前进度」，查看作业依赖链与报告；
5. **全链路冒烟**：`cd backend && python scripts/smoke_test.py`（上传 → 诊断 → 报告 → 预览 → 下载修复）。

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

> 安全提示：真实 API key 只应填入本地 `.env`，**不要提交到仓库/交付包**。

## 4. 测试与 CI

```bash
cd backend
python -B -m pytest tests -q              # 全量测试（doctor 诊断 + BE-A 生成 + workflow API）
python scripts/export_openapi.py          # 导出 backend/openapi.json（供前端 TS 类型）
```

v0.2.0 发布验证：

- 后端测试：`841 passed`（含工具箱主后端与 ai_mode）；
- 前端测试：`37 passed`；
- lint 0 errors（保留既有 Fast Refresh warnings）；Vite production build 成功，页面级代码分包；
- 完整性校验：`SHA256SUMS.txt` 共 499 项，0 项失败。

Compose/YAML、端口和持久化映射已完成静态校验；真实 docker compose build/up 尚待具备 Docker 的环境验证。

一键 CI（Windows / Linux）：

```bash
powershell -ExecutionPolicy Bypass -File backend\run_ci.ps1   # Windows
./backend/run_ci.sh                                            # Linux/macOS
```

## 5. 功能特性速览

- **诊断链路**：上传 zip → 安全解压（防路径逃逸/zip bomb）→ 文件识别（INCAR/OUTCAR/OSZICAR/POSCAR/CONTCAR/CIF/KPOINTS/日志）→ 规则诊断（证据+严重度）→ 修复建议 → Markdown 报告 → 下一步门控；
- **CIF 转换**：通过 pymatgen 保留真实晶格与原子坐标；无效、缺坐标、无序、部分占据及多结构 CIF 采用 fail-closed 处理，不生成占位坐标；
- **OSZICAR 诊断**：区分真实电子迭代与离子步汇总，支持 DAV/RMM/CG/DMP/SDA，并为 NELM、NSW 与 SCF 震荡提供对应文件证据；
- **工作流生成**：`POST /api/v1/workflows/plan|generate`，支持 AI 规划（自然语言→DAG，LLM 不稳定自动降级）与手工确认；产物含 workflow_plan.json / workflow_manifest.json / README_run_order.md / INPUT_CHECK_REPORT.md / POTCAR_REQUIRED.md 与各 step 输入文件，zip 字节级可复现；
- **DFT+U 参数确认**：DFT+U 默认关闭；U/J/L 由用户填写并逐条明确确认；用户修改 element/L/U/J 后原有确认自动失效，必须重新确认；生成前展示最终参数摘要，摘要与实际 API payload 使用同一快照；
- **scheduler 参数一致性**：scheduler 参数从 plan 到 generate replay 保持一致（节点数、每节点任务数、墙钟时间、VASP 可执行文件提示）；
- **页面级懒加载**：工作流、诊断与 HPC 页面按路由分包加载，减小首屏体积；
- **HPC 桥接**：P1 Fake 适配器完整状态机（plan → preflight → 授权部署 → 提交 → 回收），只生成不执行、argv 白名单、幂等防重放；
- **LLM 解释与对话**：`POST /api/v1/chat` 通用多轮对话（模型设置界面配置，默认关闭）；agent/handle 自然语言映射为诊断工具；
- **plots 输出**：SCF 曲线只使用真实电子迭代能量；证据不足时返回空序列、不伪造曲线；磁矩以结构化序列供前端直接绘图。

## 6. 安全边界与已知限制

- POTCAR：本项目**不下载、不内置、不拼接**（`ENABLE_POTCAR_ASSEMBLY=false`，VASP 许可证限制）；无 POTCAR 时生成 POTCAR_REQUIRED.md 且全部 step `runnable=false`；
- 存储为**内存 + TTL 临时文件**，单用户本地/演示定位；公网/多人使用前必须补认证、CSRF 与租户隔离；
- 二进制文件（WAVECAR/CHGCAR/POTCAR 等）预览一律拒绝；OUTCAR 预览限 500 行；
- `vasp_binary_hint` 已有前端 token 白名单（仅允许安全的可执行文件名或 POSIX 路径，拒绝 shell 运算符）；后端 SchedulerSettings 的 shell 字段统一服务端校验仍是启用真实 HPC 自动提交前的阻塞项；
- 生成链路零 LLM、零 HPC、零网络，可离线完整运行。

## 7. 完整性校验（SHA256SUMS.txt）

包内 `SHA256SUMS.txt` 记录了**除自身外全部文件**的 SHA-256 校验和。解压后核对：

```bash
# 解压后，在包根目录执行
sha256sum -c SHA256SUMS.txt        # Linux/macOS
# Windows（PowerShell）：
Get-FileHash -Algorithm SHA256 <file>   # 与清单逐项比对
```

## 8. 打包信息

- 当前稳定版本：v0.2.0
- 发布页面：https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.2.0
- GitHub 自动提供 Source code (zip) 与 Source code (tar.gz)
- 当前 Git 工作目录为精简源码副本，不包含虚拟环境、`node_modules`、缓存或运行数据
- `SHA256SUMS.txt` 已按当前源码快照重新生成，覆盖除自身外的源码、测试、前端与 demo case 文件，并通过 499 项校验
- 所有路径均为相对路径，无绝对路径/符号链接
