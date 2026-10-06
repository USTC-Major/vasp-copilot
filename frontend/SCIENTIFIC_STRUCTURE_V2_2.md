# V2-2 真实只读结构查看候选

基线 `bd2e45e25725ba3d6d17ef916296d8d8f510fcb0`，分支 `feat/scientific-structure-view`。本轮只在独立候选目录实施；未提交、推送、创建 PR 或发布。原六阶段 Workflow Builder、DAG、参数确认、生成和下载语义保持。

## 单一坐标源与接口

新增 `GET /api/v1/structure/{structure_id}/geometry`，使用现有统一响应封装 `{request_id,data}`。

接口通过 FileStore 的结构记录读取 `StructureSummary.poscar_text`，与生成使用同一保存的 POSCAR 输入。POSCAR 上传保留原文本；CIF 使用已有转换器的规范化 POSCAR；MP 使用已有导入程序构造的 POSCAR。查看不重新解析 CIF/MP、不重新标准化晶胞，不读取用户磁盘文件或其他外部服务。

| 字段 | 含义 |
| --- | --- |
| `structure_id`, `formula`, `atom_count` | 保存的结构身份、化学式、实际原子数 |
| `basis_cartesian_angstrom` | 按行排列的实际 a/b/c 物理基矢，单位 Å，缩放已经应用 |
| `lattice` | 从同一基矢计算的长度 Å、角度 °、体积 Å³、矩阵 |
| `sites[].id` | 保存的 POSCAR 原子顺序加一；投影深度排序不改变编号 |
| `sites[].element` | 同一 POSCAR 物种行和计数确定的元素 |
| `sites[].fractional` | 当前晶胞基底中的原始分数坐标；不取模、不强制限制到 `[0,1)` |
| `sites[].cartesian_angstrom` | 未旋转的实际笛卡尔坐标 Å；不是屏幕坐标 |
| `sites[].selective_flags` | 可选 T/F 数组；三个标志始终对应直接晶格 a/b/c 方向 |
| `coordinate_mode`, `selective_dynamics`, `selective_flags_basis` | 输入模式、是否带 flags、flags 的直接晶格基底声明 |
| `source.format/file_name/material_id` | POSCAR/CIF/Materials Project 来源元数据 |
| `source.coordinate_source` | 明确为 `StructureSummary.poscar_text` |
| `source.poscar_sha256` | 生成使用的保存 POSCAR 输入文本 UTF-8 字节 SHA-256；CIF 情况为转换后 POSCAR 哈希，**不是原始 CIF 哈希，也不冒称下载 POSCAR 注释修改后的整文件哈希** |
| `geometry_sha256` | 服务端对 `{basis_cartesian_angstrom,sites}` 确定性 JSON 字节计算的 SHA-256；不是科学正确性认证 |

`validate_poscar(..., include_coordinates=True)` 是唯一 POSCAR 语法/数值校验入口。最小扩展 `PoscarInfo` 暴露已验证坐标、有效 XYZ 缩放、flags；默认调用不收集坐标。Direct 使用分数坐标线性组合；Cartesian 先按有效 XYZ 因子缩放位置，再通过同一基矢求分数坐标。负单值按目标体积确定正缩放因子；三数分别作用笛卡尔 XYZ 分量。BOM、D/d 指数、非正交及左手基底继续沿原验证路径。格式依据：[VASP Wiki — POSCAR](https://vasp.at/wiki/index.php/POSCAR)。

## 查看边界与错误

查看专属上限为 **2048 原子 / 1 MiB JSON 响应**；为统一响应封装预留 1024 字节。基矢、分数及笛卡尔坐标分量须有限且绝对值不超过 `1e6`，基矢长度至少 `1e-8 Å`。浮点缩放后零/非有限行列式明确拒绝；这些限制只控制查看，**不下调现有上传、摘要和生成能力**。

| HTTP / 错误码 | 含义 |
| --- | --- |
| 404 `STRUCTURE_NOT_FOUND` | 结构不存在或已过期；重新上传或导入 |
| 422 `STRUCTURE_VIEW_INVALID` | 保存的 POSCAR 无效或物种/数量与记录不一致 |
| 422 `STRUCTURE_VIEW_ATOM_LIMIT` | 查看原子数超限 |
| 422 `STRUCTURE_VIEW_NUMBER_LIMIT` | 超出查看数值范围或无法可靠进行物理坐标转换 |
| 422 `STRUCTURE_VIEW_SIZE_LIMIT` | 查看响应超限 |

结构查询按 `['structureGeometry',structure_id]` 隔离，失败不使用示例或上一个结构替代。服务的错误及前端相机数值异常均局限在查看区域，保留实际摘要和工作流操作。原 FileStore TTL/结构 ID 生命周期不变；查看沿用 `get_structure` 的正常访问续期与索引行为。

## 视图与只读交互

- 固定 120×120 缩略图：标准晶形取向、真实比例、a/b/c 从同一晶胞角出发的真实棱，红/绿/蓝短标签。缩略图无旋转监听，不跟随详情视角。
- 详情：宽屏图形与信息侧栏，窄屏堆叠；鼠标/触摸 pointer capture 拖动，方向键旋转；沿 +a/+b/+c 端看向原点，正对轴以 ⊙ 表示朝向观察者；沿轴投影可能重叠。
- 正交投影与共享旋转矩阵同时作用原子、晶胞、直接晶格轴；不把非正交晶格画成三条正交轴。实际超胞外原子坐标纳入适应范围，不静默 wrap。
- 缩放 60–250%、适应、Home/复位、显示晶胞、原子编号（默认关闭）、点选与键盘原子列表、分数/未旋转笛卡尔坐标/flags/来源/哈希。
- 点击使用 pointerdown 捕获的稳定编号，5 px 阈值区分拖动；拖动不改选择。装饰棱/坐标轴/轴指示器不截获指针，原子、编号和选择圈保持命中。
- 复位恢复标准视角及 100% 缩放，保留选择和显示开关；适应保留视角与选择。关闭/Escape 恢复“查看结构”按钮焦点。视图为正交投影，原子圆点仅作标记，不代表真实半径。

标准相机复用已批准样板的独立数学：从实际晶格 c 的单位方向和 b 垂直于 c 的分量建立基底，水平角 `atan(1/3)`、俯角 `atan(1/6)`，不添加人为 roll。公开参考为 [Megane issue #661](https://github.com/megane-labs/megane/issues/661) 与 [PR #694](https://github.com/megane-labs/megane/pull/694)。这是用户指定视觉约定的参考实现，**不是 VESTA 官方精确公式，未进行 VESTA 软件逐像素比较**。沿 c 的屏幕上方向继续复用原样板的实验室 y 优先规则，若近共线则使用数值安全的备用方向；视线始终沿实际 c。

## 实际修改

后端：`backend/input_validation.py`、`backend/app/api/v1/structure.py`、新增 `backend/app/services/structure_geometry.py`、`backend/tests/test_structure_geometry.py`。

前端：`src/api/client.ts`、`src/hooks/useApi.ts`、`src/pages/WorkflowBuilderPage.tsx`；新增 `src/types/structure-geometry.ts`、`src/components/structure/CrystalViewer.tsx`、`crystal-viewer.css`、`crystalMath.ts`、两份对应测试。本说明位于候选 frontend。未修改已批准样板文件、DAG、后端生成/提交规则、依赖及锁文件。

## 验证命令与证据

Python：`D:/Documents/VASP-Doctor/vasp-copilot/.venv/Scripts/python.exe`。从候选根目录执行 `backend/.tmp/v2-2/run_checks.py`。该 wrapper 在导入前明确设置：

```
DATA_DIR=D:/Documents/VASP-Doctor/.tmp/scientific-structure-v2/backend/.tmp/v2-2/test-data
VASP_AI_HOME=D:/Documents/VASP-Doctor/.tmp/scientific-structure-v2/backend/.tmp/v2-2/test-home
ENABLE_LLM=false
ENABLE_MATERIALS_PROJECT=false
ENABLE_HPC_BRIDGE=false
ENABLE_FAKE_HPC=false
ENABLE_POTCAR_ASSEMBLY=false
PYTHONIOENCODING=utf-8
```

wrapper 清空外部凭据环境变量；不加载 .env。原测试 fixture 在该目录的 `--basetemp` 下进一步隔离 runtime home/内存凭据和内部 HTTP transport。真实 MP 导入合同使用受控 FakeMpClient 和内存假凭据，未访问 MP 实网。

```
python -m pytest backend/tests/test_structure_geometry.py backend/tests/test_input_validation_unit.py backend/tests/test_structure_api.py backend/tests/test_cif_to_poscar.py backend/tests/test_materials_api.py -q --basetemp=<候选>/backend/.tmp/v2-2/pytest-geometry.log-temp
python -m pytest backend/tests/test_workflow_api.py -q --basetemp=<候选>/backend/.tmp/v2-2/pytest-workflow.log-temp
```

结果 **63 + 24 = 87 passed**，其中新增几何 **18 项**。日志：`backend/.tmp/v2-2/pytest-geometry.log`、`pytest-workflow.log`。首轮测试 upload 封装读取/lifespan 假凭据 fixture 错误及 Windows 日志编码问题已修正并保留 `pytest-geometry-first.log`；定向下溢复现日志 `pytest-boundary-first.log` 保留，对应明确 422 已通过最终回归。

从候选 frontend 执行：

```
node node_modules/vitest/vitest.mjs run --config .tmp/structure-check.config.ts --configLoader runner src/components/structure/crystalMath.test.ts src/components/structure/CrystalViewer.test.tsx
node node_modules/vitest/vitest.mjs run --config .tmp/structure-check.config.ts --configLoader runner src/pages/WorkflowBuilderPage.test.tsx src/components/workflow/ScientificWorkflowShell.test.tsx src/components/workflow/WorkflowPlanPreview.test.tsx src/components/workflow/WorkflowPlanPreview.real.test.tsx src/hooks/useApi.filePreview.test.tsx
node node_modules/typescript/bin/tsc -p tsconfig.app.json --tsBuildInfoFile .tmp/structure-app.tsbuildinfo
node node_modules/typescript/bin/tsc -p tsconfig.node.json --tsBuildInfoFile .tmp/structure-node.tsbuildinfo
node node_modules/vite/bin/vite.js build --config .tmp/structure-check.config.ts --configLoader runner
node node_modules/oxlint/bin/oxlint
```

结果 **9 新数学/交互 + 25 原流程/主题/DAG/预览 = 34 passed**；app/node TypeScript、build、lint、`git diff --check` 通过。数学覆盖正交、长度/角度/手性保持、标准相机整体旋转协变、沿实际直接轴、沿 c 的六方 120°、1000 次旋转、外坐标及窄屏 fit。交互覆盖稳定编号、拖动不选中、缩略图独立、复位、缩放/显示开关、Escape/焦点、结构 ID 隔离、重试以及合法极端晶格相机异常的局部容错。

日志在 `frontend/.tmp/structure-tests.log`、`structure-workflow-regression.log`、`structure-tsc-app.log`、`structure-tsc-node.log`、`structure-build.log`、`structure-lint.log`。保留首轮新测试参数化/role 查询问题日志 `structure-tests-first.log`。剩余警告为既有 10 项 FastRefresh、构建大分块和 jsdom pseudo-element API 缺失；无新增 lint 错误。

node_modules 仅以候选 junction 复用既有依赖；Vite cache、TypeScript build-info 和构建产物全部在候选 `frontend/.tmp`。本实施者未启动服务器。父级独立 HTTP/浏览器/审查证据由父级管理在 `backend/.tmp/v2-2-acceptance`。

2026-10-07 最终冻结代码的父级验收回执：

- 重启后真实 HTTP **34 请求 / 71 断言零失败**，证据 `backend/.tmp/v2-2-acceptance/run-20261006T160733Z-9ea7eb/result.json`。验证正/负/三缩放因子、Direct/Cartesian、非正交及晶胞外坐标、Selective、CIF 来源哈希、查看后生成与 ZIP 坐标/flags、超限仍保留计划/生成能力。
- 极端三缩放下溢 **3 请求 / 4 断言通过**，明确返回 422 `STRUCTURE_VIEW_NUMBER_LIMIT`，证据 `backend/.tmp/v2-2-acceptance/run-20261006T160736Z-54d2ec/result.json`。
- 最终增量独立审查通过。真实浏览器使用公开 CIF 实际解析的 30 原子：原子 1 实际鼠标点击、拖动不误选、坐标不随视角变、小图固定、键盘旋转/复位/沿轴/缩放/编号、Escape/焦点恢复、深浅色与 375 px 窄屏均通过。近共线晶格局部回退、2049 原子拒绝后重新上传恢复、后续生成文件成功。
- 最终暗色详情截图 `backend/.tmp/v2-2-acceptance/detail-final-dark.png`；同目录另存 `thumbnail-dark.png`、`detail-dark.png`、`detail-light-narrow.png`、`skew-outside-light.png`、`near-parallel-recovery.png`。没有真实 MP/LLM/SSH/HPC 或 VASP 调用。

## 局限

仅查看实际保存的一个晶胞和其中原子，不推断化学键、不测距、不扩胞、不生成表面、不编辑结构；不是完整 VESTA 替代。颜色为稳定展示配色，少量元素可能同色，文字图例/列表区分；圆点重叠时可用列表选原子。极端晶格即便原生成验证接受，也可能仅在查看区域被拒绝可靠投影。没有运行 VASP 或验证科学参数正确性；没有真实 MP/LLM/SSH/HPC 调用。

## Git 交付与 CI（2026-10-07）

交付基线 main `bd2e45e25725ba3d6d17ef916296d8d8f510fcb0`，分支 `feat/scientific-structure-view`。既有 Ubuntu `remote-file-posix` 增加 `test_structure_geometry.py`、`test_structure_api.py`、`test_cif_to_poscar.py`，并列入 JUnit 必需模块检查，遗漏、跳过或失败均阻断。输入验证、材料导入和工作流回归已在既有清单中；frontend 执行全量测试/构建/lint，Docker 沿用 smoke。未扩大部署或科学功能范围。

前述 `.tmp` 路径为本机留存证据，不随 Git 提交。CI 使用仓库测试与公开工作流命令，远端最终结果见关联 PR 检查；本段不预先声称 CI 成功。
