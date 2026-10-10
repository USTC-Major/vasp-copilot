# CAT-05／06 验证回执

日期：2026-10-11。开发基线：main `730d8f394bebc8a52b86554da9c4c290fcf3b330`；本轮源码候选位于 `feat/cat05-workflow-handoff`，正式发布另行决定。

## 交付内容

选定的清洁表面或单个有效吸附候选通过不可变结构快照进入现有 Workflow。前端保护已有草稿与异步期间的新输入；后端保留来源、父清洁表面、原子行映射和约束。固定晶胞弛豫与二维面内采样作为明确的表面策略进入计划和导出，复用原有 Recipe 与 POTCAR 确认。

Selective Dynamics 在原直接晶格基矢下保持；POSCAR 规范化使用当前库 API，并检查晶胞、原子顺序及约束。不能可靠保留这些信息时明确拒绝。不同模型不能复用同一 Workflow 的旧计划确认，包括不含 POTCAR 的模式。

## 本轮最小闭环

复用 CAT-00 的基础库核定、CAT-01／02 的构建和保存、CAT-03／04 的吸附与候选导出、共享查看器的旋转缩放证据；不重复本地全量验证。

- 后端定向覆盖 clean／candidate handoff、来源与约束、保存重读、实际 plan/generate/replay/download、客户端元数据不可代替已保存结构、旧 revision／候选／确认拒绝、规范化约束保护、表面 ISIF 冲突及倾斜晶胞 k 点。
- POTCAR 使用项目合成夹具，覆盖既有库预览、组装确认、包含模式生成，以及新绑定不能复用旧 artifact；没有读取或分发真实赝势。
- 前端定向覆盖单模型确认、替换／取消、过期及迟到响应保护、冲突恢复、默认 relax/static、表面策略展示，以及倾斜 DOS 手工展宽的显式确认。
- 前端生产构建通过；修改文件使用 `oxlint --no-ignore` 明确发现文件后检查。原有 chunk 体积和 Fast Refresh 提示单独记录，不算失败。

后端通过增量定向运行闭环 12 个 CAT 用例及 1 个既有 Si2 回归；用例位于 `backend/tests/test_catalysis_workflow.py`，既有回归为 `test_si2_static_standard_scf_generates_gamma_9_with_same_structure_and_scheduler`。使用发布锁对应的 Python 3.12 科学依赖环境，通过隔离 runner 指定 pytest 用例。只重跑失败项或新加入的确认守卫，没有声称一次完整全绿运行。

前端相关验证累计覆盖 76 个通过用例（首轮 74 通过及 1 个新断言失败，修正断言后定向补跑，并补冲突恢复）；生产 `npm run build` 通过。修改文件 `oxlint --no-ignore` 检查 20 个文件，0 错误，2 条原有 `only-export-components` 提示。详细命令、文件哈希与每项有效日志索引在实施机器 `.tmp/cat05-evidence/backend/summary.json` 与 `frontend/evidence.json`。来源卡折叠后，仅重跑相关渲染用例 1 项、生产构建和该组件静态检查，均通过；没有重复运行整个测试集。

## 实际浏览器与下载包

使用真实无头 Microsoft Edge，连接独立本地前后端；外网请求阻断。通过页面完成中文草稿名的 Pt(111)＋CO 单候选接入、参数确认、计划、生成和 ZIP 下载。采用合成理想结构，固定底部两层，得到 98 原子模型。

实际 ZIP 的 manifest 文件哈希逐项匹配。`01_relax/POSCAR` 与 `02_static/POSCAR` 的晶胞、坐标、原子身份映射和 T/F 与选定快照一致；坐标核对最大误差约 $5.56\times10^{-17}$。两步 `KPOINTS` 为 Gamma $4\times4\times1$，弛豫 `ISIF=2`。`catalysis_metadata.json` 保留父清洁表面与候选来源；不含 POTCAR 的界面说明、manifest 状态、缺失文件和 `POTCAR_REQUIRED.md` 一致。

下载 ZIP SHA-256：`517496996d14edbf5b20ad57930fa0fb264e2d27d7d46af8f743b134be09e58f`。浏览器没有脚本错误或外网请求。证据保存在实施机器 `.tmp/cat05-evidence/review/browser-run1/`；它是独立验收产物，不随源码分发。

浏览器正向闭环后补充的 ISIF 修订检查与旧模型确认拒绝，仅加强失败分支，已由定向用例覆盖；后端随后优雅重启加载最终实现，原 CAT 草稿仍可读取。信息卡折叠属于展示调整，独立核对截图，不重复科学闭环。

## 已知边界

- 倾斜 c 使用显式纯面内网格，但不生成四面体连接表；相关 ISMEAR 四面体组合明确拒绝。表面模型不使用体相能带路径。
- 默认参数和几何位点不代表结构稳定性、表面极性、磁性或收敛已验证。没有调用真实模型、MP、SSH、HPC 或 VASP。
- 当前仅传入一个模型；不自动建立整套吸附能计算任务，不自动提交或承诺跨重启恢复整个 Workflow 编辑会话。CAT 草稿与绑定结构的持久化和 Workflow 会话状态应区分。
- 真实桌面、真实科学计算及用户环境复测不在这次合成浏览器闭环内。

PR、必需 CI 和合并状态以关联 PR 的最终提交及 GitHub 记录为准；本文件记录源码验证，不宣称已发布安装包。此次核对的 main 必需检查为 `frontend`、`remote-file-posix` 和 `docker-smoke`；Windows 工作流另有运行记录，不将非必需检查说成分支保护要求。

首次 CI 指出旧 `test_structure_geometry` 的损坏夹具仍依赖调用方与已存结构共享引用。新 `FileStore` 保存独立副本后，修改调用方不会损坏已存结构；产品调用并不依赖这种引用修改。保留独立副本，调整该测试先证明调用方修改被隔离，再在受控存储对象中制造损坏，继续核对错误与恢复。此次修订不改变产品代码或既有浏览器证据。
