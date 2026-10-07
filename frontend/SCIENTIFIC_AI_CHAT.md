# S-3 智能项目与任务聊天视觉交付回执

GitHub 全量 CI 补查发现两份既有主题边界测试仍将 `/ai/projects/:projectId` 视为未迁移页面。该前提已被本批批准的聊天主题迁移替代。交付范围因此从13文件增加至15文件：新增修改 `src/components/diagnosis/SharedDiagnosisTheme.test.tsx`、`src/components/toolbox/ToolboxTheme.test.tsx`，保留旧进度页的外层主题隔离及共享组件默认行为断言，并补聊天路由深浅切换、内容/选择保持和不增加业务调用的覆盖。此修正只更新测试合同，产品实现与用户复测版本保持一致。下方13文件为首轮冻结状态。

本批交付基线为 `c58d0a972222f3ba47ff012636b690b826dc246d`（S-2 PR45已合并），目录 `.tmp/scientific-ai-chat-ui`，分支 `feat/scientific-ai-chat-ui`。聊天视觉、深色配色及公共助手拖动共13文件，用户已复测通过。原验收基线5d3ed29与该main代码树完全一致，12个产品/测试文件保持验收内容，只有本回执更新交付记录。后续准确提交CI结论以PR正文与统筹记录为准；不自动合并本批或发布。下文保留分阶段验收历史，其中“未提交”和11文件描述属于当时状态。

## 布局与主题

- 精确将 `/ai/projects/:projectId` 加入既有科研主题；保留路由的大小写及尾斜杠规则。未知前缀、额外路径和旧 progress 地址的路由行为保持原状。
- 聊天页面在既有壳内分配 `100dvh` 剩余空间，移除固定最小高度与负外边距。桌面侧栏单独滚动，保留 200–480px 拖动宽度与原存储键，并提供左右箭头/Home/End 调宽；拖动监听在卸载和切换窄屏时清理。
- 900px 及以下任务列表默认收起为 Drawer。选择任务、打开新建任务或精度设置后收起抽屉；任务选择仍使用原 `selectTask`，输入草稿保持原语义。
- 标题/工作区元信息限高可滚动；消息、共享状态、待授权卡、结果和连接提示共用主滚动区，输入区固定在其下。长路径换行，代码块局部横向滚动，完整授权预览继续限高滚动。
- Graphite / Cold Blue 复用既有 `--wf-*` 与 Ant Design 主题。历史和实时助手正文复用既有安全 React Markdown 解析器，保留存储消息原文及代码内容；普通段落/列表项保留换行。思考区默认折叠。
- 批量和删除确认从静态 `Modal.confirm` 迁移至 `Modal.useModal` 继承页面主题；保留原 Promise、onOk/onCancel、选择代次与卡片快照语义。

## 实际文件

- `src/pages/AiProjectPage.tsx`
- `src/pages/scientific-ai-chat.css`（新增，聊天及自身弹层作用域）
- `src/pages/AiChatLayout.test.tsx`（新增）
- `src/components/ai/AiTaskSidebar.tsx`
- `src/components/ai/AiChatBubble.tsx`
- `src/components/ai/AiContextBar.tsx`（可选主题 token，默认兼容）
- `src/components/ai/AiDirectoryPicker.tsx`（可选 portal class，复用既有 scientific 模式）
- `src/components/ai/AiProjectExtraSettings.tsx`（可选主题 token 与自身 portal class，默认兼容）
- `src/components/workflow/scientificNavigation.ts`
- `src/components/workflow/ScientificWorkflowShell.test.tsx`
- `src/components/chat/ChatPanel.tsx`（用户复测后明确追加授权的拖动补修）
- `src/components/chat/ChatPanel.position.test.tsx`（对应位置/点击/清理回归）
- 本回执。

## 验证

按原锁文件 `npm ci --no-audit --no-fund` 成功；依赖与锁文件未修改。

- 既有相关 5 文件 138 项通过：`AiFlow`、`AiProjectExtraSettings`、`ScientificWorkflowShell`、`router`、`api/client`。既有恢复、单卡/批量授权、unknown/失败、切任务保护测试没有删减。
- 最后产品兼容变更后定向复跑 `AiFlow` 与 `AiProjectExtraSettings`，2 文件 52 项通过。
- 新增 8 项覆盖键盘任务切换与草稿、拖动/键盘调宽边界和持久化、窄屏 Drawer、切任务中止旧 SSE 与旧卡隔离、Markdown/多行参数/安全转义和滚动区边界、批量取消零写入与原卡快照、AI 不可用单次错误恢复、旧批量确认在切任务及返回原任务后均零授权请求。
- `npm run build`（包含 TypeScript 检查）、`npm run lint`、`git diff --check` 通过。lint 保留既有 Fast Refresh 导出警告，build 保留既有大 chunk 提示；JSDOM 保留伪元素 getComputedStyle 缺失提示。
- 首批保护文件核查对基线 diff 为空：API、hooks、共享 Markdown 工具、依赖/锁文件、S-2 项目列表、S-1 设置、Toolbox 页面与当时未改的公共助手。公共助手的定位补修在用户复测后得到单独授权，见下节。

本地日志位于候选目录根：`s3-npm-ci.log`、`s3-tests.log`、`s3-final-chat-tests.log`、`s3-layout-tests.log`、`s3-build.log`、`s3-lint.log`。日志和 dist 是本地验证产物，不纳入产品提交。独立浏览器与源码复核由统筹执行，结果另记；此回执不将单元测试视为实际视口、真实模型或科学验证。

## 审查修正与边界

独立首审指出 Markdown 段落的默认 normal 会合并未围栏科学参数换行，已仅在聊天 CSS 为 p/li 保留 pre-wrap，并补原换行参数回归。代码块、表头、授权卡和精度抽屉使用主题背景，未更改共享解析器。

真实浏览器首轮发现 375px 精度抽屉头部操作区稳定后仍横向溢出。已仅为科研模式的头部 Space 开启 wrap，并将自身 extra 行限制为可收缩的整行宽度；模板/清空/自动保存业务未改。返工后精度组件已有 4 项测试、build/type、lint 与 diff check 全部通过，定向日志 `s3-extra-wrap-tests.log`；浏览器增量复测由统筹确认。

基线 `ToolboxTaskStatus` 已展示待授权卡的确认/拒绝按钮；聊天顶部的 `observeOnly` 仅观察同一查询缓存，不会令嵌入状态卡只读。该现状已报告统筹，本批按明确反馈保留组件及授权行为，不能称其为严格只读嵌入。没有新增目录初始化、作业调度、取消远端作业、写文件或自动重发能力。

SSE/AbortController、任务选择代次、草稿清空时机、确认卡 ID 与单次/批量审批的业务处理没有重构。未调用真实 LLM、Materials Project、SSH 或 HPC；无浏览器夹具写入本候选。

## S-3首轮统筹验收（2026-10-08，用户复测补修前）

独立源码终审无剩余阻断；精度抽屉最后换行增量另经只读复核。真实 Chromium + 生产 dist + 本机合成 HTTP 验收通过：四宽1920/1280/768/375聊天页无横向溢出且输入区在视口内，深浅主题代表宽窄，任务抽屉和草稿跨任务保留，代码及普通多行参数（computed pre-wrap），超长无空格路径，创建/目录取消、创建失败保留草稿和重试成功，批量取消零确认请求、批准两卡两请求、单卡unknown不显示成功且无重发、慢速流切任务无旧消息/卡串入、EOF明确断线并恢复输入、之后手动发送正常完成。精度抽屉375px稳定后宽359、scrollWidth359，三操作按钮均在视口内。

受控请求全部在合成服务内，不调用真实模型或文件系统操作。夹具初次创建失败存在HTTP/1.1未消费请求体的连接风险，修正普通JSON连接关闭后重新验收，不计入产品代码修复。快速视口切换的过渡状态不计作稳定布局；公共助手旧位置问题在该阶段仍独立登记，后续用户授权补修见下一节。没有穷举所有错误/主题/视口组合，真实模型质量与实际科学计算未验。

本地证据位于D:/Documents/VASP-Doctor/.tmp/ai-chat-ui-review，包含截图、layout-check.json、browser-review.json、请求计数、11文件哈希和61保护项核查。用户复测入口 http://127.0.0.1:8935/ai/projects/synthetic-chat-project （仅合成数据；本地服务运行期间可访问）。本批未提交或推送；S-2 PR45仍未合并，S-3准确基线为5d3ed29而非main。用户复测通过后，再单独安排S-2集成与S-3独立PR/CI，不发布或替换EXE。


## 用户复测补修（2026-10-08）

用户反馈深色聊天头部路径/危险操作颜色区分度低，公共助手气泡拖动粘滞。本轮在原候选上追加授权修改，共 13 个产品/测试/回执文件，继续保持未提交与未推送。

深色配色只作用于 `.scientific-shell[data-workflow-theme="dark"] .scientific-ai-chat`：本地路径冷蓝、超算路径浅青、普通状态灰蓝、模拟/提醒浅琥珀、成功低饱和绿色、错误/危险珊瑚红。路径保留浅色原 Ant color preset，仅附加语义 class；浅色没有新增覆盖。六组声明的文字/背景对比度分别为 neutral 7.78、blue 7.49、cyan 8.46、amber 8.31、success 7.92、danger 7.98；这些数值由源码颜色计算，实际浏览器样式/交互验收由统筹记录，disabled 不按普通文本对比验收。

浏览器确认公共 FAB 原 computed `transition-property:all`、duration 0.2s，导致 left/top 持续追赶；源码原每个 mousemove setState 后同步写 localStorage。已显式将 FAB transition 限于 background-color/border-color/color/box-shadow。面板 computed duration 为 0，不增加无必要的样式改动。

拖动沿用原鼠标事件、存储键、默认位置与 viewport clamp：rAF 合并每帧最后 move；mouseup 用真实终点立即 flush、取消待帧并保存落点；曾越过 6px 阈值的拖动即使返回起点也抑制随后的鼠标 click，并消耗该抑制状态，键盘 detail=0 点击可继续打开。blur 只提交最后已见 move；resize 终止拖动并按新尺寸收敛；unmount 取消帧/移除监听/保存已见落点，不 setState。未增加 PointerEvents/touch 功能。

本轮定向验证：ChatPanel.position 6 项通过（`s3-feedback-tests.log`），涵盖每帧合并及落点一次保存、mouseup 终点、返回起点后的误点击抑制及 Enter/正常点击、blur/unmount 清理、panel 拖动中 resize 与陈旧帧隔离、旧视口边界。AiChatLayout 最终 11 项通过（`s3-feedback-layout-tests.log`），在既有 8 项上补深浅路径资格与全部强调配色 >=4.5:1、覆盖严格隔离 dark 聊天的校验。初组的两项路径查询未命中 Ant 6 的嵌套内容 span，已只修测试查询后通过。build、最终类型检查（`s3-feedback-type.log`）、lint 和 diff check 通过；未重复无关全量测试。

公共助手从 `listRef` 到 JSX return 前的 history/config/send 模型逻辑块逐字对基线一致，API/hooks/依赖/锁文件/共享 Markdown/S-1/S-2/Toolbox 页面仍无改动。真实模型与外部科学/执行服务未调用。产品增量冻结交独立源码与浏览器复核；浏览器首测确认 FAB transition、拖动落点、Enter 打开、panel 拖动和 reload 持久化正常，浅色与窄屏等剩余增量结果由统筹回执补齐。

统筹最终增量复核：独立源码审查无新增阻断；Chromium实测头部文字对比7.49–8.46（中性7.78、提醒8.31），浅色头部computed文字/背景/边框逐项与修改前一致。FAB不再过渡left/top；从(1200,564)拖至(990,444)实际矩形与样式落点一致且未误开，Enter打开、鼠标关闭与刷新后保留通过；面板(40,72)拖至(210,114)通过。375px下按钮right367、bottom496保持边界内，页面无横向溢出。已恢复默认视口并将气泡放回不遮挡输入按钮的位置。没有承诺消除所有硬件/浏览器环境下的卡顿；本轮证据针对原坐标缓动和重复存储根因。补修证据位于`.tmp/ai-chat-ui-review/retest-feedback`，61保护项保持。继续同一8935合成预览供用户复测，未提交/推送/合并/发布。
## 用户验收与Git交付

用户在配色/拖动补修后明确反馈复测通过，并批准PR45合并及本批独立PR/CI。PR45合并树已核与原基线相同；本批仅交付上述13文件，不夹带S-2或新增功能。既有验证在产品内容不变时复用，不重复同树本地全量测试。停止点为本批PR待审核，不发布或替换EXE。
