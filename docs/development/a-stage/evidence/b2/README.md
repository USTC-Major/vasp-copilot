# B-2 设置页展示验收

- 开发基线：`11d81f591e8fb3a3073f93fa8146828dce029ed2`（PR #30 已合入）。
- 页面：真实 `/ai/settings`，包含应用导航；本机无头 Microsoft Edge（Chromium）。
- 视口：1280×1000、375×1000；PNG 为完整页面截图，图片高度随内容变化。
- 全部 API 请求在浏览器中拦截，响应为合成数据；外部网络请求阻断。未调用真实后端、LLM、MP、SSH、超算或 VASP。
- `before-*` 使用基线设置页；`after-*` 使用本次修改。相同配置：最大作业数 7，轮询间隔 120 秒，密钥只提供“已保存”状态，未提供密钥内容。

| 场景 | 修改前 | 修改后 |
|---|---|---|
| 宽屏：四区块、测试成功/失败及长结果 | [before-1280.png](before-1280.png) | [after-1280.png](after-1280.png) |
| 窄屏：同一场景 | [before-375.png](before-375.png) | [after-375.png](after-375.png) |
| 宽屏：字段错误和未保存提示 | — | [after-1280-validation.png](after-1280-validation.png) |
| 窄屏：字段错误和未保存提示 | — | [after-375-validation.png](after-375-validation.png) |

## 实际检查

1. LLM、SSH、MP 测试按钮分别调用其已有测试端点；结果留在各自区块内。LLM/MP 返回模拟成功（含长无空格文本），SSH 返回模拟失败。
2. 宽窄屏均无页面横向溢出（`scrollWidth == innerWidth`）。修改后模型/provider、SSH 字段在窄屏堆叠；深度思考标签不再逐字挤成竖列；长测试按钮换行。
3. 最大作业数填 0、轮询间隔填 9 后保存，两个既有错误均出现在对应字段下；没有发送保存请求。
4. 存在未保存修改时点击测试，显示原有先保存提示，不额外发送测试请求。
5. 已保存的 120 秒仍显示为 120；“默认 60 秒”仅为帮助文字。密钥输入保持空白，仅显示保存状态。
6. 新增单元断言验证帮助/校验信息与控件的可访问关联。对源码比较确认设置加载、保存、冲突、密钥、校验、测试业务逻辑及所有 onChange/onClick/onClear 处理保持不变。

## 本地验证

在 `frontend` 执行：

```sh
npm ci --no-audit --no-fund --cache E:/codex-copilot/npm-cache
npm run test -- src/pages/AiSettingsPage.test.tsx
npm run build
npm run lint
```

- 设置页 10 项测试通过。jsdom 有 pseudo-element getComputedStyle 未实现提示，不影响结果。
- build 通过，有超过 500 kB 的 chunk 警告。
- lint 通过，10 条 Fast Refresh 警告均在本次未修改文件。
- `git diff --check` 通过；依赖及锁文件未改。

本次是离线展示验收，不证明真实连接或科学计算成功。长输入值仍沿用原生输入框的水平滚动/占位文字省略行为；共享 SecretInput、应用导航及浮动助手未修改。
