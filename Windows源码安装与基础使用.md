# VASP-Copilot Windows 源码安装与基础使用（0.5.1）

以下命令面向 v0.5.1 新解压的源码目录、PowerShell 和本机浏览器。可使用已有标准 CPython 3.11–3.14 x64 环境与 Node.js 24/npm；安装依赖需要网络。确切构建环境见发布来源记录，不同电脑仍需按安装步骤核对。使用普通用户权限即可；历史真实计算证据不因版本更新自动扩展。桌面包用户先读[快速开始](0.5.1快速开始.md)。本版沿用既有支持政策和依赖，不因版本号变更升级依赖。

## 桌面配套包的简化入口

若下载的是Windows桌面配套包，已含生产前端和EXE：当前源码通过 desktop/build.ps1 生成完整包（位置见 desktop/dist/last-package.json），双击该包根目录的 `VASP-Copilot.exe`。首次自动检测 Python 并准备专用依赖；仍需已有 Python、.NET 4.8 和 WebView2 Runtime。不要只复制 EXE。已发布历史包沿用各自快速开始。

源码首次试用请使用下面的独立数据目录；桌面隔离运行方式见 [desktop/README.md](desktop/README.md)。

## 1. 干净安装

在**源码根目录**打开 PowerShell；请将首行路径替换为实际解压目录。`python -m venv` 创建隔离环境，随后从本包依赖清单安装；`npm ci` 从锁文件安装前端依赖。无需使用旧 `setup_env.ps1`。

```powershell
$sourceRoot = 'C:\path\to\VASP-Copilot-0.5.1'
Set-Location $sourceRoot
python --version
node --version
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\backend\requirements.txt
Set-Location .\frontend
npm ci
```

安装后分别用两个 PowerShell 窗口启动服务。不要用 `start_services.ps1` 启动基础版；那个旧开发便利脚本会同时尝试启动 AI 服务。此处不启动 8500，不需要模型 API key。

## 2. 启动 Toolbox（窗口 A）

用新的本地目录存储诊断与 Toolbox 数据；不要把原先 8000 服务的数据根用于并行候选验收。`DATA_DIR` 控制诊断数据，`VASP_AI_HOME` 控制 Toolbox 执行设置和任务库。示例数据根可改成任意本机绝对路径。

```powershell
$sourceRoot = 'C:\path\to\VASP-Copilot-0.5.1'
$dataRoot = Join-Path $env:LOCALAPPDATA 'VASP-Copilot\0.5.1-local'
# 已安装用户可保留原数据目录；目录名不影响产品名称。
New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
$env:DATA_DIR = Join-Path $dataRoot 'doctor'
$env:VASP_AI_HOME = Join-Path $dataRoot 'toolbox'
$env:ENABLE_LLM = 'false'
$env:ENABLE_AI_MODE = 'false'
$env:VASP_REVIEWER_ENABLED = 'false'
$env:ENABLE_LOCAL_FAKE_HPC = 'false'
$env:ENABLE_MATERIALS_PROJECT = 'false'
$env:ENABLE_POTCAR_ASSEMBLY = 'false'
$env:ENABLE_BAND_WORKFLOW = 'false'
Set-Location (Join-Path $sourceRoot 'backend')
& (Join-Path $sourceRoot '.venv\Scripts\python.exe') -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

此窗口保持运行。新窗口访问 `http://127.0.0.1:8000/health` 应返回健康响应。Toolbox 的任务与执行设置持久化在 `$env:VASP_AI_HOME`，而诊断上传/运行数据使用 `$env:DATA_DIR`。同一 Toolbox 数据根一次只运行一个执行服务进程，不用多 worker。

## 3. 启动前端（窗口 B）

```powershell
$sourceRoot = 'C:\path\to\VASP-Copilot-0.5.1'
Set-Location (Join-Path $sourceRoot 'frontend')
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5173/`。Vite 原配置把 `/api/v1` 代理到 `127.0.0.1:8000`；`/ai/v1` 指向可选的 8500，未启动 AI 时智能设置会说明服务不可用，Toolbox 不受影响。首页及 Toolbox 若显示空白任务，这是新数据目录的预期状态。停止时分别在两个窗口按 **Ctrl+C**，确认进程退出；这不会取消已在远端调度器运行的作业。

## 4. 与现有 5173/8000 服务并行（可选）

如果现有服务需要保持运行，使用另一组端口，例如 Toolbox `18030`、前端 `15330`，并为候选另选数据根。窗口 A 按第 2 节设置环境与目录后，只将末尾 uvicorn 命令的 `--port 8000` 改为 `--port 18030`。窗口 B 在**新解压目录的 `frontend` 下**创建临时 `vite.local.config.ts`，内容为：

```ts
import base from './vite.config';

export default {
  ...base,
  server: {
    ...base.server,
    open: false,
    port: 15330,
    strictPort: true,
    proxy: {
      ...base.server.proxy,
      '/api/v1': { target: 'http://127.0.0.1:18030', changeOrigin: true },
      '/ai/v1': { target: 'http://127.0.0.1:18530', changeOrigin: true },
    },
  },
};
```

然后在窗口 B 运行：

```powershell
$sourceRoot = 'C:\path\to\VASP-Copilot-0.5.1'
Set-Location (Join-Path $sourceRoot 'frontend')
npm run dev -- --config vite.local.config.ts --host 127.0.0.1
```

打开 `http://127.0.0.1:15330/`，检查页面实际请求走向 `18030`。这里的 `18530` 只是未启用的 AI 代理目标，基础版不启动它。验收结束按 Ctrl+C 停止两个窗口，并删除本地临时配置文件；产品自带 `vite.config.ts` 无需修改。若浏览器直接跨域请求后端而非经过 Vite 代理，再为窗口 A 增设 `$env:CORS_ORIGINS = 'http://127.0.0.1:15330'` 后启动；默认同源代理路径不需要该项。

## 5. 一次基础计算的人工流程

以上源码示例关闭 Materials Project、本地 POTCAR 组装、能带工作流与 AI，便于从本地结构开始。若需这些可选功能，请按对应配置启用；桌面完整入口的功能开关不受此示例限制。

在 `/workflow` 从含真实晶格与完整坐标的 POSCAR/CIF 生成单步 static 输入 ZIP，阅读输入检查报告。手工解压到**后端所在机器可访问**的工作区，核对 POSCAR、INCAR、KPOINTS；此示例未启用 POTCAR 组装，用户需从合法来源按元素顺序另备 POTCAR，审阅并替换为站点可用提交脚本。产品不调用 VASPKIT 拼接 POTCAR；启用本地库后可通过既有确认流程组装。

在统一“设置”的“超算与执行”配置站点、SSH 与调度目标；在 `/toolbox/projects` 建任务并指定本地及远端工作区，登记文件，按卡片逐次确认必要上传，认领脚本并执行预检。预检通过后逐次批准提交，以调度器作业号和持久回执判定是否提交成功。作业到终态后可分别下载 OUTCAR、OSZICAR、CONTCAR，每个最多 32 MiB；WAVECAR、CHGCAR 等大文件不会自动下载。具体交接和结果证据边界见 [首版基础计算与结果取回](./首版基础计算与结果取回.md)。首次安装与健康检查不代表已经完成 SSH 连接、实际作业或科学收敛验证。

## 6. v0.5.1 的表面与能量入口

- `/toolbox/surface-builder`：导入体相或已有 slab，应用表面与约束设置，构建单吸附物候选；独立导出或选一个接入 Workflow。
- `/toolbox/postprocessing/energy`：分别创建吸附能或材料形成能任务，导入 OUTCAR、CSV 或手填样本，在任务内建立计算卡，确认参考、计量与风险后锁定并计算。
- `/toolbox/postprocessing`：延续 DOS／能带后处理；基础能量的快照读取策略不改变此入口的任务终态限制。

操作步骤见[0.5.1 快速开始](0.5.1快速开始.md)，支持范围与验证边界见[本版发布说明](docs/release/v0.5.1/发布范围与验收说明.md)。本地几何构建和能量分析无需 SSH 或模型；实际远端取回仍需要自己的站点连接。
