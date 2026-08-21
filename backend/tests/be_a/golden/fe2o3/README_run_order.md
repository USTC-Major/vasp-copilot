# README — VASP 工作流运行顺序

- workflow_id: `wf_fe2o3`
- revision: 1
- 结构: Fe2O3（Fe / O，5 atoms）

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

### 3. `03_dos/` — dos
- step_id: `03_dos`
- runnable: **false**（blocked_by: POTCAR_NOT_PREPARED, UPSTREAM_OUTPUT_MISSING, UPSTREAM_DIAGNOSIS_NOT_PASSED）
- depends_on: 02_static
- produces: DOSCAR, OUTCAR
- 运行: 进入目录后按 `submit.sh` 内的提示手动提交（生成阶段不执行任何命令）


## 文件继承（FileInheritancePlan）

| 上游 step | 源文件 | 下游 step | 目标文件 | 需上游诊断通过 |
|---|---|---|---|---|
| `01_relax` | CONTCAR | `02_static` | POSCAR | True |
| `02_static` | CHGCAR | `03_dos` | CHGCAR | True |


## POTCAR

**尚未准备 POTCAR**：见 `POTCAR_REQUIRED.md`，所有 step 在补齐前均不可运行。

## 警告与待确认

- [medium] INITIAL_RECOMMENDATION_ONLY: 这是初始推荐，不是唯一正确设置。
- [medium] METAL_SMEARING_REQUIRES_CONFIRMATION: 金属展宽设置需用户确认，初始值仅为起点。
- [high] DFTU_USER_VALUE_REQUIRED: U/J/L 来自用户输入，不代表系统断言其可靠；请记录来源。
- [medium] MAGMOM_INITIAL_GUESS: 初始磁矩仅为起点，请确认元素级初值；不保证是基态磁结构。
- [info] STATIC_TIGHTER_EDIFF_HINT: static 建议比 relax 更严的 EDIFF；如需 1E-6 请选择 high 精度档或提交 EDIFF patch。
- [high] CHGCAR_DEPENDENCY: DOS 依赖 static 的 CHGCAR；文件尚未生成时 README 只写运行时复制说明，不伪造文件。
