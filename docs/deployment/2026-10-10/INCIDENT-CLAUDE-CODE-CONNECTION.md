# Claude Code 连接中断排查

采集于 2026-10-10 约 13:46–13:55（北京时间）。只读检查本地指定项目会话、配置及 VPS 运行状态、追踪和分类日志。未修改服务、账号或客户端配置，未发起模型推理。

## 已证实

- 本机 Claude Code 用户配置的 ANTHROPIC_BASE_URL 为 https://02acc.itssx.com/；项目配置未发现该字段覆盖。运行进程的环境覆盖及分发网关路由未核验。
- 截图三个子 agent ID 与本地会话文件匹配。实际错误时间早于用户估计的 12–13 点。
- 11:13:39、11:25:29，一个子 agent 收到 Cloudflare 524；错误正文明确为 120 秒 Proxy Read Timeout。
- 11:36:16、11:38:37（两个子 agent，相差 12ms）、11:40:06、11:43:43，记录 API Error: Connection dropped (ECONNRESET)。未将会话中的普通说明文字当作底层错误证据。
- 生产 kiro-rs 容器 running、restart_count=0、OOMKilled=false；Caddy API 路由 flush_interval=-1。最近三小时生产日志分类未发现 429、reset 或 timeout。
- kiro-rs 11–12 点开始的 96 条请求全部 final_status=success，12–13 点开始的 6 条请求同样成功。服务端 success 不证明最终客户端完整收到响应。
- 11:11:35 开始的请求 first_token_ms=137946、duration_ms=249795；11:23:24 开始的请求 first_token_ms=131139。其开始时间加 120 秒与客户端两次 524 时间接近，支持中间代理超时解释；没有跨系统 request ID，尚非逐请求证明。

## 判断和边界

已确认存在 Cloudflare 回源超时，也存在连接重置。并发可能通过增加排队和上游响应延迟加重问题，但没有证据证明此次为 Kiro 账号并发限流。不能将所有 ECONNRESET 都归因于 524：分发网关、客户端代理或共享连接故障仍需核验。

本地源码普通流和 /cc 缓冲流在获得上游响应头后每 25 秒发 ping，等待响应头阶段未开始下游 SSE。源码只作为排查线索，未验证线上镜像与当前本地修改完全一致，亦未核验分发网关是否传递 ping。

建议先单 agent 与原多 agent 场景对照，核验分发端并发、排队与首字节超时；确认长请求的 SSE 及时发送且心跳贯穿所有代理。对 524 场景可评估 API 专用入口绕过 Cloudflare代理或调整受支持的超时策略，保留 TLS 和鉴权。仅增加 Claude Code 本地超时无法改变 Cloudflare 的回源超时。

原始会话正文、密钥、认证头、客户端 IP 和带敏感信息日志未写入此记录。未修改线上路由或尝试绕过现有认证。
