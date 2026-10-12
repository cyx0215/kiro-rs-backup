# Kiro 反代提示词精简调研

记录时间：2026-10-12 18:20（Asia/Shanghai）。本轮范围：本地源码、公开源码和官方文档的只读调研；另做一次脱离服务的 Rust 纯函数复现。未访问线上实例、未发推理请求、未改应用源码或运行配置、未部署/重启/push。

## 结论

可以精简**我们能够看到的请求内容及反代自行添加的规则**。本轮未发现能关闭或删除 Kiro 服务端内置系统提示词的、经过验证的 API 开关。删除客户端 system、增加“不要自称 Kiro”的文字、切换 origin/端点，都不能直接证明服务端提示词被删除。

建议先缩短和按工具条件注入本项目的分块策略，再提供默认关闭、可预览的精确过滤机制。不要直接照搬按 Claude Code 关键词整段替换 system 的实现。

## 证据边界及版本

- **源码核验**：kiro-rs 本地 HEAD `74d8492e6687f69c15e961432fea29b55fc8b928`。工作区已有代理热更新等未提交内容，未覆盖；这不是线上版本证明。
- **源码核验**：kiro-account-manager 本地 HEAD `41f48ed43b7a772a379e8ef04efbeb139e5fc20e`，存在大量未提交内容。下述两个关键文件用 `git diff --ignore-space-at-eol` 检查无实质差异；并核对公开版本 `844aac76a2763d8da2168dbb51932c6087bdf5bf` 的过滤器和清洗函数，关键逻辑一致。
- **离线实测**：直接抽取账号管理器原始 `CLAUDE_CODE_BACKEND_PROMPT` 常量和 `clean_system_prompt` 函数，以 `rustc` 编译临时程序并断言：326 字节短提示经清洗后为 0 字节。没有运行网关或发送网络请求。此测试只验证两段逻辑的组合，不是端到端模型测试。
- **未验证**：线上当前过滤配置、Kiro 服务端真实提示全文、移除/缩短后的模型效果和实际信用点节省。

## 三层提示词需要分开看

| 层次 | 内容和来源 | 本项目能做什么 |
| --- | --- | --- |
| 客户端传入 | Claude Code 的 system、用户附加约束、工具描述；Kiro 客户端显式传入的上下文（如有） | 可过滤，但需区分模板和用户规则，不能因为出现 Kiro/Claude Code 字样就删除 |
| 反代添加 | 分块写入策略、工具描述后缀、thinking 标签、兼容历史消息 | 可直接修改；首先处理这一层 |
| Kiro 服务端 | 可能由上游加载的身份、行为或工具规则 | 请求体中不可见的内容无法用本地字符串过滤器删除；本轮没有核验到关闭开关 |

Kiro 官方 [Steering 文档](https://kiro.dev/docs/steering/) 说明 `.kiro/steering/` 和 `~/.kiro/steering/` 为客户端提供项目/全局知识。这支持“存在客户端上下文”的区分，不证明我们的反代会读取这些目录，也不证明其内容必然随所有请求发送。本项目检查范围内没有自动载入一整份官方 Kiro 长 system 的实现。

## 本项目实际添加什么

位置：`src/anthropic/converter.rs`。

- 183–196 行：Write、Edit、Bash 后缀，以及 `SYSTEM_CHUNKED_POLICY`。
- 1531 行起：ClaudeCode 兼容模式使用内置工具描述，其中写入/编辑/命令工具带后缀；1661 行起的转换逻辑在其他模式保留原描述并按原名追加后缀。
- 1773 行起：传入 system 非空时，将其拼接后追加全局分块策略，再包装成历史 user 消息，随后添加 `I will follow these instructions.` 的 assistant 配对。全局策略没有检查是否存在 Write/Edit 工具。
- 1722 行起：按 thinking 请求生成控制标签。它们关联功能，不能作为一般噪音直接删除。

按 Rust 字符串续行规则去除源码转义和换行后，四段固定英文文本的长度为：

| 文本 | ASCII 字符数 |
| --- | ---: |
| Write 后缀 | 312 |
| Edit 后缀 | 280 |
| Bash 后缀 | 314 |
| system 分块策略 | 237 |
| 合计 | 1,143 |

合计适用于非空 system 且包含三个对应工具的请求，未计拼接分隔符。字符数不是 tokenizer 计数，也不是每条请求都能减少这么多。当前没有证据能宣称精简这里就省掉数千 tokens。

本地 Kiro 请求类型没有独立的 Anthropic system 字段；这里是把 system 放入 user/history 的兼容转换。客户端字段名叫 system，不代表经过这条链路后具有与 Anthropic 原生 system 相同的优先级。

端点层核验：`src/kiro/endpoint/ide.rs:108` 主要补 profileArn；`src/kiro/endpoint/cli.rs:126` 调用转换，将 `AI_EDITOR` 换为 `KIRO_CLI` 并调整少数结构字段。未看到清除服务端提示词的逻辑。

## 参考项目比较

| 项目 | 源码中实际做法 | 可借鉴程度 |
| --- | --- | --- |
| kiro-account-manager | 可配置 Claude Code 整段替换、环境段删除、边界标记删除、自定义正则/按行规则；payload 还会无条件调用另一层清洗 | 借鉴规则开关；不直接照搬整段替换和双重清洗 |
| hj01857655/Kiro-Go | 同类过滤器，并提供参数化过滤与预览入口使用的函数；另有前后追加提示块 | 借鉴预览和配置快照设计；追加块不是减少上游提示 |
| jwadow/kiro-gateway | 将超长工具说明移入 system，并按开关追加 thinking、截断恢复说明 | 是兼容处理；搬移工具说明不等于减少总输入 |
| AIClient2API | 在请求前追加身份覆盖、禁止自称 Kiro及分段写入规则 | 增加提示词，不能作为已删除服务端 system 的证据 |

### 账号管理器的具体注意点

1. `prompt_filter.rs:38`：开启 `filter_claude_code` 后，命中至少两个特征就**全量替换该 system 消息**，并立即返回。若该消息同时包含调用方自定义工作约束，约束也会丢失；自定义过滤规则在这一分支不会继续执行。
2. `prompt_filter.rs:96`：环境过滤会删除 `# Environment` / `# auto memory` 段，直到下一个一级标题。环境和记忆可能含任务必需信息，不宜统一视作噪音。
3. `proxy/upstream.rs:199`：开关过滤作用于归一化后 role 为 system 且 content 为字符串的消息，不是对所有发往上游的文本进行清洗。
4. `converter/payload.rs:259`：又调用 `clean_system_prompt`，该函数不是由上述三个开关统一控制。它会去掉时间戳、特定标签块，以及五句 backend 短提示。
5. 因此开启 Claude Code 整段替换且命中后，会出现“原 system → 326 字节短提示 → 空文本”的组合。离线纯函数实验已证实最后一步；后续仍可能添加工具说明和 thinking 标签，不能把整个最终请求说成空。
6. 三个配置开关默认 false（`gateway/mod.rs:468`），所以不能说默认就触发第 5 点。但无条件清洗确实存在。
7. 删除边界标记后，payload 在 292 行起又包装新的 system 边界标记；这不是彻底取消边界，更接近去重/重包装。

## 建议落地方向（尚未实现）

1. **先管理反代自身规则**：把分块策略独立为保守/精简配置；无相关工具时不加写文件规则。精简文案保留当前阈值，首轮不同时修改阈值和工具映射。去掉重复解释和“不能向用户解释/不能询问切换”等全局行为限制，需要单独作为行为变化评估。
2. **调用方内容默认保留**：保留用户约束、项目规则、工具参数说明和 thinking 控制。只有能够确认来源和边界的重复模板才允许精准去重；不按“Kiro”关键词批量删除，不默认清空整个 Claude Code system。
3. **先预览再启用**：离线/本机预览保留原请求副本，显示哪些规则命中、前后字符数及估算 token 差；正常留证仅记录规则编号、长度、版本和哈希，不保存原始用户提示。
4. **统一实际请求与计量**：当前 handlers 在转换后仍用原 payload 进行输入估算（`handlers.rs:919` 附近）。若精简客户端 system，需要明确原始输入、实际发送和计费估算的口径，并为策略版本隔离/失效模拟缓存。不能只改出站文本，再直接把原计量下降或模拟缓存字段视作实际成本收益。这是本功能的接入要求，不是恢复已搁置的其他计费改造。
5. **验证分两阶段**：离线检查普通对话、含项目约束、工具往返、thinking、自定义/MCP 工具、长描述、重复包装和过滤幂等性；另行获得本次真实推理授权后，再在测试实例做同模型、同请求的行为对照。观察工具成功率、截断、输入/输出量和实际 credit；短输入可能导致更多工具调用，总成本不一定降低。

减少客户端可见字数、改善模型行为、降低实际 credit 是三个不同目标，应分别验证。不要用模型自述身份作为“服务端提示词已被移除”的证明，也不要承诺未经测量的节省比例。

## 公开源码定位（本轮抓取的固定版本）

- [账号管理器过滤器](https://github.com/hj01857655/kiro-account-manager/blob/844aac76a2763d8da2168dbb51932c6087bdf5bf/src-tauri/src/gateway/prompt_filter.rs#L14)
- [账号管理器二次清洗](https://github.com/hj01857655/kiro-account-manager/blob/844aac76a2763d8da2168dbb51932c6087bdf5bf/src-tauri/src/gateway/converter/content.rs#L317)
- [Kiro-Go 过滤与预览共用函数](https://github.com/hj01857655/Kiro-Go/blob/d035fe81bf3c59ad34ce20831bb6204adc7294ae/proxy/translator.go#L379)
- [kiro-gateway 拼接提示词](https://github.com/jwadow/kiro-gateway/blob/a5292ca04c7c6231e0b47673ac3f981f5a706e1e/kiro/converters_core.py#L1441)
- [AIClient2API 内置身份前缀](https://github.com/justlovemaki/AIClient2API/blob/03d90e9123f75a643768f5e4e19d49598f1ac330/src/providers/claude/claude-kiro.js#L1329)

公开源码注释作为实现背景读取，不等同于 Kiro 官方协议保证。未复制第三方实现到本项目。
