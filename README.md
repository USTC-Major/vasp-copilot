# VASP-Copilot

**面向材料计算的 VASP 辅助工作台。**

在一个界面中准备计算输入、管理任务、诊断结果，并把已有 VASP 文件绘制成 DOS 和能带图。AI 解释与交互可按需启用。

[下载 v0.5.0](https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.5.0) · [快速开始与升级](0.5.0快速开始.md) · [更新日志](CHANGELOG.md) · [问题反馈](https://github.com/USTC-Major/vasp-copilot/issues)

## 你可以用它做什么

| 想完成的事情 | VASP-Copilot 提供的帮助 |
|---|---|
| 准备计算输入 | 导入 POSCAR/CIF 或 Materials Project 结构，查看晶体，准备弛豫、静态、DOS 或常规 PBE/PBE+U 能带输入 |
| 管理本地赝势 | 登记自己的授权 PAW-PBE 库，确认元素顺序与变体后拼接 POTCAR，下载或加入工作流 ZIP |
| 管理计算任务 | 在自己的计算资源上准备文件、预检、确认提交、监控状态并取回必要结果 |
| 排查计算问题 | 查看收敛曲线、磁矩变化和诊断报告，核对修复建议与参数差异 |
| 分析与绘图 | 导入本地结果，或预览并确认取回已有任务结果；从本地缓存绘制 DOS、XML 投影 DOS 和常规路径能带 |
| 保存与导出 | 保存分析与图形设置，选择配色，导出白底 PNG、CSV 和 JSON |

AI 是可选功能。不配置模型，也可以使用基础工作流、确定性诊断、任务工具和结果后处理。

## 快速开始

1. 从 [Releases](https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.5.0) 下载 **`vasp-copilot-0.5.0-windows-x64.zip`**，完整解压到新目录。
2. 退出旧桌面，双击新包根目录的 **`VASP-Copilot.exe`**。
3. 在启动设置中核对当前包目录，Python 留空自动检测；首次等待专用依赖环境准备完成。
4. 从“生成工作流”“诊断计算”“计算任务”或“结果后处理”开始，按需在统一“设置”中配置模型、材料服务和超算。

需要已有 **Windows x64、标准 CPython 3.11–3.14 x64、.NET Framework 4.8 和 WebView2 Runtime**。桌面包无需 Node/npm；首次依赖准备需直连官方 PyPI。Python 3.15 尚未纳入完整应用支持。

升级时会读取已知旧启动设置；来源冲突或数据路径无法确认时会提示处理。请保留旧包与数据，具体步骤见[快速开始与升级](0.5.0快速开始.md)。

## 文档与功能范围

- 主线源码新增表面切面、固定层约束、吸附位点与单模型 Workflow 接入；这些催化预处理能力尚未包含在上方 v0.5.0 安装包中。使用与边界见 [催化预处理说明](docs/development/cat-stage/README.md) 和 [接入 Workflow](docs/development/cat-stage/WORKFLOW.md)。
- [结果后处理、桌面启动与升级](0.5.0快速开始.md)
- [源码安装指南](Windows源码安装与基础使用.md) · [桌面构建说明](desktop/README.md)
- [基础计算与结果取回](首版基础计算与结果取回.md)
- [本版发布公告](docs/release/v0.5.0/发布公告.md) · [功能范围与验收说明](docs/release/v0.5.0/发布范围与验收说明.md)

本版后处理支持非自旋与普通共线数据；投影能带、NCL/SOC、形成能与吸附能、催化台阶图、磁矩自动生成等仍待后续。默认参数、诊断和 AI 建议需结合体系核对与收敛测试。

运行 VASP 需要用户自己的合法使用权限与计算环境。本项目不提供真实赝势；POTCAR 使用用户有权使用的本地库，拼接前需确认元素顺序与变体。

## 团队与参与

**中国科学技术大学 · 宋礼课题组 / USTC · Song Group**

Developed by **夏梓童、黄思远、王奕轩**。

欢迎通过 [Issues](https://github.com/USTC-Major/vasp-copilot/issues) 反馈问题，或通过 [Pull Requests](https://github.com/USTC-Major/vasp-copilot/pulls) 参与改进。反馈请附版本、复现步骤和必要的脱敏信息，不要上传密钥或真实赝势文件。
