# Kiro 开源网关备选检索

检索日期：2026-10-09。通过 GitHub 仓库搜索筛选并拉取 5 个仓库静态核验。未运行这些项目、未使用生产凭据测试、未修改当前部署；存在测试文件不等于本次已运行或证明生产稳定。下述结论仅对应固定 commit。

## 优先候选：minpeter/kiro-lb

- 仓库：https://github.com/minpeter/kiro-lb
- commit：`9833db2b559501cd6caeff6299dfa289cd053ed0`；Cargo 版本 0.2.8；AGPL-3.0-only。
- `src/settings.rs:46`：全局、每账号并发默认均为 0（不限制），队列等待超时默认 30 秒。需要手动设置，不能开箱假定已有保护。
- `src/upstream/http.rs:403`：全局与每账号 semaphore，并支持等待超时；`:444` 的等待计数清理使用 guard。
- `src/routes_v1.rs:478`：把并发 permit 移入流式生成器，持有到流结束或 drop。直接对应当前生产缺少每账号流式并发限制的问题。
- `src/pool.rs:116`、`:1267` 附近：调度考虑账号并发占用，忙账号排序靠后。
- `tests/concurrency_waiting.rs`：含取消等待后的清理与队列超时测试。本次只读，未执行。
- `src/config.rs:214`：账号限流冷却默认 10 秒，可配置；默认短冷却不是官方恢复保证，不建议盲目照搬。
- `src/stream_anthropic.rs:53`：提取上游 usage/metering 的部分缓存字段。未证实对我们账号返回的数据格式能产生非零缓存，也未确认与现有模拟计量等价。
- 建议作为隔离对比测试第一候选，重点验证每账号并发 1、全局并发 2，随后验证客户端断流、工具调用、代理路径、缓存字段及 Sub2API 账单。需要进一步检查等待队列总量上限与饱和选点竞争行为。

## AIClient2API

后续重点审查与本地隔离复现见 [AICLIENT2API-REVIEW.md](AICLIENT2API-REVIEW.md)：已复现并发槽交接超限、可选节流与流式网络重试死锁、包装器取消未传递。下述最初静态筛选应结合该报告阅读，不应直接按候选功能表迁移。

- 仓库：https://github.com/justlovemaki/AIClient2API
- commit：`4c4c774930c85f3760e9c89a1ef481f74eac4d00`；GPL-3.0。
- `src/providers/provider-pool-manager.js:903`：每节点 concurrencyLimit、queueLimit、排队超时，默认未配置 concurrencyLimit 时不限并发；队列满返回 429。
- `src/utils/common.js:1318`：流式处理 finally 释放号池并发槽，包含失败换节点路径。
- Kiro adapter 有 429 等待/换凭据处理，但不是增加上游实际容量的机制。
- 重要取舍：`src/providers/claude/claude-kiro.js:3357` 的最终流式 usage 明确填写 cache_creation_input_tokens=0 和 cache_read_input_tokens=0；不能宣传为解决当前缓存计费需求。此结论针对 Kiro adapter 当前直接输出路径，其他插件/后处理未穷尽审查。

## TsinHzl/kiro2cc-proxy

- 仓库：https://github.com/TsinHzl/kiro2cc-proxy
- commit：`d3a84a6ed4114f752d8e60aa0fe704166dd04eb8`；MIT。
- 包含账号池、RPM 等待/故障转移和缓存兼容，功能接近当前 Rust 项目。
- `src/kiro/provider/core.rs:30`：全局并发常量 50、每账号 20，不适合直接套用到当前三个账号。
- 关键局限：`src/kiro/provider/retry.rs:111` 获取账号 permit，但成功后在 `:228` 返回 `(reqwest::Response, id)`，局部 permit 随函数退出释放。此路径保护到响应头返回，不覆盖后续整个响应流。因此“有 semaphore”不能当作流式并发问题已解决。
- `src/cache/mod.rs:18`：缓存选择依次采用上游字段、前缀估算、指纹命中、比例兜底；不是所有缓存字段都是真实上游命中。

## 其他已看项目

- https://github.com/jwadow/kiro-gateway ，commit `a5292ca04c7c6231e0b47673ac3f981f5a706e1e`，AGPL-3.0。HTTP client 有 429 指数退避；本轮未确认满足多账号全流生命周期并发控制。可作基础网关参考。
- https://github.com/caidaoli/kiro2api ，commit `a2837e91d0f93d1f340ce43911c31b04c257c0d8`，MIT。README 明确把并发控制写为 Token 刷新并发控制，缓存写为 Token 缓存；不能据此认为有推理请求排队或 prompt cache 优化。本轮不优先迁移。

## 选择结论

当前最有针对性的候选是 kiro-lb，其全流生命周期并发控制有具体源码支持。AIClient2API 更适合多供应商池，但缓存读写为零的取舍影响当前需求。kiro2cc-proxy 可以继续研究，但按当前版本不能视为已经解决流式单账号并发。

先隔离验证再决定是否替换，不能把开源项目的“高并发”宣传理解为绕过 Kiro 上游账号限流。若复用源码，应保留并遵守对应许可证；尤其不要直接把 AGPL/GPL 代码作为无许可证约束的片段移植。
