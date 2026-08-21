# README — VASP 工作流运行顺序

- workflow_id: `wf_nacl`
- revision: 1
- 结构: NaCl（Na / Cl，2 atoms）

> 本目录树为**生成产物**，不代表可直接提交：缺少 POTCAR 与上游运行时输出时必须先补齐。

## 运行顺序


### 1. `01_relax/` — relax
- step_id: `01_relax`
- runnable: **false**（blocked_by: POTCAR_NOT_PREPARED）
- produces: CONTCAR, OUTCAR, OSZICAR
- 运行: 进入目录后按 `submit.sh` 内的提示手动提交（生成阶段不执行任何命令）

### 2. `02_static/` — static
- step_id: `02_static`
- runnable: **false**（blocked_by: POTCAR_NOT_PREPARED, UPSTREAM_OUTPUT_MISSING, UPSTREAM_DIAGNOSIS_NOT_PASSED）
- depends_on: 01_relax
- produces: CHGCAR, WAVECAR, OUTCAR, OSZICAR
- 运行: 进入目录后按 `submit.sh` 内的提示手动提交（生成阶段不执行任何命令）


## 文件继承（FileInheritancePlan）

| 上游 step | 源文件 | 下游 step | 目标文件 | 需上游诊断通过 |
|---|---|---|---|---|
| `01_relax` | CONTCAR | `02_static` | POSCAR | True |


## POTCAR

**尚未准备 POTCAR**：见 `POTCAR_REQUIRED.md`，所有 step 在补齐前均不可运行。

## 警告与待确认

- [medium] INITIAL_RECOMMENDATION_ONLY: 这是初始推荐，不是唯一正确设置。
- [medium] QUICK_PRECISION_NOT_FOR_PUBLICATION: quick 档位仅用于快速试算，不建议用于最终结果。
- [info] STATIC_TIGHTER_EDIFF_HINT: static 建议比 relax 更严的 EDIFF；如需 1E-6 请选择 high 精度档或提交 EDIFF patch。
