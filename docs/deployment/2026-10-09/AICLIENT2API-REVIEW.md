# AIClient2API：针对当前 Kiro 账号池的审查

审查日期：2026-10-09（Asia/Shanghai）。仓库：https://github.com/justlovemaki/AIClient2API 。本次远端 main 与本地检出均为 `4c4c774930c85f3760e9c89a1ef481f74eac4d00`；package 版本 3.0.0；GPL-3.0。

## 结论

它有值得借鉴的每节点并发、队列、负载评分、429 冷却及流式异常处理，适合作为隔离测试和二次开发候选。但本次复现了并发槽交接竞态、开启 Kiro 节流后的网络重试死锁、流式包装器取消未传递三项问题。当前版本不建议直接替换生产。

其 Kiro 流式输出的缓存读写字段仍为 0，不满足我们希望在 Sub2API 获得缓存计费字段的需求。当前生产的模拟缓存字段也不能视为真实上游缓存命中；两者不能混为一谈。

本次只读源码及执行本地隔离测试，未启动完整服务、未调用真实 Kiro 账号、未更改 VPS、代理或 Sub2API。没有对官方账号并发上限或生产吞吐作出保证。

## 已核验能力

| 能力 | 源码证据与实际边界 |
| --- | --- |
| 每账号并发、有限队列 | `src/providers/provider-pool-manager.js:903` 的 `concurrencyLimit`、`queueLimit`；UI `static/app/modal.js:1715` 可填写。未配置并发为 0，即不限；队列为 0 表示并发满后不排队。 |
| 调度考虑繁忙程度 | `provider-pool-manager.js:594` 将满并发节点降优先级，考虑队列、活跃数、健康状态及使用顺序。不能等同于三个账号必然各分到一个请求。 |
| 通用流式槽生命周期 | `src/utils/common.js:1318` 在处理器 finally 释放号池槽；下面记录了内层取消和递归重试的边界。 |
| 429 冷却 | `common.js:308` 解析 Retry-After，`common.js:1228` 标记恢复时间；`src/core/config-manager.js:124` 默认关闭。默认回退 30 秒、最大 300 秒、随机抖动 5 秒。上游给出更长 Retry-After 时会被上限截断，不能认为完全遵守上游恢复时间。 |
| 空响应与流中断处理 | `claude-kiro.js:3383` 延迟转发 message_start，`common.js:1169` 空响应有限重试；`:1150` 已发数据后不整体重放；`claude-kiro.js:2760` 内层网络重试也检查是否已产出内容。 |
| 新模型映射 | `claude-kiro.js:273` 包含 Sonnet 5.5、Opus 5.5 的连字符到点号映射，`:214` 对应上下文表为 1M。这是源码映射支持，不是本次真实账号可用性测试。 |
| 代理 | `src/utils/proxy-utils.js:247` 接入 sidecar 或 Axios 代理；Kiro 推理、刷新相关路径调用 `_applySidecar`。普通代理配置为 `PROXY_URL`，并在 `PROXY_ENABLED_PROVIDERS` 加入 `claude-kiro-oauth`。本次未实测出口。 |
| Sub2API 对接路径 | README-ZH.md:481 示例为 `/claude-kiro-oauth/v1/messages`，候选 Base URL 追加 `/claude-kiro-oauth`；应实际验证 Sub2API 的拼接、流事件及用量解析。 |

## 本地复现的问题

复现文件：[测试脚本](evidence/aiclient2api-concurrency-repro.cjs)、[JSON 结果](evidence/aiclient2api-concurrency-repro.json)。Node v24.19.0，无第三方依赖。脚本校验 commit，抽取未修改的方法，通过 VM 执行；选择器底层和网络为替身。结果包含抽取源码 SHA-256。它验证特定控制流缺陷，不是完整应用压测，也不表示我们当前 Rust 服务的故障由这些缺陷导致。

### 1. 并发设置为 1，仍可同时放行 2 个请求

位置：`provider-pool-manager.js:903`、`:975`。

触发顺序：A 占槽；B 排队；A 释放时先把 activeCount 减为 0，再用 setImmediate 唤醒 B；在 B 获得执行前，C 看到空槽并进入；B 恢复后再把 activeCount 加 1，最终为 2。

测试使用原始 acquireSlot、releaseSlot、selectProvider 方法，只将底层选择固定到一个虚拟账号。配置 concurrencyLimit=1、queueLimit=2，实测 activeCount=2。默认未启用 Kiro 全局节流时，没有那层串行化来掩盖此问题。

修复方向：释放时直接把占用权交给等候者；只有无人等待时才降低占用数。选号与保留槽位要作为一致操作处理，并测试新请求插队、队列超时和取消。

### 2. 开启 Kiro 请求间隔，网络重试会等待自己

位置：`claude-kiro.js:63`、`:163`、`:2638`、`:2765`、`:2781`。

`KIRO_REQUEST_MIN_INTERVAL_MS>0` 启用一个模块级 Promise 队列。它没有按账号分组，并且流式请求持有队列锁到结束。因此在该进程中参与此节流的 Kiro 请求会整体串行，并非每账号独立设置请求间隔。

首段内容出现前遇到可重试网络错误时，catch 内递归执行 streamApiReal；外层的 releaseThrottle 位于 finally，要等递归返回才执行；内层又等待外层的锁，形成循环等待。

测试注入一次 ECONNRESET：节流关闭时两次请求完成并产出 ok；节流为 1ms 时第二轮请求体已经构建，但第二次网络调用无法进入，100ms 观察窗口内无输出。等待关系本身没有释放路径，结论不仅依据耗时。

修复方向：每次网络尝试独立 acquire/release，重试前释放，使用循环取代持锁递归；按账号实现并发和起始时间间隔，等待需要超时及取消。这个开关默认未配置则关闭，不能说所有默认请求都会死锁。

### 3. 客户端结束外层流，没有关闭内层生成器

位置：`claude-kiro.js:3383`。

generateContentStream 手动调用 rawGenerator.next()，但没有 finally 调用 rawGenerator.return()。隔离测试读取第一帧后对外层 return，内层 finally 未执行；显式对内层 return 后才清理。

实际风险：客户端断开时，通用处理器可能释放了号池槽，但内层请求/stream/throttle 尚未清理；开启节流时尤其可能堵住后续请求。未在真实 HTTP 链路测量 socket 存活时间，不能宣称每次断线都会永久泄漏。

修复方向：外层 finally 传播 return/cancellation，网络请求接 AbortSignal，验证中途取消后活跃数、网络连接、节流锁全部回收。

## 其他需要验证的边界

- 排队默认等待 300000ms（5 分钟），全 src 搜索没有其他 queueTimeout 传入点。可能比下游超时长；队列等待函数未接客户端 AbortSignal。
- `common.js:1269` 在旧槽释放前获取备用槽，并递归处理新请求；旧槽直到递归结束才释放（`:1321`）。需要验证账号全忙或只剩一个健康账号时是否等待自己的槽，避免无效占槽放大拥塞。这一点仅静态分析，未完整复现。
- 冷却默认关闭时，Kiro 429 设置 skipErrorCount，并不保证把该账号从调度中暂时摘除。项目具备切换重试代码不等于不会反复撞限流。
- 单进程内存状态不会自动在多实例之间共享。扩容服务实例前需要确认同账号的总并发由谁控制。

## 缓存与费用

`claude-kiro.js:2944` 和 `:3357` 将 cache_creation_input_tokens、cache_read_input_tokens 写为 0。输入 token 在 `:3340` 根据 contextUsagePercentage 及上下文表估计，缺失则使用本地估算；不能当作完整真实上游账单。

当前审查范围为 Kiro 内置输出路径，未穷尽第三方插件。若 Sub2API 按这两个缓存字段计费，直接换到这个路径不会产生所需的非零缓存折扣；仍要在分发端核验最终账单。即使另外实现缓存模拟，也只能标注为本地估算，不能称上游真实命中。

## 建议次序

1. 保持现有生产入口，先把三项复现转为修复后的回归测试；增加有限排队、断开取消、失败前释放槽和 Retry-After 边界测试。
2. 隔离候选实例从每账号并发 1、很小的有界队列开始；全局先以用户已观察较稳的 2 为测试起点。这是试验值，不是官方限制，也不保证不会 429。
3. 不直接照抄默认 30 秒冷却或开启有死锁问题的间隔开关。按上游返回和实际日志调整，避免多层重复重试。
4. 用不同会话及相同会话分别测并发 1/2/3、长流、主动断流、队列满、冷却恢复、工具调用、代理刷新和缓存字段。不要同时让两个实例高频使用同一账号。
5. 由 Sub2API 发实际请求检查账单，再决定是否迁移。若保留现有 Rust 项目，参考控制思路实现相同保护，避免为多供应商功能承担额外迁移成本。

## 固定版本源码链接

- [账号槽及排队](https://github.com/justlovemaki/AIClient2API/blob/4c4c774930c85f3760e9c89a1ef481f74eac4d00/src/providers/provider-pool-manager.js#L903)
- [Kiro 节流](https://github.com/justlovemaki/AIClient2API/blob/4c4c774930c85f3760e9c89a1ef481f74eac4d00/src/providers/claude/claude-kiro.js#L163)
- [流式递归重试](https://github.com/justlovemaki/AIClient2API/blob/4c4c774930c85f3760e9c89a1ef481f74eac4d00/src/providers/claude/claude-kiro.js#L2757)
- [缓存字段及包装器](https://github.com/justlovemaki/AIClient2API/blob/4c4c774930c85f3760e9c89a1ef481f74eac4d00/src/providers/claude/claude-kiro.js#L3350)
- [冷却计算](https://github.com/justlovemaki/AIClient2API/blob/4c4c774930c85f3760e9c89a1ef481f74eac4d00/src/utils/common.js#L308)
