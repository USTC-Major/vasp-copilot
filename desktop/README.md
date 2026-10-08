# Windows 桌面源码构建与0.4.0配套包

本目录承接已试用的 Desktop V3 原生壳和控制器。启动后准备本地服务并打开真实 `/workflow` 页面；其他业务页面的视觉主题保持当前实现。0.4.0候选提供配套包与源码构建说明；仍不包含安装器、签名、更新器或科学计算验证。整包本地验收见仓库根《0.4.0本地验收步骤》。

## 前提

- 64 位 Windows，已安装 .NET Framework 4.8 或更高版本及其 Framework64 C# 编译器。
- Node.js 24 和 npm。前端依赖由既有 `frontend/package-lock.json` 固定；不新增或升级依赖。
- 运行桌面时已有 WebView2 Runtime；SDK 是编译依赖，不能代替 Runtime。脚本不安装 Runtime。
- 运行本地服务时已有 Python 3.10+ 及项目后端依赖，实际需通过控制器的模块导入检查。Windows CI 使用 Python 3.11；本地测试可显式指定已有解释器。构建脚本不安装 Python 或后端依赖。

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

## SDK、品牌资源与产物

WebView2 SDK 固定为 [Microsoft.Web.WebView2 1.0.3650.58](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3650.58)。官方包 URL、包 SHA256 和所用 DLL/许可证文件 SHA256 在 `webview2.lock.json`。每次构建都核验包和所用缓存文件；下载或解压校验失败不会发布完整缓存目录。`desktop/.cache` 不入库，SDK 不注册或安装到系统。

包内原始 `LICENSE.txt`、`NOTICE.txt` 在 `licenses` 保留，构建输出同时携带它们；提交中不包含 SDK 工具树、包缓存或 DLL。

已批准的原始 `assets/app-icon.svg` 和生成的 PNG/七尺寸 ICO 均作为品牌资产入库。普通构建核对 `icon-export.json` 的来源/输出哈希，不要求 Pillow。维护者需要重新导出时，可在已有 Pillow 10.4.0 的独立环境执行 `python desktop/assets/export_icon.py`；脚本不会安装依赖。更换资产时须一起审核 SVG、PNG、ICO 和清单。

输出为 `frontend/dist` 与 `desktop/dist`。桌面目录内包含 EXE、相邻三个 WebView2 DLL、EXE.config 和 WebView2 许可证/NOTICE。`build-manifest.json` 记录本次输入、工具版本、七项运行产物及前端资源哈希；清单不收录自身或目录内的无关旧文件。失败重建会撤销旧成功清单，旧 EXE 不能据此视为新构建成功。旧 Framework 编译器不承诺不同构建的 EXE 位级相同。

## 运行与隔离试用

```powershell
./desktop/dist/VASP-Copilot-Desktop-V3.exe
```

保留上述相对目录结构：程序从 `desktop/dist` 定位仓库 `launcher/runtime.py`，首次在“启动设置”选择已有安装目录（含 `backend` 与 `frontend/dist`），后续自动寻找已有 Python 和本地端口。复制单个 EXE 不构成安装包。

正常启动偏好位于 `%LOCALAPPDATA%/VASP-Copilot/desktop-v3`，不读取或迁移 D2 启动偏好；业务配置沿用项目既有优先级。V3 与 D2 有独立单实例标识；同一 Toolbox 业务 home（`VASP_AI_HOME`）的重复服务由后端进程锁拒绝，控制器仅管理本次所属进程。

隔离试用可显式指定绝对测试目录：

```powershell
./desktop/dist/VASP-Copilot-Desktop-V3.exe --test-profile 'C:/test/vasp-desktop' --test-root 'C:/src/vasp-copilot' --test-python 'C:/path/to/existing/python.exe'
```

隔离模式使用独立 home/data/偏好、跳过项目 `.env`、阻断共享 keyring 写入与 WebView 外部资源，强制关闭智能模式。省略 `--test-root` 并使用新的 profile 可检查首次目录设置。不要把正常用户配置或科研数据作为测试 profile。

## 验证范围

`test.ps1` 将控制器测试宿主编译到忽略缓存，并用显式已有 Python 运行 16 个受控场景：环境候选、中文/空格路径、端口占用者保留、绑定争用及三次上限、身份核验、部分失败清理、取消、合成项目/设置持久化、服务全灭和重试恢复。测试 HOME、数据、偏好、日志、pycache 均独立；异常结束时只回收测试启动的进程。

新增 `desktop-windows` CI 从准确 PR head 干净构建，恢复既有后端声明依赖到独立测试环境，再运行控制层检查。CI 只上传小型清单/结果，不上传 profile、配置、科研数据、缓存或完整状态目录；已有前端/Linux/Docker CI 保持不变。

这证明可构建和受控本地软件行为，不证明真实桌面目视、窗口前台激活、其他 DPI/跨屏、原生下载同名拒覆盖、全新机器兼容或科学正确性。测试不调用真实 MP、LLM、SSH、HPC 或 VASP；安装器、升级、发布及新科学能力另行处理。
