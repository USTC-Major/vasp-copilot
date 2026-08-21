# 更新日志

本文件记录 VASP-Copilot 面向用户的重要变化。开发中的变更先写入
`[Unreleased]`；正式发布时再归入对应版本。具体代码级修改可查阅 Git
提交历史。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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

[Unreleased]: https://github.com/USTC-Major/vasp-copilot/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/USTC-Major/vasp-copilot/releases/tag/v0.1.0
