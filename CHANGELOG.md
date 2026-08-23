# 更新日志

本文件记录 VASP-Copilot 面向用户的重要变化。开发中的变更先写入
`[Unreleased]`；正式发布时再归入对应版本。具体代码级修改可查阅 Git
提交历史。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.1.0
