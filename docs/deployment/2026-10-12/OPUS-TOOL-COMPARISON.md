# Opus 管理页成功、Claude Code 失败：四组对照

测试时间：2026-10-12 13:47:34–13:49:14（Asia/Shanghai）。用户本次明确授权向 8993 发送最多四条合成短请求；四条已全部执行，服务内部重试共形成 11 次上游尝试。本轮未部署、重启或修改配置，未发送真实会话内容。

## 实测

四组均调用 8993 `/v1/messages`，模型 `claude-opus-5-5`，`stream=true`、`max_tokens=1024`，提示为 `Reply with exactly: OK. Do not call tools.`。四组 trace 均落到最高优先级账号 #5。

| 组别 | 客户端结果 | 总耗时 | 首个文本 | 上游尝试 |
| --- | --- | --- | --- | --- |
| 普通文本 | 200，完整结束 | 17.478 秒 | 17.477 秒 | 500 → 200 |
| adaptive 思考 + high effort | 200，完整结束 | 45.572 秒 | 45.571 秒 | 200 |
| 普通文本 + 一个工具定义 | 502 | 19.604 秒 | 无 | 4 次 500 |
| 思考 + 一个工具定义 | 502 | 16.801 秒 | 无 | 4 次 500 |

工具是合成的 `diagnostic_echo`，仅有一个可选字符串参数。未执行工具。所有上游失败均为 HTTP 500，`reason=MODEL_TEMPORARILY_UNAVAILABLE`，消息为 `Encountered unexpectedly high load when processing the request, please try again.`。客户端最终看到的 502 是网关在上游尝试失败后的结果。

两条成功请求都包含 `message_stop`，证明该账号可以完成 Opus 流式请求；加入思考参数也至少成功过一次。此次两条带工具定义的请求均在上游返回错误，未进入正常输出阶段。

客户端结果与 trace 按模型、开始时间及 200 毫秒内唯一候选关联，没有共享 request ID。脱敏证据：

- [客户端结果](evidence/opus-compare-client.jsonl)
- [关联 trace 及上游尝试](evidence/opus-compare-traces.jsonl)
- [测试脚本](compare-opus-requests.py)（默认 dry-run；再次使用 `--execute` 需要新的适用授权）

## 代码核对

- 管理页“真实测试”在 `src/admin/service.rs::test_model` 直接构造最简 Kiro 请求，无工具、历史和额外思考字段。它能成功不代表 Claude Code 的完整请求能成功。
- 本轮先前读取的实际 Claude Code INFO 日志显示入口为 `/v1/messages`，不是 `/cc/v1/messages`。已部署的 `/cc` 增量流修复不覆盖这些请求，不能用它解释本次失败。
- `call_api` 与 `call_api_stream` 进入同一个上游请求/重试实现；客户端流式开关并不在这里选择另一种上游协议。
- 模型别名 `claude-opus-5-5` 会归一化为 `claude-opus-5.5`。`max_tokens` 在此转换链路不作为字段发送给 Kiro；不能仅凭 Claude Code 设置 128000 将本次上游错误归因为输出长度限制。
- `src/anthropic/converter.rs::convert_tools` 将普通工具包装为 `toolSpecification`，schema 放在 `inputSchema.json` 下。此次合成工具不命中 Claude Code 内置工具替换规则。此次检查没有发现可以直接确认并修复的转换缺陷；结构检查不等于已验证上游兼容性。

## 推测与验证边界

当前最值得继续验证的是 Opus 对含工具定义请求的处理，范围包括 Kiro 上游处理和本项目协议转换兼容性。不能据此直接宣布 Anthropic 全局故障，也不能宣布 Opus 不支持工具。

这是按固定顺序各执行一次的对照，未随机交错重复。测试期间还有自然 Claude Code 请求（包括 13:47:41、13:48:55 开始的请求），不是独占空闲环境。普通文本自身第一次也收到相同 500，因此时变负载、并发和其他请求差异仍是混杂因素。先前记录还存在 13:16 的较复杂 Opus 请求成功，故不能推断所有复杂/工具请求必然失败。

客户端 usage 与 trace credits 只代表本网关记录；失败 trace 的 credits=0 不证明外部最终账单为零。四组比较没有验证 Sub2API 最终计费。

尚未定位到确定代码缺陷，当前不应以删除 Claude Code 工具定义作为修复。若继续实测，应在受控调用窗口交错重复有/无工具请求，并比较最小 Kiro 工具请求与转换结果，以区分偶发负载和协议差异；本轮四条额度已用完，额外推理须另获授权。
