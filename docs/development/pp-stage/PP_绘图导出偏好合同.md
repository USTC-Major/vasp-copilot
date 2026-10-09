# PP：绘图与导出偏好合同

日期：2026-10-10。位于 `feat/pp00-postprocessing-contract` 的本地候选；未提交、推送、发布或修改正在运行的用户设置。本文记录本次独立偏好实现，白底 PNG 重绘和坐标合同另由绘图层实现。

## 产品行为

- 全局默认为“科研通用（色盲友好）”，另有“蓝橙”“低饱和”“黑白打印”。PNG 固定白底、深色坐标与文字，和页面深浅主题分别处理。
- 可编辑六位十六进制颜色，增加或删除颜色（2–16 个），前移或后移颜色顺序；修改预设颜色或顺序会转换为“自定义”。颜色按稳定曲线语义使用固定槽位，筛选或曲线重新排序不改变同一语义的颜色；自旋通道保留线型区别。配色顺序决定各槽位的颜色。
- 白底配色示意图用于查看颜色与线型；白底对比度低于 3 的颜色给出较浅警告，允许用户在知情情况下导出。警告不代替实际打印、色觉模拟或论文规范验收。
- 设置页增加独立“绘图与导出”区块，只通过“保存默认配色”持久化，不并入 LLM／SSH／MP 的保存请求。智能服务读取失败的页面也保留此 Toolbox 区块。
- 导出对话框从已保存默认开始。本次改色和改序只保存在对话框草稿；“下载 PNG”“预览 PNG”均不保存全局偏好。只有明确点击“将当前配色设为默认”才写默认。
- 导出宽度和高度为整数像素，单边 240–8000，总像素最多 3200 万；默认 1600 × 1000。支持锁定当前比例和 16:9、4:3、3:2、1:1、3:4 常用比例。尺寸为本次导出参数，不写入全局配色偏好。
- “预览 PNG”调用与实际下载相同的白底重绘回调，显示实际图片；尺寸或配色变化后清除过期预览。失败会在对话框显示，保留草稿以便重试。配色读取失败时可用科研默认进行临时导出，但禁止未读取版本时保存全局默认。

## 独立存储与 API

文件位于现有 Toolbox 根目录的 `postprocessing/preferences.json`。不读取模型配置、连接凭据或操作系统主题，不创建新凭据体系。首次 GET 无偏好文件时返回默认和 revision 0，不写文件；显式 PUT 后使用临时文件和原子替换保存。服务重启后从同一路径恢复默认配色与颜色顺序。

GET／PUT `/api/v1/toolbox/postprocessing/preferences` 是独立路由，挂载于现有 Toolbox router；不改数据集、源文件或分析结果。

GET 响应字段：

```json
{
  "mode": "toolbox",
  "preferences": {
    "schema_version": "pp.preferences.v1",
    "revision": 0,
    "palette": {
      "preset": "scientific",
      "colors": ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#333333", "#8B5C00"]
    }
  },
  "presets": [{ "id": "scientific", "label": "科研通用（色盲友好）", "colors": ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#333333", "#8B5C00"] }]
}
```

示例仅展示 presets 中的一项；实际含四项。PUT 请求必须包含 `expected_revision` 与完整 `palette`。合法 preset 为 `scientific`、`blue_orange`、`muted`、`print`、`custom`；预设颜色须与预设定义完全一致，自定义颜色会规范为大写。未知字段、非法颜色、颜色数量超限和非法版本值被拒绝，不接受背景或页面主题字段。

保存成功递增 revision。旧页面保存返回 `409 / PP_PREFERENCES_CONFLICT`，不会覆盖新默认。损坏或无法读取的偏好文件返回 `503 / PP_PREFERENCES_UNAVAILABLE`，不会悄悄以默认值覆盖原文件；写入失败返回 `503 / PP_PREFERENCES_SAVE_FAILED`。错误界面可重新读取并保留当前导出草稿。

当前服务为单进程本地应用，模块锁保护同进程读取／更新；不宣称提供多独立后端进程共同写入一个偏好目录的锁。已打开的其他浏览器标签页需重新读取或重新打开导出框来获得最新后台默认；revision 检查仍防止它用旧值无提示覆盖。同一 React 应用中的已挂载组件会同步收到显式保存后的默认更新。

## 前端组件合同

- `api/plotPreferences.ts`：独立类型、四预设、API、颜色格式及白底对比度校验。
- `hooks/usePlotPreferences.ts`：`usePlotPalette(enabled?)` 返回默认 `colors`、完整 `preferences`、`loading`、`error`、`reload()`、`saveDefault(palette)`；无需智能服务或 React Query 上下文。
- `PlotPaletteEditor`：颜色编辑、顺序与示意图，所有改变交给调用方草稿。
- `PlotPreferencesSettings`：独立默认配色保存按钮，最小嵌入 `AiSettingsPage`。
- `PlotExportDialog`：`{open,onCancel,onExport,preview?}`，导出与预览回调接收 `{width_px,height_px,colors}`；组件自显示错误，成功下载后调用 `onCancel`。
- 白底 PNG 工具接收 `{width,height,colors}`，页面只转换字段名；预览和下载均使用同一曲线快照与相同导出工具。

## 定向验证与限制

隔离验证不访问模型、MP、SSH 或真实用户状态，不安装或升级依赖。后端 `tmp_path`／隔离 runtime home 验证，前端偏好 API 为 mock。

- `backend/tests/test_postprocessing_preferences.py`：16 项通过。覆盖首次读取无写入、副本隔离、颜色顺序与大写规范、服务重启恢复、同进程旧版本冲突、损坏文件保留、所有预设及非法请求。
- `frontend/src/components/postprocessing/PlotExportDialog.test.tsx`：11 项通过。覆盖精确宽高、锁比例、常用比例、临时改色不保存、显式默认与重开恢复、浅色警告、格式拒绝、真实预览回调及失效、导出失败保留、读取失败回退、版本冲突保留草稿、设置区块独立保存和像素预算。
- `frontend/src/pages/AiSettingsPage.test.tsx`：原 11 项通过；仅增加偏好 API 隔离 mock，原设置保存、冲突与秘密接口行为保持验证。
- 合计定向前端 22 项通过；`git diff --check` 通过。初轮前端构建通过。修正配色初始化／预览竞态后交由独立审查统一执行最终构建、lint、全量测试及浏览器 PNG 像素验收，避免并行修改 dist。

示意 SVG 和 mock 回归不证明最终 PNG 像素、裁切、文字布局或不同 DPI 浏览器行为；这些依赖真实重绘层及独立浏览器验收。本次不增加 DPI／物理尺寸、透明背景、论文模板、任意配色配置文件导入或跨标签页实时轮询。
