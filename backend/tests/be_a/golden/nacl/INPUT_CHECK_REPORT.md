# INPUT_CHECK_REPORT — wf_nacl (revision 1)

> 生成时刻（固定基准）：2026-08-10T00:00:00Z；本报告为生成时自检证据。

## 1. 结构与顺序

- 化学式：NaCl
- POSCAR 元素顺序：Na × 1, Cl × 1
- 总原子数：2
- source_sha256：1111111111111111111111111111111111111111111111111111111111111111

## 2. 数组展开

- MAGMOM：未启用（非磁性组合）。

- LDAU：未启用。

## 3. 任务步骤

| step | 任务 | composition revision | runnable | blocked_by | 静态校验 |
|---|---|---|---|---|---|
| `01_relax` | relax | 1 | false | POTCAR_NOT_PREPARED | passed |
| `02_static` | static | 1 | false | POTCAR_NOT_PREPARED, UPSTREAM_OUTPUT_MISSING, UPSTREAM_DIAGNOSIS_NOT_PASSED | passed |

## 4. 文件继承

| 上游步骤 | 源文件 | 下游步骤 | 目标文件 | 当前满足 | 提交前检查 |
|---|---|---|---|---|---|
| `01_relax` | CONTCAR | `02_static` | POSCAR | false | 源文件实际产生、非空、格式/哈希通过且上游诊断无 blocking |

## 5. POTCAR 准备

- 系统不内置、不下载、不分发 POTCAR（VASP 许可证限制，`ENABLE_POTCAR_ASSEMBLY=false`）。
- 请在合法授权环境按下列 POSCAR 元素顺序拼接 POTCAR：
  1. Na
  2. Cl
- 未完成前所有 step 保持 `POTCAR_NOT_PREPARED` 阻塞。

## 6. 参数来源

### `01_relax`

| parameter | value | 标识 | source ID | version |
|---|---|---|---|---|
| ALGO | `Normal` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| EDIFF | `0.0001` | Recipe 初始推荐 | precision.quick | 1.0.0 |
| EDIFFG | `-0.02` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ENCUT | `400` | 确定性派生/规则修复 | derive_encut_from_precision | - |
| IBRION | `2` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ISIF | `3` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| ISMEAR | `0` | Recipe 初始推荐 | electronic.semiconductor | 1.0.0 |
| LCHARG | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LREAL | `Auto` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LWAVE | `.FALSE.` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| NELM | `60` | Recipe 初始推荐 | precision.quick | 1.0.0 |
| NSW | `100` | Recipe 初始推荐 | task.relax.standard | 1.0.0 |
| PREC | `Accurate` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| SIGMA | `0.05` | Recipe 初始推荐 | electronic.semiconductor | 1.0.0 |
| SYSTEM | `NaCl_relax` | 确定性派生/规则修复 | derive_system_label | - |

### `02_static`

| parameter | value | 标识 | source ID | version |
|---|---|---|---|---|
| EDIFF | `0.0001` | Recipe 初始推荐 | precision.quick | 1.0.0 |
| ENCUT | `400` | 确定性派生/规则修复 | derive_encut_from_precision | - |
| IBRION | `-1` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| ISIF | `2` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| ISMEAR | `0` | Recipe 初始推荐 | electronic.semiconductor | 1.0.0 |
| LCHARG | `.TRUE.` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| LREAL | `Auto` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| LWAVE | `.TRUE.` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| NELM | `60` | Recipe 初始推荐 | precision.quick | 1.0.0 |
| NSW | `0` | Recipe 初始推荐 | task.static.standard | 1.0.0 |
| PREC | `Accurate` | Recipe 初始推荐 | base.vasp | 1.0.0 |
| SIGMA | `0.05` | Recipe 初始推荐 | electronic.semiconductor | 1.0.0 |
| SYSTEM | `NaCl_static` | 确定性派生/规则修复 | derive_system_label | - |

> 说明：`Recipe 初始推荐` 只是受审查起点，不是唯一正确值。

## 7. warning 与门控结论

- warning 统计：high 0 / medium 6 / info 1
- 当前不可提交步骤：`01_relax`, `02_static`
- 待补文件：POTCAR；上游运行时产物（CONTCAR/CHGCAR）需实际计算后产生。
- 待确认字段：无。
- warning 明细：
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] QUICK_PRECISION_NOT_FOR_PUBLICATION：quick 档位仅用于快速试算，不建议用于最终结果。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [medium] INITIAL_RECOMMENDATION_ONLY：这是初始推荐，不是唯一正确设置。
  - [info] STATIC_TIGHTER_EDIFF_HINT：static 建议比 relax 更严的 EDIFF；如需 1E-6 请选择 high 精度档或提交 EDIFF patch。
  - [medium] QUICK_PRECISION_NOT_FOR_PUBLICATION：quick 档位仅用于快速试算，不建议用于最终结果。
