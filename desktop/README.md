# Windows 桌面源码构建与运行环境

本目录承接 Desktop V3 原生壳和控制器。v0.5.0 使用包根目录 `VASP-Copilot.exe` 单入口，并提供统一设置中心与旧启动偏好兼容；仍不包含安装器、签名或更新器。用户操作见[0.5.0 快速开始与升级](../0.5.0快速开始.md)，正式版本与附件证据见[发布范围与验收说明](../docs/release/v0.5.0/发布范围与验收说明.md)。

v0.5.0 沿用 v0.4.1 的标准 Windows x64 CPython 3.11–3.14 支持政策与锁定依赖，保留注册信息、PATH、常见目录及 Conda 环境自动发现。3.15 尚缺关键 Windows wheel，识别后明确提示原因。原兼容依据见 [兼容计划与验收](../docs/development/v0.4.1/Python兼容与自动发现.md)。

## 完整功能入口

v0.5.0 构建生成根目录 `VASP-Copilot.exe` 的完整包，不再提供 CMD 用户入口。不带参数默认具备完整功能：自动端口、Python 检测、专用依赖准备及真实功能入口；首次默认启用 AI，后续保留已保存的开关选择。AI 可关闭，基础工作流、诊断、任务和后处理无需配置模型。`--full-features` 是同一正式模式的兼容参数，不能与隔离 `--test-profile` 混用。旧 v0.4.1 附件与其历史说明保留，用户步骤见[桌面单入口说明](../桌面单入口快速开始.md)。

## 前提

正常桌面的全局按钮为“设置 / 关于 / 重试启动 / 退出”。设置通过受控消息软切换到 `/settings`，嵌入网页不再显示重复齿轮，两种环境都移除侧栏执行设置。中心复用现有模型/材料/超算表单；AI 不可达时使用既有 Toolbox 接口。原生启动表单在故障时直接回退可用，保存真正改变启动配置前说明重启影响并确认；未改变配置不重新加载当前文档。

- 64 位 Windows，已安装 .NET Framework 4.8 或更高版本及其 Framework64 C# 编译器。
- Node.js 24 和 npm。前端依赖由既有 `frontend/package-lock.json` 固定；不新增或升级依赖。
- 运行桌面时已有 WebView2 Runtime；SDK 是编译依赖，不能代替 Runtime。脚本不安装 Runtime。
- 普通/隔离入口运行本地服务时需已有受支持的标准 CPython 及后端依赖。完整功能入口自动准备独立依赖，支持 Windows x64 CPython 3.11/3.12/3.13/3.14；Windows CI 对这四个版本执行首次安装与复用检查。构建脚本仍不安装 Python 或后端依赖。

从仓库根目录执行：

```powershell
./desktop/build.ps1
./desktop/test.ps1 -PythonExecutable 'C:/path/to/existing/python.exe'
```

默认构建会从官方 NuGet 恢复固定 SDK、执行 `npm ci --ignore-scripts` 恢复既有锁文件依赖，并设置 `VITE_USE_MOCKS=false` 构建生产前端和 x64 原生壳。需要访问 NuGet/npm，已具备相应缓存时可离线执行：

```powershell
./desktop/build.ps1 -FrontendDependencies offline -SdkPackage 'C:/cache/Microsoft.Web.WebView2.1.0.3650.58.nupkg'
```

`-FrontendDependencies existing` 仅供已恢复依赖后的增量构建，不是干净检出证明。干净检出无需旧 `node_modules`、`dist`、DLL、EXE 或本机 `.tmp` 目录。前端目录存在 `.env*` 文件时构建明确拒绝，避免读取个人构建配置。

`desktop/test.ps1` 包含实际包设置中心探针：生产 EXE 的原生壳/表单、生产前端、合成数据与真实本地服务，检查入口、草稿、故障配置恢复、基础保存和未配置 SSH 反馈。WebView2 缺失会明确记为实际 UI 未执行，不能据此宣称界面验收通过。独立浏览器可另执行 `python desktop/tests/settings_center_tests.py --browser 'C:/path/to/chrome.exe'`，使用已安装 Chromium 和 Node 24、独立临时 profile 与固定本地服务，阻断外部请求，无新增测试依赖。

## 自动运行环境

完整功能入口使用 `launcher/environment.py`，读取 `launcher/python-support.json`，按对应 `backend/requirements-win-cp3xx-x64.lock` 安装完整闭包；每包精确版本及 wheel SHA256，禁止源码临时编译。运行依赖来自 `requirements-runtime.txt`，开发/测试仍用 `requirements.txt`。依赖变化必须重建相应锁并通过四版本 CI，不能只改宽泛运行清单。

环境和下载缓存继续位于原 `desktop-v040-full/runtime/full`，偏好迁移不搬动环境。按解释器版本/架构/锁摘要分开，未完成标记不作为可用凭证；核验成功后复用。取消仅停止所属安装进程树，不修改用户 Python。仅直连官方 PyPI，不继承 pip 配置、额外源或代理；失败有日志与重试提示。

## SDK、品牌资源与产物

WebView2 SDK 固定为 [Microsoft.Web.WebView2 1.0.3650.58](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3650.58)。官方包 URL、包 SHA256 和所用 DLL/许可证文件 SHA256 在 `webview2.lock.json`。每次构建都核验包和所用缓存文件；下载或解压校验失败不会发布完整缓存目录。`desktop/.cache` 不入库，SDK 不注册或安装到系统。

包内原始 `LICENSE.txt`、`NOTICE.txt` 在 `licenses` 保留，构建输出同时携带它们；提交中不包含 SDK 工具树、包缓存或 DLL。

已批准的原始 `assets/app-icon.svg` 和生成的 PNG/七尺寸 ICO 均作为品牌资产入库。普通构建核对 `icon-export.json` 的来源/输出哈希，不要求 Pillow。维护者需要重新导出时，可在已有 Pillow 10.4.0 的独立环境执行 `python desktop/assets/export_icon.py`；脚本不会安装依赖。更换资产时须一起审核 SVG、PNG、ICO 和清单。

构建暂存为 `frontend/dist` 与 `desktop/dist`，使用新 EXE 名；随后自动调用 `stage-package.ps1`，从版本控制输入和经核验产物生成新的 `desktop/.cache/packages/package-*` 完整包，位置记录在 `desktop/dist/last-package.json`。包根包含 EXE、相邻 config、三个 WebView2 DLL 和许可证；后端、前端及 helper 同包。已有输出目录不会被清空。构建和包清单记录输入/产物哈希，失败重建撤销旧成功清单。Framework 编译器不承诺位级相同。

## 运行与隔离试用

```powershell
$package = (Get-Content desktop/dist/last-package.json -Raw | ConvertFrom-Json).directory
& (Join-Path $package 'VASP-Copilot.exe')
```

须完整解压；发布 EXE 从自身目录定位 helper、前后端，不依赖工作目录。首次预填当前包目录；保存的目录指向另一包时提示核对并阻止混用。内部 desktop/dist 仅供源码构建调试，不作为额外用户入口。

正式偏好固定为 `%LOCALAPPDATA%/VASP-Copilot/desktop/preferences.json`，同一用户的正式入口共用单实例。首次兼容 desktop-v040-full、desktop-v3、launcher；不同旧来源需明确选择，旧原件保留，已有新配置优先。旧 full 模式隐含 home/data 延续；其他旧空路径依据原配置优先级解析为可确认的绝对路径，保留原数据根。动态路径或旧目录不可访问等无法可靠确认时停止迁移并提示处理，不搬数据库、凭据、浏览器数据或缓存。旧 V3 正在运行时提示先退出，不杀进程或接管服务。

设置页显示来源/目标；保存先写盘并回读核验，再接受内存新值。读取损坏或访问失败不能当成首次使用。根 EXE 的 `--diagnose-startup` 不启动服务，输出 startup-diagnostics.json，含 EXE/版本/模式、配置位置及上次操作阶段，不含业务配置或密钥。具体发布证据见对应版本的 `release-provenance.json`。

隔离试用可显式指定绝对测试目录：

```powershell
& (Join-Path $package 'VASP-Copilot.exe') --test-profile 'C:/test/vasp-desktop' --test-root $package --test-python 'C:/path/to/existing/python.exe'
```

隔离模式使用独立 home/data/偏好、跳过项目 `.env`、阻断共享 keyring 写入与 WebView 外部资源，强制关闭智能模式。省略 `--test-root` 并使用新的 profile 可检查首次目录设置。不要把正常用户配置或科研数据作为测试 profile。

## 验证范围

`test.ps1` 将控制器测试宿主编译到忽略缓存，并用显式已有 Python 运行受控场景，新增实际 SettingsForm 保存/独立进程恢复、迁移和读写失败检查：环境候选、中文/空格路径、端口占用者保留、绑定争用及三次上限、身份核验、部分失败清理、取消、合成项目/设置持久化、服务全灭和重试恢复。另包含完整功能启动、开关、动态 reviewer、子进程凭据隔离、重启及配置缺失检查。测试 HOME、数据、偏好、日志、pycache 均独立；异常结束时只回收测试启动的进程。

新增 `desktop-windows` CI 从准确 PR head 干净构建，恢复既有后端声明依赖到独立测试环境，再运行控制层检查。CI 只上传小型清单/结果及 CI 专用环境的安装日志（安装进程不继承业务凭据或代理），不上传 profile、配置、科研数据、缓存或完整状态目录；已有前端/Linux/Docker CI 保持不变。

这些受控检查证明可构建与对应本地软件行为，不能推出其他 DPI/跨屏、全新机器或科学正确性。测试不调用真实 MP、LLM、SSH、HPC 或 VASP；本版已完成与复用的实际包检查、用户复测及正式发布记录分别见[发布范围与验收说明](../docs/release/v0.5.0/发布范围与验收说明.md)。
