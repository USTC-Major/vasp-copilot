# A 批任务分工与队友 B 提示词

这是 Git 协作版任务说明。先读[协作入口](README.md)和[接口合同](接口合同.md)。主责已确定；各任务仍须按入口列出的依赖接续，拉取源码不代表批准真实计算或发布。用户内部规划文档不需要额外下载。

## 1. 职责与唯一主责

- **总负责人（用户）：** 总体计划、必要接口协调、分发前预修复、Toolbox 主体修复、科学正确性及最终集成验收。当前分配中的科学数据链不为减轻工作量而转给仅负责简单界面的队友。
- **队友 A：** 智能模式相关修复，承接本批其余跨页交互和产品说明。不是所有未列明工作的兜底负责人；新需求先向总负责人报告。
- **队友 B：** 三项限定前端工作：MP 编号展示、设置说明、Recipe 分类配色及详情折叠。接口、科学含义和操作权限不自行修改。贡献应对应真实可验收的修复，不为名单制造空提交或虚假署名。

| 原问题 | 唯一主责 | 本次任务号 | 分配内容与接口 |
|---|---|---|---|
| N01 能带 | 用户 | U-1 | 修复能力接通，核高对称路径、晶胞、CHGCAR和参数；包含所需表单/接口/生成器修改 |
| N02 DFT+U | 用户 | U-2 | 两种形式的完整参数流、新旧兼容；与U-1共享代码串行集成 |
| N03 DAG | 队友A | A-2 | 显示/选择/拖动边界对齐并移除MiniMap；不修改依赖算法 |
| N04 Recipe | 队友B | B-3 | 分类分组、固定颜色、折叠组合/覆盖详情；不改组合算法 |
| N05 生成者标记 | 用户 | U-3 | KPOINTS和三套submit模板去BE-A；不改科学值或执行命令 |
| N06 INCAR用途头 | 用户 | U-3 | 最终参数对应的确定性说明，解析值不变 |
| N07 POSCAR标题 | 用户 | U-3 | 可信编号＋可编辑样品名、来源和格式兼容；含所需前端确认字段 |
| N08 SCF双视图 | 用户 | U-4 | 后端数据到图表完整链路、阈值和缺失处理 |
| N09 磁矩 | 用户 | U-5 | MAGMOM/结构/输出映射、排列对照及前端可读性，科学审查 |
| N10 诊断LLM | 队友A | A-1 | 统一配置状态与实际解释路径；用户先核定跨模块接口和必要Toolbox预修复 |
| N11 修复建议 | 用户 | U-6 | 后端真实状态、差异/文件、页面文案和人工步骤；不实现自动应用 |
| N12 新手指引 | 队友A | A-4 | 页面提示＋现有中文指南更新；Toolbox操作事实由用户验收，截图在最终UI稳定后补 |
| N13 MP显示 | 队友B | B-1 | 任务页复用formatMaterialId；请求身份不改。其他来源字段归U-3协调 |
| N14 设置重排 | 队友A | A-3 | 分块、测试归位、已保存配置语义、数值控件及校验，供B-2接续 |
| N15 设置说明 | 队友B | B-2 | 字段帮助/单位/可访问标签及窄屏匹配；数值逻辑由A-3一次实现，B不重复改 |

N01—N15各有一个主责。N15中的数值控件实现作为A-3明确依赖交付，B负责展示验收并反馈差异；不能双方各自改保存和校验逻辑。

N16—N25（流程模板、项目目录、搜索增强、连续/并行/自动纠错、kagome）本轮不分配。尤其智能模式目录初始化属于B批，此处“队友B”与“B批”不是同一概念。

## 3. 用户与队友 A 的任务合同

### 用户：Toolbox与科学数据链

| 任务 | 必要输入与边界 | 交付与验收 |
|---|---|---|
| U-1 能带 | WB-1合同；已有管线和能力开关；先补普通入口复现 | 支持的PBE/PBE+U从页面到下载可用，路径基底/弛豫结构/CHGCAR/d-f/采样检查，关闭和失败行为明确 |
| U-2 DFT+U | U-0b；旧LDAUTYPE=2兼容；与U-1共用schema/pipeline | 两种形式的输入和最终值一致；改变形式后旧确认失效；与能带做组合回归 |
| U-3 输入说明 | 既有MP显示规则、样品名合同；N05简单文字可独立先做 | 三套脚本及两类KPOINTS说明、INCAR用途、POSCAR标题；参数/结构等价、元数据追溯、长名称边界 |
| U-4 SCF | DG-1合同，OSZICAR实际字段 | 双图、变化量/阈值来源、零值/断档/实际NELM；不只改轴标签 |
| U-5 磁矩 | DG-2合同，结构/MAGMOM/OUTCAR样例；数据合同先行 | 原子映射、缺失/重启/非共线边界、相对排列与明细；独立科学逻辑审查后验收 |
| U-6 人工修复 | DG-4合同，真实fix状态/文件 | 无空确认卡、有效差异和下载、手工步骤；下载不等于已应用 |

U-4/U-5/U-6共用诊断数据服务和结果页，串行集成；A-1需要修改同一结果页时由用户安排接线窗口。用户主责也包含这些Toolbox功能必要的前端，不将所有前端都默认交给A。

### 队友 A：智能模式与剩余交互

| 任务 | 必要输入与改动范围 | 交付与验收 |
|---|---|---|
| A-1 诊断LLM接通 | U-0b/c；现有AI配置/调用、诊断报告证据、前端状态。跨Toolbox接口变更先报用户 | 页面状态与实际请求同源，配置变更/清除/禁用/失联正确，AI缺席时报告仍可用；受控调用先验证，真实调用另定范围 |
| A-2 DAG | 当前WorkflowPlanPreview及ReactFlow样式，用户截图；不动工作流科学参数 | 外层尺寸与卡片命中区域一致、连线正确、MiniMap删除；100%及缩放、长名称、窄屏浏览器验收 |
| A-3 设置结构与行为 | AiSettingsPage及其测试；必要时对齐Toolbox设置入口。字段及凭据语义不变 | LLM/SSH/MP/作业监控分区，测试已保存配置、未保存提示及持久状态；数值输入/合法范围/空值行为确定；交B-2稳定页面 |
| A-4 新手指引 | TS-0指南合同、用户确认的真实操作路径；B-1完成后接TaskPage | 页面说明目的和下一步，既有输入与只有结构两条路径，指南复用已有文档；保存计划≠提交，受控演练通过，最终截图真实 |

A不要把N16流程模板、PI目录初始化、自动运行或修复授权放进A-4；首批解释当前可用流程即可。A-3发现凭据或保存机制缺陷，应先报告并界定必要修复，不通过整体替换设置页消除旧保护。

## 4. 队友 B 的任务与交接顺序

| 任务 | 可开始条件 | 允许修改 | 完成条件 |
|---|---|---|---|
| B-1 MP显示 | U-0a基线含现有formatter；尚未交给A-4改同一页面 | `frontend/src/pages/ToolboxTaskPage.tsx`及针对性页面测试 | 数字主显/原值可查，未知值保持，实际导入仍rawID，其他任务行为不变 |
| B-3 Recipe | 当前生成类型和配方身份与下文一致 | `frontend/src/components/recipes/RecipeCompositionPreview.tsx`及同目录测试；如需局部样式留组件内 | 按类别固定配色与分组；折叠详情保留原顺序及参数来源，空值和长内容可读；不修改生成类型或后端 |
| B-2 设置说明 | A-3交付并冻结该页的结构、数值控件/校验 | `frontend/src/pages/AiSettingsPage.tsx`的label/help/展示，及既有测试中相关断言 | 三字段帮助正确对应，单位范围可见、窄屏不串列、保存与测试逻辑不变 |

建议B按 **B-1 → B-3 → B-2** 做，每项独立报告。B-1完成后A可接A-4；B-2必须等A-3。若A-3尚未交付，B完成前两项后等待，不在旧页面提前大改。

## 5. 队友 B 可直接使用的 Codex 提示词

以下提示词供实际领取任务后使用。在自己的任务分支目录打开Codex，先粘贴通用提示，再逐项粘贴任务。不要一次粘贴三项让代理自行跨过依赖。仓库相对路径在各队友电脑通用；不要照抄统筹机器的`.tmp`路径。

### 通用提示词（每个任务先读）

```text
你正在修改 VASP-Copilot 的限定前端任务。请先读仓库适用的 AGENTS.md、总负责人提供的基线说明和本任务合同，再检查 git 状态，保留原有修改。

启动前置：只有总负责人已明确发出本任务启动指令、给定统一基线，且本任务依赖已交付时才开始修改。否则只报告缺少什么，不猜分支、不自行拉取或重建缺失能力。

允许做：限定目录内的实现、相称的离线验证和浏览器检查。请直接完成已明确的小任务，不反复询问是否继续。
禁止做：修改后端/科学参数/权限/凭据/调度/生成API类型；添加依赖或升级包；调用真实LLM、MP或SSH；修改密钥、用户数据和运行服务；自动提交、推送、合并、发布或给总记录改任务状态。不要扩展成重构整个页面。

出现以下情况停在具体阻断点并报告：基线缺少约定接口/工具、类型与合同不符、需要跨出允许文件、发现权限或科学逻辑问题、他人正在改同一文件。不要用any、删除失败测试、复制另一份实现来绕过。

验证使用项目已有工具；先跑相关测试，最终执行frontend的npm run build和npm run lint。依赖缺失时先报告现状，不能通过随意改锁文件解决。测试重点覆盖行为风险，纯文案不必额外生成大量重复测试。

交付报告：实际完成、修改文件、每项验证命令及结果、未完成/限制、需要总负责人复查的地方。附与本任务相关的差异说明和可用的页面证据；截图前不连接真实外部服务。不要声称未执行的浏览器或科学验证通过。
```

### B-1：计算任务页 MP 编号展示

```text
任务：B-1，对应N13。用户在计算任务页仍看到mp-aaaabwmb，希望与已修复的工作流页面一样主显示mp-32761。只修展示，不改变材料身份或导入行为。

允许修改：frontend/src/pages/ToolboxTaskPage.tsx；针对性页面测试（优先参考frontend/src/pages/ToolboxFlow.test.tsx，新测试可放页面同目录）。
只读参考：frontend/src/utils/materialId.ts、materialId.test.ts、components/upload/MaterialsProjectPanel.tsx及测试。不要修改已有formatter或复制编码算法。

现状：MP列表从row.material_id ?? row.materialId ?? row.id取materialId；导入按钮runTool('mp_import_poscar', {material_id: materialId, ...})使用原值。
做法：保留raw materialId，另外调用formatMaterialId用于展示。转换前后不同时，主显示数字ID，次行或可展开详情显示原始API ID，便于复制。两者相同时不重复展示。没有ID仍保留现有禁止导入行为。
所有请求、按钮参数、缓存身份和列表身份仍使用rawID，不能把展示值写回row或状态；不改公式、查询、材料选择、权限确认和工具调用流程。

验收样例：mp-aaaabwmb主显mp-32761，原值可查；mp-32761不重复；mp-hilze及不识别值保持原样，不能编造旧数字编号。输入为空不能因为格式化放开导入。
使用现有测试替身，验证可见显示和实际mp_import_poscar调用仍携带mp-aaaabwmb，不调用真实MP。复用formatter测试，不重写编码测试一套。
建议验证：在frontend目录运行 npm run test -- src/utils/materialId.test.ts src/pages/ToolboxFlow.test.tsx（以及你新增的测试文件），再build/lint。宽窄窗口检查ID不过度挤压、不遮按钮。

停止条件：formatter不存在或签名不符、需要改MP后端、接口返回缺失且无法用已有测试构造、A正在改同一页面。完成后停止，让总负责人把页面交给A-4，勿自行开始新手指南。
```

### B-3：Recipe 分类、固定颜色与详情折叠

```text
任务：B-3，对应N04。替换按序号轮换颜色的主Timeline，为每个计算步骤显示按类别分组的Recipe；实际组合和覆盖信息放在可展开详情。只改展示，不改Recipe运算。

允许修改：frontend/src/components/recipes/RecipeCompositionPreview.tsx及同目录RecipeCompositionPreview.test.tsx。
只读参考：同目录RecipeBadge.tsx；types/generated-api.ts的SelectedRecipe、RecipeComposition、ParameterProvenance、ParameterPatch；backend/app/recipes/packs/vasp_mvp_core/modifiers/dftu.yaml（其recipe_id为modifier.dftu）。不改RecipeBadge公共接口、生成类型或后端；若必须改先报告。

当前契约：composition含step_id、composition_id、revision、recipe_pack、selected、可选resolved_parameters/provenance/patches。selected条目含layer、recipe_id或recipe_ref、version、order、selection_reason、可选matched_context。空compositions仍显示原空状态。

固定分类与色彩（主视图显示顺序也按此表）：
1. 基础与任务：layer为base或task，中性灰；每条仍显示基础/任务身份。
2. 精度：layer=precision，蓝色。
3. 电子类型：layer=electronic_type，绿色。
4. DFT+U：layer=modifier且标准recipe身份恰为modifier.dftu，紫色。
5. 其他修饰：其余layer=modifier，橙色。
6. 用户覆盖：layer=user_patch或composition.patches中实际存在的补丁，青色。
7. 未知类别：仅遇未知layer时显示“其他/未识别类别”，中性灰，并保留原layer与身份，不能丢弃。

身份取值：优先非空recipe_id；没有时取recipe_ref。recipe_ref若为ID@version，仅去掉最后的@version部分用于身份比较，完整原值仍用于详情。禁止按描述文字或contains('u')猜DFT+U；若recipe_id与recipe_ref冲突，保留原数据并报告，不静默误分。不创造新的配方和未启用条目。

实现要求：
- 每个step保留原有独立卡片与名称；分类可用局部纯函数和固定映射，避免通用插件体系。
- 用文字标题+同类固定色Tag；不只靠颜色传意，也不把颜色称为成功/失败。主Timeline去掉，不按idx配色。
- 分组只读派生，不原地sort/mutate comp.selected。每组内部保留原数组次序。
- 使用项目现有antd Collapse显示“组合与覆盖详情”。组合顺序以原selected数组逐条展示，同时保留order值、完整ID/版本、选择原因和matched_context。不要为分类重新排列详情或修改props。
- 详情保留Composition ID/revision、pack/version、resolved_parameters；provenance按实际字段parameter/value/source_type/source_id/source_revision/overrode/derived_by及确认状态可读呈现；patches按已有parameter/operation/value/reason/confirmed_by_user呈现。原始未知字段可放补充数据展示，不编造来源或批准状态。
- 用户覆盖组用真实user_patch条目和patches表达；不要无依据把patch_id与recipe_id当同一对象，也不制造两次执行的结论。数据无来源明细时说明“未提供来源明细”，不要说“无覆盖”。没有应用配方的类别可以省略，不伪造默认选择。
- 冲突/警告若实际存在，主卡保留可见提示，详情按原数据展示，不能在折叠后伪装无异常；不新增科学推断。
- 沿用现有JSON值显示习惯并处理null/数组/长文本，折叠内容窄屏可换行或局部滚动，不撑破页面。

验收：同一类别在selected重排/新增条目后颜色不变；modifier.dftu单列、其他modifier不误归；主图不含Timeline；展开后原顺序/参数/覆盖来源仍在；空composition、缺可选字段、未知layer、多个step、用户patch、长内容均不崩溃。用冻结输入或相应断言验证组件不修改数据，测试可见行为而非大量快照。
不需要调用后端或模型，用小型typed fixture，参考现有React Testing Library写法。建议在frontend运行 npm run test -- src/components/recipes/RecipeCompositionPreview.test.tsx，再build/lint，最后浏览器检查折叠和窄屏（未做则明示）。

停止条件：实际layer/ID规则与以上不同、provenance必须新增后端数据才能获得、需要改变API类型或改变组合结果。缺字段可以明确显示缺失，不允许补造。完成后只交回执，不自动合并。
```

### B-2：设置字段说明与单位整理

```text
任务：B-2，对应N15。必须先收到总负责人确认的A-3完成基线；A-3应已完成设置分块、测试归位、数值控件及校验。未完成就停止等待，不自行接管A-3。

允许修改：frontend/src/pages/AiSettingsPage.tsx的label/help/展示布局及AiSettingsPage.test.tsx中相关断言。其他页面若发现同类问题，只列出位置交给总负责人，不扩范围。

固定文案与显示：
- MP API Key：帮助“用于 Materials Project 材料搜索与结构导入；密钥可替换或清除。”保留SecretInput及当前保存状态，不显示密钥。
- 最大作业数：帮助“本软件提交时参考该超算账号排队和运行中的作业数量，并按此上限限制新提交。至少为 1。”不宣称限制其他客户端提交，不修改现有保存值。
- 监控轮询间隔：标签清楚，输入显示秒单位；帮助“影响已提交作业的状态查询频率；范围 10–3600 秒，默认 60 秒。”不能只放placeholder，也不把60写回用户已保存配置。
- 每个字段的label/help/error在自己的布局容器里，不用一个跨列帮助解释另一列字段。窄窗口堆叠后仍对应。若添加htmlFor，给同字段输入匹配id；或使用现有Form.Item机制，不引入第二套表单状态。

不改：toForm、patchFields、onSubmit、冲突检查、SecretInput行为、密钥替换/清除、测试按钮处理、useEffect、数值onChange与校验、后端/API/hooks/类型。30/60/120快捷值不是本小任务要求。发现控件校验与范围不一致，报告A-3返工，不自行用前端新逻辑掩盖。

验收：三字段帮助准确对应，单位和范围在填入数字后仍可见；窄屏不串列。已有值仍展示原值，保存请求和测试请求不变，密钥没有回显。复用已有测试；纯文字断言适量，不调用真实连接测试。
建议在frontend运行 npm run test -- src/pages/AiSettingsPage.test.tsx，再build/lint。报告验证范围，无法做浏览器窄屏检查则列待验。
```

## 6. 集成、审查与交付约定

1. 用户是最终集成负责人；每项由唯一主责交付，另一个人发现问题先报告，不直接改对方未完成分支。
2. B-1先于A-4，A-3先于B-2；B-3通常独立，但U-2若改变Recipe表现字段须先同步合同。用户的诊断页变更与A-1同页接线串行集成。
3. 每项提交前（未来获准提交时）检查真实作者信息，队友自己的有效修改使用自己的正常Git身份。如何显示贡献依赖托管平台规则，本轮不承诺仅凭发任务即可进入贡献者名单，也不为此执行仓库权限或提交历史操作。
4. B的范围确定后轻量审查，重点查rawID、数据不变、字段逻辑不变；A-1与用户科学/执行相关变更按重要变更审查。
5. 用户最后在同一集成版本复检：原流程、A批新行为、AI可选、失败恢复和原修改保护。真实模型/计算若未做须清楚列待验，不拿build或模拟成功代替。
6. 不以队友各自通过就宣称A批完成；各批次回执包括实际修改、验证、限制和下一步，先供用户审核，不自动release。
