# INPUT_CHECK_REPORT — wf_fe2o3 (revision 1)

> 生成时刻（固定基准）：2026-08-10T00:00:00Z；本报告为生成时自检证据。

## 1. 结构与顺序

- 化学式：Fe2O3
- POSCAR 元素顺序：Fe × 2, O × 3
- 总原子数：5
- source_sha256：0000000000000000000000000000000000000000000000000000000000000000

## 2. 数组展开

### MAGMOM

- compact：`2*5 3*0.6`
- raw：`5 5 0.6 0.6 0.6`
- 展开后总数：5（原子数 5）
- Fe：原子 1–2，磁矩 [5.0, 5.0]
- O：原子 3–5，磁矩 [0.6, 0.6, 0.6]

### LDAUL / LDAUU / LDAUJ（按 POSCAR 元素顺序）

| 元素 | L | U (eV) | J (eV) | 状态 |
|---|---|---|---|---|
| Fe | 2 | 4 | 0 | 用户确认 |
| O | -1 | 0 | 0 | 不使用 U（L=-1） |

## 3. 任务步骤

| step | 任务 | composition revision | runnable | blocked_by | 静态校验 |
|---|---|---|---|---|---|
| `01_relax` | relax | 1 | false | POTCAR_NOT_PREPARED | passed |
| `02_static` | static | 1 | false | POTCAR_NOT_PREPARED, UPSTREAM_OUTPUT_MISSING, UPSTREAM_DIAGNOSIS_NOT_PASSED | passed |
| `03_dos` | dos | 1 | false | POTCAR_NOT_PREPARED, UPSTREAM_OUTPUT_MISSING, UPSTREAM_DIAGNOSIS_NOT_PASSED | passed |

## 4. 文件继承

| 上游步骤 | 源文件 | 下游步骤 | 目标文件 | 当前满足 | 提交前检查 |
|---|---|---|---|---|---|
| `01_relax` | CONTCAR | `02_static` | POSCAR | false | 源文件实际产生、非空、格式/哈希通过且上游诊断无 blocking |
| `02_static` | CHGCAR | `03_dos` | CHGCAR | false | 源文件实际产生、非空、格式/哈希通过且上游诊断无 blocking |

## 5. POTCAR 准备

- 系统不内置、不下载、不分发 POTCAR（VASP 许可证限制，`ENABLE_POTCAR_ASSEMBLY=false`）。
- 请在合法授权环境按下列 POSCAR 元素顺序拼接 POTCAR：
  1. Fe
  2. O
- 未完成前所有 step 保持 `POTCAR_NOT_PREPARED` 阻塞。

## 6. 参数来源

### `01_relax`

| parameter | value | 标识 | source ID | version |
|---|---|---|---|---|
| ALGO | `Normal` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| EDIFF | `1e-05` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| EDIFFG | `-0.02` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ENCUT | `520` | 确定性派生/规则修复 | derive_encut_from_precision | - |
| IBRION | `2` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ISIF | `3` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ISMEAR | `1` | Recipe 初始推荐 | electronic.metal | 1.0.0 |
| ISPIN | `2` | Recipe 初始推荐 | modifier.magnetic | 1.0.0 |
| LCHARG | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LDAU | `.TRUE.` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUJ | `0 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUL | `2 -1` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUTYPE | `2` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUU | `4 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LMAXMIX | `4` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LREAL | `Auto` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LWAVE | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| MAGMOM | `5 5 0.6 0.6 0.6` | 确定性派生/规则修复 | generate_magmom_from_structure | - |
| NELM | `100` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| NSW | `100` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| PREC | `Accurate` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| SIGMA | `0.2` | Recipe 初始推荐 | electronic.metal | 1.0.0 |
| SYSTEM | `Fe2O3_relax` | 确定性派生/规则修复 | derive_system_label | - |

### `02_static`

| parameter | value | 标识 | source ID | version |
|---|---|---|---|---|
| EDIFF | `1e-05` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| ENCUT | `520` | 确定性派生/规则修复 | derive_encut_from_precision | - |
| IBRION | `-1` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| ISIF | `2` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| ISMEAR | `1` | Recipe 初始推荐 | electronic.metal | 1.0.0 |
| ISPIN | `2` | Recipe 初始推荐 | modifier.magnetic | 1.0.0 |
| LCHARG | `.TRUE.` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| LDAU | `.TRUE.` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUJ | `0 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUL | `2 -1` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUTYPE | `2` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUU | `4 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LMAXMIX | `4` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LREAL | `Auto` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LWAVE | `.TRUE.` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| MAGMOM | `5 5 0.6 0.6 0.6` | 确定性派生/规则修复 | generate_magmom_from_structure | - |
| NELM | `100` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| NSW | `0` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| PREC | `Accurate` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| SIGMA | `0.2` | Recipe 初始推荐 | electronic.metal | 1.0.0 |
| SYSTEM | `Fe2O3_static` | 确定性派生/规则修复 | derive_system_label | - |

### `03_dos`

| parameter | value | 标识 | source ID | version |
|---|---|---|---|---|
| EDIFF | `1e-05` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| EMAX | `10` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| EMIN | `-10` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| ENCUT | `520` | 确定性派生/规则修复 | derive_encut_from_precision | - |
| IBRION | `-1` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| ICHARG | `11` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| ISMEAR | `-5` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| ISPIN | `2` | Recipe 初始推荐 | modifier.magnetic | 1.0.0 |
| LCHARG | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LDAU | `.TRUE.` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUJ | `0 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUL | `2 -1` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LDAUTYPE | `2` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LDAUU | `4 0` | 确定性派生/规则修复 | generate_ldau_arrays | - |
| LMAXMIX | `4` | Recipe 初始推荐 | modifier.dftu | 1.0.0 |
| LORBIT | `11` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| LREAL | `Auto` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LWAVE | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| MAGMOM | `5 5 0.6 0.6 0.6` | 确定性派生/规则修复 | generate_magmom_from_structure | - |
| NEDOS | `2000` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| NELM | `100` | Recipe 初始推荐 | precision.standard | 1.0.0 |
| NSW | `0` | Recipe 初始推荐 | task.dos.standard | 1.0.0 |
| PREC | `Accurate` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| SYSTEM | `Fe2O3_dos` | 确定性派生/规则修复 | derive_system_label | - |

> 说明：`Recipe 初始推荐` 只是受审查起点，不是唯一正确值。

## 7. warning 与门控结论

- warning 统计：high 4 / medium 11 / info 1
- 当前不可提交步骤：`01_relax`, `02_static`, `03_dos`
- 待补文件：POTCAR；上游运行时产物（CONTCAR/CHGCAR）需实际计算后产生。
- 待确认字段：无。
- warning 明细：
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] METAL_SMEARING_REQUIRES_CONFIRMATION：金属展宽设置需用户确认，初始值仅为起点。
  - [high] DFTU_USER_VALUE_REQUIRED：U/J/L 来自用户输入，不代表系统断言其可靠；请记录来源。
  - [medium] MAGMOM_INITIAL_GUESS：初始磁矩仅为起点，请确认元素级初值；不保证是基态磁结构。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [info] STATIC_TIGHTER_EDIFF_HINT：static 建议比 relax 更严的 EDIFF；如需 1E-6 请选择 high 精度档或提交 EDIFF patch。
  - [medium] METAL_SMEARING_REQUIRES_CONFIRMATION：金属展宽设置需用户确认，初始值仅为起点。
  - [high] DFTU_USER_VALUE_REQUIRED：U/J/L 来自用户输入，不代表系统断言其可靠；请记录来源。
  - [medium] MAGMOM_INITIAL_GUESS：初始磁矩仅为起点，请确认元素级初值；不保证是基态磁结构。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [high] CHGCAR_DEPENDENCY：DOS 依赖 static 的 CHGCAR；文件尚未生成时 README 只写运行时复制说明，不伪造文件。
  - [high] DFTU_USER_VALUE_REQUIRED：U/J/L 来自用户输入，不代表系统断言其可靠；请记录来源。
  - [medium] MAGMOM_INITIAL_GUESS：初始磁矩仅为起点，请确认元素级初值；不保证是基态磁结构。
