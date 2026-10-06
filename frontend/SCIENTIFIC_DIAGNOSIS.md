# 诊断页面科研视觉适配回执

日期：2026-10-07。候选基线：`6729493c2d5c793358dbeef880de99fed62ab976`（公共导航与首页 PR 已合并；与实施起点 `380095f` 同树）。用户复测反馈未见明显问题，已批准整理独立 PR 并运行 CI。合并、发布及替换桌面程序不在本次交付范围。

## 本批变更

诊断上传页与结果页接入公共 Graphite / Cold Blue 深浅主题，继续使用 `vasp-copilot.workflow-theme`。已移除上传页旧品牌后缀；报告摘要改为紧凑状态总览，显示的摘要、严重问题数量、最高严重度及 VASP 版本仍来自同一响应。375px 下诊断状态独占整行，其余信息按两列排列，长标题、证据、文件名及状态可换行。

证据、修复建议、原始差异、模型解释和下载区域采用同一边界与文字层级。所有新增页面 CSS 限定于 `.diagnosis-page`。上传和模型解释组件也被智能项目页使用，因此它们新增的诊断变量均保留原浅色 fallback；尚未迁移的智能页面不会因公共深色导航而获得诊断配色。

SCF 双视图与磁性排列图仅调整文字、轴线、网格、tooltip、系列和参考线的显示颜色。颜色来自当前主题；不改变数据、符号、零值／缺失值、科学 formatter、坐标类型或范围、过滤、阈值、参考线数据、缩放或原始证据。主题切换更新图表 option，没有设置会重建图表实例的 ECharts `theme`。

`DIAGNOSIS_NOT_FOUND` 显示记录不存在或已过期的说明和返回上传入口，不假装恢复 TTL 记录；其他请求失败保留原重试行为。修复候选可用性、下载禁止条件、风险说明、人工确认、LLM 能力检查／禁用／失败／重试及上下文裁剪提示均保留。

## 准确文件范围

| 文件 | 内容 |
| --- | --- |
| `src/pages/DiagnosisUploadPage.tsx` | 页面标题、上传工作区和开始操作布局 |
| `src/pages/DiagnosisResultPage.tsx` | 状态／摘要总览、加载、请求错误及 TTL 说明 |
| `src/components/diagnosis/DiagnosisUploadPanel.tsx` | 扫描状态、检测文件排版与旧嵌入 fallback |
| `src/components/diagnosis/IssueCard.tsx` | 问题标题换行、证据／建议可读配色 |
| `src/components/diagnosis/LlmExplainPanel.tsx` | 标题换行、回答背景、问题输入可访问名称 |
| `src/components/diagnosis/RepairSuggestions.tsx` | 局部修复区域样式与差异背景 |
| `src/components/diagnosis/ReportDownloadPanel.tsx` | 局部下载区域样式类 |
| `src/components/diagnosis/ScfPlot.tsx` | 当前主题的图表显示颜色 |
| `src/components/diagnosis/MagnetizationPlot.tsx` | 当前主题的图表显示颜色 |
| `src/components/diagnosis/diagnosisChartStyle.ts` | 两个诊断图表共用的纯显示颜色函数 |
| `src/components/diagnosis/scientific-diagnosis.css` | 诊断专用组件边界、深浅变量、窄屏与焦点 |
| `src/components/workflow/scientificNavigation.ts` | 诊断上传／结果路由的主题资格 |
| `src/components/workflow/scientific-workflow.css` | 窄屏导航按内容高度分配行，正文承接短页剩余高度 |
| `src/components/workflow/ScientificWorkflowShell.test.tsx` | 诊断资格、尾斜杠、主题往返及旧内容隔离 |
| `src/components/diagnosis/ScfPlot.test.tsx` | 图表实例、科学数据和联动缩放跨主题保持 |
| `src/components/diagnosis/MagnetizationPlot.test.tsx` | 真实磁性 builder 的单轴／数据／formatter及筛选保持 |
| `src/components/diagnosis/SharedDiagnosisTheme.test.tsx` | 旧智能页共享面板仍使用原 provider 和 fallback |
| `src/pages/DiagnosisPages.visual.test.tsx` | 扫描、显式诊断操作、失败重试、TTL与长摘要 |
| `src/router.test.tsx` | 诊断页新版标题冒烟检查 |
| `SCIENTIFIC_DIAGNOSIS.md` | 本回执 |

没有修改 `scfPlotModel.ts`、`magneticPlotModel.ts`、科学数据／类型、API、hooks、后端、桌面、依赖或锁文件。智能项目父页面、首页和工作流科学组件保持原样。

## 已执行验证

- 最终关联回归：17 个测试文件、185 条用例通过，涵盖诊断、两个科学 Model、router、公共壳、旧智能流程、首页、工作流与浮动助手。
- 深浅切换测试确认两个图表均保持单次挂载；SCF 的数据／formatter／坐标域／参考线 precision 与数据／联动 zoom 保持，磁性图的真实 builder 数据、单轴、tooltip 和 Fe 筛选保持。
- 页面状态验证保留显式上传后开始诊断、执行失败后错误重置与再次开始的原请求行为；404 TTL 与 503 临时失败分开说明；原报告和候选门槛仍受响应约束。
- 最终 TypeScript 和生产构建通过；最后窄屏摘要与短页导航高度修正仅改 CSS，并再次构建通过。
- lint 通过，仅 10 项既有 Fast Refresh 警告；没有新增告警。`git diff --check` 通过。
- 首次磁性补测沿用了原测试的空图表桩，访问不存在的 tooltip 导致该补测失败；改用真实 builder 后通过，未为适应测试修改产品科学逻辑。

测试使用既有 MSW 或本地组件桩，没有调用真实 MP、LLM、SSH、HPC 或 VASP。jsdom 的伪元素计算样式和跨文档导航提示，不作为真实图表显示或下载落盘证据。

统筹最终真实浏览器复检通过：生产构建配合隔离本地后端，合成 ZIP 经上传、扫描、开始诊断进入实际报告；深浅 SCF、磁性图与 Fe 筛选保持、修复不可用原因、AI 关闭时确定性报告可读均已检查。375px 页面无横向溢出，摘要长状态独占首行；短错误页导航从异常拉伸恢复按内容布局。缺失记录展示准确说明，返回上传入口可用。最终宽窄屏均使用最后一次生产构建。

页面下载 Markdown 实际落盘 2529 字节，与接口原始字节 SHA256 一致（`25ce192f17bac92e94342775b291b7b2a4c675bb97cbf0d5f3c854fb8319e8de`）。浏览器工具未返回下载事件，下载成功以独立文件落盘和哈希为证。合成输入仅用于软件与 UI 验收，不作为真实科学案例；可用修复 ZIP、LLM 成功回答和其他失败分支的覆盖来自组件测试，不冒充真实服务验收。

最终独立源码复审无阻断；61 项原有工作文件与配置保护检查一致。自动验收使用的临时诊断服务正常停止并释放端口；随后为用户另开隔离的诊断试用服务，用户反馈未见明显问题。该反馈作为视觉复测证据，不扩展成真实模型或科学计算验收。

## 独立浏览器验收方法与限制

先用既有隔离本地运行方式打开最新生产 `dist`，刷新页面以读取最终资源。全程关闭真实外部模型与集群调用。

1. 首页→诊断上传→上传本地合成 ZIP→查看扫描／检测结果→明确点击开始诊断→结果页。核对标题、原文件与问题状态；从首页真实记录返回同一报告。
2. 深浅主题分别查看摘要、严重度文字、证据折叠、修复差异和风险提示；报告候选状态不能因换主题改变。切换至工作流和旧智能／设置页，确认其主题资格和共享面板边界。
3. 宽屏与 375px 查看 SCF 双图标题、数字、EDIFF／NELM参考标注、tooltip；切换数据块／全程末期、缩放后换主题，核对视图状态保持。磁性图检查正负值、原子顺序、元素筛选、分页和局部表格滚动。
4. 查看 LLM 未配置／不可用状态下的原报告，使用离线测试响应检查回答、裁剪说明及失败重试；不发送真实模型请求。键盘检查跳到主内容、上传／开始、详情 summary、筛选、下载和解释输入焦点。
5. 使用缺失／过期记录与可恢复请求失败分别检查页面；下载报告核对实际文件，候选不可用／刷新中／服务器拒绝状态依旧禁止或报错，不把发起下载当成已应用修复。

本批不重构科学模型、不增加自动修复、不改变原上传／诊断动作和授权规则。实际科学参数质量、真实模型效果、跨浏览器全量无障碍与原生桌面下载仍不属于前端视觉验收。

## 下一步

已通过用户视觉复测，下一步完成独立 PR、准确交付提交的 CI 与审查回执；检查通过后另行确认合并。计算任务、网页设置和智能模式的整页视觉迁移另定批次，不借本次扩大范围。
