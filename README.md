# VASP-Doctor 0.3.0-rc.2（候选版，尚未正式发布）

VASP-Doctor 是面向 VASP 计算的源码应用。本候选版以 **Toolbox 独立基础计算**为主线：从已有结构准备输入，人工核对并交接到工作区，逐次批准远端写入和提交，查看监控与报告，并取回必要结果。AI 模式是可选客户端；不运行 AI 服务也能使用 Toolbox。解耦及 A—E 限定能力已进入当前代码，早期 D0 开发记录属于历史状态。

当前真实计算证据限定于 **Windows 源码部署 + ParaCloud/NICHE + Si2 单步 static**：一次真实作业经页面确认提交、自动监控到终态，并分别下载 OUTCAR、OSZICAR、CONTCAR，下载字节与远端哈希一致。该案例不能证明其他站点、体系、计算类型或科学参数的普适适用性。Docker 已有容器构建和合成数据 smoke 验证，尚不是 Docker 连接真实 HPC 的验收。AI 真实模型闭环与判断质量尚未验收；默认保持关闭，reviewer 也默认关闭。

本版仍是单用户、本机或可信网络使用范围。POTCAR 须由用户从合法来源自行准备并核对元素顺序，站点提交脚本与 VASP/调度环境也须由用户提供、审阅。项目不内置、下载或拼接 POTCAR，亦不通过 VASPKIT 代拼。没有新增科学模块。

## 安装与启动

Windows 从**干净源码包**安装、默认只启动 Toolbox 和前端的完整 PowerShell 步骤，见 [Windows 源码安装与基础使用](./Windows源码安装与基础使用.md)。默认前端 `127.0.0.1:5173`，Toolbox `127.0.0.1:8000`；指南也给出独立数据目录及可选端口覆盖，适合与已有服务并行验收。本轮推荐与最终包验收环境一致的 Python 3.12.7、Node.js 24.19.0；首次安装须联网取得 Python 与 npm 依赖。

入口与分工：

| 页面或服务 | 用途 |
|---|---|
| `/workflow` | 从有真实晶格和完整坐标的 POSCAR/CIF 生成、下载输入 ZIP；检查输入摘要 |
| `/toolbox/projects` | 人工建立项目和任务、登记输入、预检、逐次确认上传及提交、监控与取回结果 |
| `/toolbox/settings` | 配置执行适配器、SSH 和调度目标；远端身份变化时重新预检 |
| `/ai/*` 与 `8500` | 可选自然语言辅助；需显式配置和启动，真实模型闭环未验收 |

结构 ZIP 到 Toolbox 的手工交接、用户提供 POTCAR/脚本、作业状态与结果解释，见 [首版基础计算与结果取回](./首版基础计算与结果取回.md)。Toolbox 只提供绑定提交目录中当前存在的 OUTCAR、OSZICAR、CONTCAR 单文件下载，单文件上限 32 MiB；WAVECAR/CHGCAR 等大文件不会自动下载。传输哈希证明收到的文件一致，不证明文件必定由本次计算生成，也不证明科学收敛。运行或下载前应核对站点记录、输出时间和科学内容。

旧 `start_services.ps1` 是会启动三服务的开发便利脚本，不作为基础版默认启动方式。`docker-compose.yml` 默认启用 Toolbox 和前端，可选 `ai` profile；其合成 smoke 证据不扩大上述真实计算支持范围。

## 版本与完整性

- 本源码树版本为 **0.3.0-rc.2**；仍是候选交付，未创建该版本的 tag 或 GitHub Release。[CHANGELOG](./CHANGELOG.md) 记录本候选与历史版本。[v0.2.5](https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.2.5) 是此前已发布版本，其测试与校验结论不直接适用于本候选。
- 旧版的详细 API、部署与验证说明完整保留在 [v0.2.5 README 历史参考](./README-v0.2.5-历史参考.md)，其中标注的时点状态不能覆盖本候选版说明。
- 候选归档内的 `SHA256SUMS.txt` 应覆盖除清单本身外的全部归档文件；对**实际收到的 ZIP**运行 `python backend/scripts/release_checksums.py --archive 'C:\path\to\candidate.zip'`，逐文件检查覆盖范围与 SHA-256。再用 PowerShell `Get-FileHash -Algorithm SHA256 'C:\path\to\candidate.zip'`，同交付时提供的**外部 ZIP 整包 SHA-256**比对。清单验证须针对归档原始字节，不能将 Windows Git 工作树可能转为 CRLF 的文件直接混比。
- 源码包不应包含依赖目录、运行数据、凭据或 POTCAR。用户在本机配置的私钥、API key 和计算文件不属于可再分发源码。
