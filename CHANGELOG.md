# 更新日志

本文件记录 VASP-Copilot 面向用户的重要变化。开发中的变更先写入
`[Unreleased]`；正式发布时再归入对应版本。具体代码级修改可查阅 Git
提交历史。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.1] - 2026-09-05

### Fixed

- 修正自动 K 点网格的 KPPA 语义：目标总 K 点数现在按
  `KPPA ÷ 原子数` 计算，并依据倒易晶格长度分配各方向网格。
- 修正 band 工作流 KPOINTS 的 VASP Line-mode 格式和倒易坐标声明，
  避免生成语义错误或不可识别的能带路径文件。
- 诊断服务现在记录 CHGCAR/WAVECAR 的存在性、大小与哈希证据，供续算与
  故障判断使用，但不会预览或向 LLM 暴露二进制内容。
- 无 HPC 执行后端时不再生成看似可提交的授权卡；空提交脚本也会在预检前
  fail closed。

### Changed

- AI Mode 的 INCAR 修改改为结构化参数提案，经确定性校验、序列化、差异预览
  和精确哈希绑定后，必须由用户单次确认才会原子写入。
- AI Mode 的 KPOINTS 只能由确定性生成器产生；提交脚本必须由用户提供，
  并按路径、大小和 SHA-256 认领，LLM 不再代写脚本或自由执行命令。
- 提交、上传和文件修改授权统一为一次性状态机；确认内容、预检快照或目标
  变化后原确认立即失效，依赖作业也不再继承旧确认自动补提。
- 前端明确显示实际执行环境（Real/Fake/None），不再用 LLM 状态冒充 HPC
  运行模式。

### Security

- 禁用 LLM 可达的任意本地/远端命令执行、通用输入写入和远端脚本写入工具。
- SSH 改为严格 known_hosts 校验，未知或变化的主机密钥会被拒绝，不再自动
  信任首次连接。
- SFTP 上传采用同目录临时文件、SHA-256 核对和原子重命名；提交前重新校验
  输入、脚本、执行环境与预检摘要，降低确认后被替换和重复提交风险。
- 密钥接口改为只写状态模型，不提供明文回显；环境变量来源的密钥不会写入
  config.json，且前端不会错误显示为可清除的本地密钥。
- scheduler 调用采用单任务锁和持久化执行状态，结果不确定时标记 unknown
  且不自动重试，实现尽力而为的 at-most-once 语义。

### Known limitations

- 当前仍是单用户、本地/可信网络工具，没有登录、租户隔离和公网安全防护。
- 本版本没有在真实 Docker、SSH、HPC 或 Slurm 环境执行端到端测试；上线真实
  集群前仍需按目标集群配置 known_hosts、队列与提交脚本并人工验收。
- 本地 LLM 与 Materials Project 密钥仍可保存在用户目录的 config.json；
  SSH 密码继续使用系统 keyring，后续可统一迁移密钥存储。
- 结构化 INCAR 使用保守标签白名单，高级或站点特有标签可能被拒绝，需要扩展
  白名单并增加测试后才能启用。
- scheduler 的 at-most-once 为进程与持久化状态层面的尽力保证，无法替代
  Slurm 侧幂等键或人工核对未知提交结果。

## [0.2.0] - 2026-09-03

### Added

- 新增独立的智能模式（AI Mode）FastAPI 服务，默认端口 8500。
- 自然语言任务规划、项目/任务管理、聊天交互与流式响应。
- 本地与超算双工作区浏览。
- SSH/Slurm 作业提交、依赖链、状态监控与结果报告能力。
- 高风险操作授权卡片（提交/取消/删除等需用户确认）。
- Fake LLM / Fake HPC 离线演示模式，无真实凭据也可跑通全流程。
- AI Mode 前端页面、设置页、进度页与作业时间线。
- frontend + backend + ai_mode 三服务 Docker Compose 配置。
- Windows PowerShell 与 Linux/macOS 一键启动/守护脚本。
- Nginx SPA 托管及 `/api/v1`、`/ai/v1` 反向代理。

### Changed

- 前端增加 AI Mode 导航与相关页面。
- requirements 增加 AI Mode 所需依赖。
- README 与 .env.example 增加 AI Mode、部署及环境变量说明。
- AI Mode 数据通过 `VASP_AI_HOME` 持久化。

### Security

- Compose 默认只发布 `127.0.0.1:5173`。
- 主后端 8000 与 AI Mode 8500 默认仅在 Docker 内部网络访问。
- 提交、取消、删除和高风险命令需用户授权。
- LLM/Materials Project/SSH 凭据由用户提供，不进入仓库。
- POTCAR 不由项目提供或下载。

### Known limitations

- 当前仍定位为单用户、本地或演示部署，没有用户登录、租户隔离和
  公网安全防护。
- 不应将 8000/8500 直接暴露到公网或不可信局域网。
- 真实 HPC 使用前仍需完善/复核 SSH 主机指纹 TOFU、后端 scheduler
  shell 字段校验和集群 profile。
- AI Mode 当前是独立服务，尚未通过正式 HTTP adapter 完整复用原有
  Recipe/Doctor 后端。
- Docker Compose 已完成源码静态检查，但双方开发机均无 Docker，
  尚未完成真实 docker compose build/up 验证。
- Linux 无桌面容器中的 keyring/真实 SSH 凭据链尚需实机验证。
- 高级科研流程和最终科研结论不由系统保证。

## [0.1.2] - 2026-08-23

### Fixed

- 修复 WorkflowBuilder 表单中的 DFT+U 和 scheduler 参数未进入后端
  workflow 请求、最终落入默认值的问题。
- plan、GET workflow 和 workflow_id replay 现在保持 DFT+U/scheduler
  参数一致。
- DFT+U 默认关闭，不再自动填入 U=4.0 eV；U/J/L 必须由用户填写并明确确认。
- 用户修改 element/L/U/J 后，原有 confirmed_by_user 自动失效，必须重新确认。
- DFT+U 关闭时不再错误声称会生成 LDAU 数组。
- scheduler 请求字段 type 与响应字段 scheduler_type 使用独立 TypeScript 类型。

### Changed

- 增加工作流参数最终确认摘要，摘要和实际 API payload 使用同一不可变快照。
- 增加同步提交锁，防止快速双击产生重复 plan 请求。
- 工作流、诊断和 HPC 页面改为路由级懒加载。
- 建立前端 Vitest、Testing Library 和 MSW Node 测试链路。
- 后端工作流 API 新增 DFT+U/scheduler 回显，但保持旧 top-level
  goals/assumptions 请求兼容。

### Security

- 前端 vasp_binary_hint 只允许安全的可执行文件名或 POSIX 路径 token，
  拒绝 shell 运算符。
- 明确说明后端 SchedulerSettings 的所有 shell 字段仍需统一服务端校验；
  启用真实 HPC 自动提交前必须完成。

### Known limitations

- 当前只在前端校验 vasp_binary_hint；直接调用后端 API 仍需要后端统一
  shell 字段验证。
- 当前 MVP 的 DFT+U 表单只支持 d（L=2）和 f（L=3）轨道。
- DiagnosisResultPage 仍包含较大的图表依赖 chunk，后续可进一步拆包。
- 当前 POTCAR、真实 HPC 自动提交和缺失 CIF 对称操作恢复均不属于 v0.1.2。

## [0.1.1] - 2026-08-22

### Fixed

- 修复 CIF 转 POSCAR 时生成规律性占位坐标的问题，改为通过 pymatgen
  读取 CIF 中的真实晶格、原子种类和坐标，并在文件提供对称操作时进行
  合理的结构展开。
- `standardize=true` 时实际执行结构标准化，并通过
  `summary.standardized` 返回结果；不再静默忽略相关请求参数。
- 对无效 CIF、缺少坐标、无序或部分占据结构以及包含多个结构的 CIF
  采用明确的 fail-closed 错误，不再回退生成看似可运行的 POSCAR。
- 保持结构分析 API 的响应字段兼容，并为 CIF 转换增加真实坐标、晶格、
  对称展开、错误分类和零副作用测试。
- 修复 OSZICAR 将离子步汇总误当作电子迭代的问题，新增对
  `DAV`、`RMM`、`CG`、`DMP` 和 `SDA` 电子步的结构化解析，并支持
  多离子步、截断输出、科学计数法和数值溢出的安全处理。
- 将电子步与离子步的诊断证据分离：`SCF_REACHED_NELM` 使用最后一个
  真实电子块，`IONIC_REACHED_NSW` 使用离子步汇总，并避免 `NSW=0`
  静态计算误报。
- 修正 SCF 收敛曲线的数据来源，不再将 `F/E0` 离子步能量伪装成电子步；
  缺少真实电子迭代时返回空曲线并提示证据不足。
- 增加 OUTCAR 结构优化收敛文本的精确识别，避免仅凭宽泛短语判断几何
  优化已经收敛。

### Known limitations

- MVP 暂不支持无序或部分占据结构，也不支持一个 CIF 中包含多个结构。
- 如果 CIF 只写空间群名称但没有提供对称操作，pymatgen 可能按 P1 读取，
  系统不会凭空补造对称等价原子。

## [0.1.0] - 2026-08-21

### Added

- 导入 VASP-Copilot v0.1 一体化源码，包括工作流生成、VASP 运行诊断、
  规则化修复建议、报告导出和本地/Fake HPC 演示链路。
- 建立 Recipe Pack 驱动的 INCAR、KPOINTS、POSCAR 与提交脚本生成流程。
- 建立 FastAPI 后端、React 前端、自动化测试、Docker 配置和演示用例。

[Unreleased]: https://github.com/USTC-Major/vasp-copilot/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/USTC-Major/vasp-copilot/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.1.0
