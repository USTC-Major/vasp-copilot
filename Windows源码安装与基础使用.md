# VASP-Copilot Windows 源码安装与基础使用（0.3.0）

以下命令面向新解压的源码目录、PowerShell 和本机浏览器。推荐本轮最终包验收环境的 Python 3.12.7、Node.js 24.19.0 与 npm，并需安装依赖所需网络；其他版本未在本轮干净安装中验证。使用普通用户权限即可。0.3.0 已正式发布；仅将实际验收过的 Windows 源码 + ParaCloud/NICHE + Si2 单步 static 作为真实计算证据。

## 1. 干净安装

在**源码根目录**打开 PowerShell；请将首行路径替换为实际解压目录。`python -m venv` 创建隔离环境，随后从本包依赖清单安装；`npm ci` 从锁文件安装前端依赖。无需使用旧 `setup_env.ps1`。

```powershell
$sourceRoot = 'C:\path\to\VASP-Copilot-0.3.0'
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
$sourceRoot = 'C:\path\to\VASP-Copilot-0.3.0'
$dataRoot = Join-Path $env:LOCALAPPDATA 'VASP-Doctor\0.3.0-local'
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
$sourceRoot = 'C:\path\to\VASP-Copilot-0.3.0'
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
$sourceRoot = 'C:\path\to\VASP-Copilot-0.3.0'
Set-Location (Join-Path $sourceRoot 'frontend')
npm run dev -- --config vite.local.config.ts --host 127.0.0.1
```

打开 `http://127.0.0.1:15330/`，检查页面实际请求走向 `18030`。这里的 `18530` 只是未启用的 AI 代理目标，基础版不启动它。验收结束按 Ctrl+C 停止两个窗口，并删除本地临时配置文件；产品自带 `vite.config.ts` 无需修改。若浏览器直接跨域请求后端而非经过 Vite 代理，再为窗口 A 增设 `$env:CORS_ORIGINS = 'http://127.0.0.1:15330'` 后启动；默认同源代理路径不需要该项。

## 5. 一次基础计算的人工流程

在 `/workflow` 从含真实晶格与完整坐标的 POSCAR/CIF 生成单步 static 输入 ZIP，阅读输入检查报告。手工解压到**后端所在机器可访问**的工作区，核对 POSCAR、INCAR、KPOINTS；用户从合法来源按元素顺序另备 POTCAR，审阅并替换为站点可用提交脚本。ZIP 不包含 POTCAR，产品不调用 VASPKIT 拼接它。

在 `/toolbox/settings` 配置站点、SSH 与调度目标；在 `/toolbox/projects` 建任务并指定本地及远端工作区，登记文件，按卡片逐次确认必要上传，认领脚本并执行预检。预检通过后逐次批准提交，以调度器作业号和持久回执判定是否提交成功。作业到终态后可分别下载 OUTCAR、OSZICAR、CONTCAR，每个最多 32 MiB；WAVECAR、CHGCAR 等大文件不会自动下载。具体交接和结果证据边界见 [首版基础计算与结果取回](./首版基础计算与结果取回.md)。首次安装与健康检查不代表已经完成 SSH 连接、实际作业或科学收敛验证。
