# 智能项目列表科研视觉适配回执

日期：2026-10-08。交付基线：`2bbe1753017b111b1ac58320d9d89aaa37f0669a`（PR #44 已合入 main），分支 `feat/scientific-ai-projects-ui`。用户已反馈本批其余部分无问题、复测基本通过；聊天界面尚未适配，属于独立 S-3 批次。准确提交的 CI 结果见对应 PR，本批未合并或发布。

## 展示与交互变化

仅 `/ai` 项目列表接入已有 Graphite / Cold Blue 深浅主题。主题路由继续使用 React Router `matchPath` 的完整路径匹配，支持原有大小写与尾斜杠规则；项目详情、任务对话与进度页不取得新的内容主题资格。

标题与新建按钮置于顶部，项目数量、创建时间／修改时间排序置于列表上方。项目名称、说明、任务数和修改时间保持真实返回字段；长文本换行，窄屏下项目操作区分行。项目主体增加原生选择按钮与 `aria-pressed` 状态，支持空格／Enter 选择；进入与删除按钮为同级控件。创建弹窗增加标签与输入框关联，并继承页面 ConfigProvider 主题。页面、弹窗和删除浮层样式均有独立作用域，共享壳 CSS 未改动。

等待队列保留后台 `count`（缺失时回退到实际列表长度）、任务标题、原因与排队时间；标题缺失仍显示“待定任务”，无效时间仍显示破折号。队列区明确“条件满足后重新预检并确认提交，不自动补提”，没有新增提交操作、统计或调度功能。

原排序算法、时间回退、`name.trim()` 空白校验、原始 `{ name, description }` payload、创建成功导航、删除请求及错误恢复保持。基线与候选从 `const projects` 到 `createProject`／`removeProject` 结束的业务片段逐字一致。

## 删除确认文案核查

旧“仅演示数据”说明与实际接口不符，已改为：“将删除该项目及其计算任务记录；不会取消超算作业或删除工作区文件。”

只读核查依据：`backend/ai_mode/server.py` 的项目删除路由调用 `ProjectStore.delete_project`；`backend/ai_mode/projects.py` 转发 Toolbox `DELETE /projects/{id}`；`backend/toolbox/api.py` 在服务锁与任务锁内调用持久存储删除；`backend/toolbox/projects.py` 先执行 `_retain_file_audit`，通过后删除项目与其任务记录并持久化。该调用链不取消超算作业，也不删除工作区文件。远端文件审计或执行中／未知上传仍可被后端以 409 拒绝，前端继续显示真实错误并保留项目，可再次操作。没有修改后端或删除行为。

## 文件范围

| 文件 | 用途 |
| --- | --- |
| `src/pages/AiProjectsPage.tsx` | 项目列表、创建弹窗、删除确认、排序与等待队列展示及键盘语义 |
| `src/pages/scientific-ai-projects.css` | 本页及所属浮层局部样式 |
| `src/pages/AiProjectsPage.test.tsx` | 保留两个独立重试回归，新增排序、创建、删除、键盘与队列字段行为回归 |
| `src/components/workflow/scientificNavigation.ts` | 精确增加 `/ai` 内容主题资格 |
| `src/components/workflow/ScientificWorkflowShell.test.tsx` | 双主题切换、大小写／尾斜杠、详情及进度页隔离回归 |
| `SCIENTIFIC_AI_PROJECTS.md` | 本回执 |

未修改 API、hooks、后端、依赖、锁文件、设置页、Toolbox 项目／任务页或统筹主文档。

## 已执行验证

| 命令／核查 | 结果 |
| --- | --- |
| `npm ci --no-audit --no-fund` | 按原锁文件安装成功，未修改依赖；保留 MSW 安装脚本提示 |
| `npm run test -- src/pages/AiProjectsPage.test.tsx src/components/workflow/ScientificWorkflowShell.test.tsx` | 2 文件、58 项全部通过 |
| `npm run build` | TypeScript 与生产构建通过；保留既有大资源块提示 |
| `npm run lint` | 0 错误，10 项既有 Fast Refresh 警告，修改文件无新增警告 |
| `git diff --check` | 通过 |
| 基线业务处理片段逐字比对 | 排序、队列字段、创建及删除处理均一致 |

行为测试覆盖创建／修改时间降序与缺失修改时间回退；名称全空白不写入；创建取消不写入；原始含空白 payload 与区分大小写 ID 导航；创建失败保留草稿后重试；删除取消零请求、精确 ID、审计错误后继续操作；键盘选择与进入；真实队列计数及原因、无效时间和重新预检说明。原项目失败／队列失败分别重试的回归保留。

首轮 53／58、第二轮 55／58，最终 58／58。首轮定位暴露装饰图标进入控件可访问名称，已为这些图标设置 `aria-hidden`。其余测试修正为点击 Ant 排序标签、在本测试 wrapper 禁用 jsdom 无法完成的弹层动画、等待队列 HTTP 返回后断言真实计数，并在测试后销毁静态消息。没有放宽测试时限或删减业务风险断言。日志保存在本候选 `frontend/s2-install.log`、`s2-tests-initial.log`、`s2-tests-second.log`、`s2-tests-third.log`、`s2-build.log`、`s2-lint.log`、`s2-diff-check.log`、`s2-semantics-check.log`，仅为本地证据。

## 验收状态与限制

统筹已完成真实 Chromium 生产构建验收，使用独立本地合成 HTTP 服务。深色1920／1280／768／375px无页面或项目控件横向溢出；浅色1280／375px、长名称／说明／队列原因、窄屏新建与删除确认、空态和错误态检查通过。删除弹层在375px内完整显示。创建取消和空白名称均零POST，创建失败保留草稿，重试成功按原ID进入；取消删除零DELETE，删除失败保留项目，重试仅删除合成目标ID。项目与队列分别重试，另一接口请求次数不变，恢复空态正常。键盘Enter选择、Tab到删除和进入、Enter按原ID导航通过；`/AI/`主题生效，详情页保持旧主题隔离。

独立只读审查未发现阻断项，产品与相关测试六文件范围符合合同，61项用户保护文件保持。用户已复测本批，反馈基本通过；现整理独立PR，尚未合并。证据及截图在统筹本地`.tmp/ai-projects-ui-review`，不作为真实科学计算证据。

快速模拟视口切换再次观察到未改动的公共浮动助手移到左上角；已按既有独立线索保留，不能据此认定普通窗口必然复现。本批未改该组件；恢复默认视口后拖回右下角并刷新保持通过。没有穷举所有宽度、主题及表单状态组合。队列验证为静态字段和交互展示，未验证动态调度。

测试和浏览器均使用合成数据，未调用真实LLM、MP、SSH、HPC或VASP。没有发布或替换桌面程序。下一步完成独立PR与准确提交CI后交付回执，再决定集成；A-4继续由队友A从当前main独立接入，任务对话视觉另列S-3。