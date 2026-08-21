# POTCAR_REQUIRED — 需要用户准备 POTCAR

- workflow_id: `wf_nacl`
- 结构: NaCl

本项目**不下载、不分发、不拼接** POTCAR（`ENABLE_POTCAR_ASSEMBLY=false`，VASP 许可证限制）。
在下列 POTCAR 准备完成之前，所有 step 均保持 `runnable=false`（blocked_by: `POTCAR_NOT_PREPARED`）。

## 需要的 POTCAR（按 POSCAR 元素顺序拼接）

| 顺序 | 元素 | 建议 POTCAR 符号 |
|---|---|---|
| 1 | Na | `Na` |
| 2 | Cl | `Cl` |


## 操作步骤

1. 从你的 VASP 授权目录中按上表顺序找到对应 POTCAR；
2. 按 POSCAR 元素顺序拼接为每个 step 目录下的 `POTCAR`（顺序必须与 POSCAR 一致）；
3. 校验拼接后 POTCAR 的元素顺序与数量（TITEL 行数 == 元素种类数）；
4. 完成后在平台中重新运行输入检查，解除 `POTCAR_NOT_PREPARED` 阻塞。

> 注意：赝势版本（如 `_pv`、`_sv`）属于用户决策，请结合 ENMAX 与 ENCUT 确认。
