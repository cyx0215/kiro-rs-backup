# 项目交接：kiro-rs

更新日期：2026-10-10（Asia/Shanghai）。这是部署讨论的脱敏摘要及文档索引，不是完整聊天逐字稿。

2026-10-12 补充：按用户授权完成 Grok 离线审查后的监控修复：并发回退配对保留全部候选、歧义/未配对人工复核、无账单才提示路由、ID降序采集、明确图片/搜索字段跳过未覆盖公式、来源原因变化重报、诊断截断覆盖标志、状态故障保留报告、SSH凭据环境隔离及旧入口兼容适配。40项离线回归、旧测试入口和新旧CLI报告一致性验证通过；未访问线上或发布。旧采集包装文件不能直接作为远端源码，需在本机用 `--emit-source` 生成独立源码。用户已取消 `kiro` 监控任务，本轮未恢复定时任务。使用方法与边界见[监控工具说明](monitoring/billing/README.md)和[操作文档](docs/deployment/2026-10-10/BILLING-MONITOR.md)。

2026-10-10 23点后补充：新增独立只读计费监控工具 `billing_monitor.py`，默认8993与Sub2API账号94，输出中文HTML/JSON、费用分项/倍率核验、token推断配对及去重告警。SSH采集和20条真实Sub2API页面脱敏响应联调通过；CLI认证全量API采集尚缺运行环境凭据，现有`kiro`自动化未改动。监控规则、使用方法及验证边界见[计费监控工具](docs/deployment/2026-10-10/BILLING-MONITOR.md)。工具不修改线上配置或发推理请求。

2026-10-10 23:01补充：同文text对象/字符串缓存签名修复已重新构建并部署至8993独立测试容器，镜像ID `sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a`。部署前检查到无活动上游连接；部署后管理页200，测试与生产公网模型入口无密钥均401，生产8992镜像未变。新镜像下已有一条成功Opus自然调用，但诊断标记`first_observation`，不能据此判断缓存修复效果；仍待同身份连续调用及Sub2API费用核对。镜像校验和、回滚及验证边界见[测试部署记录](docs/deployment/2026-10-10/BILLING-TEST-DEPLOYMENT.md)和[缓存计费排查](docs/deployment/2026-10-10/CACHE-BILLING-DIAGNOSIS.md)。

2026-10-10 21:04补充：账号 #94 已接入8993独立计费测试实例，生产8992仍用上游发布镜像。已确认真实请求中同文text对象/字符串切换造成模拟缓存签名误判；修复已通过794项Rust测试并构建上传，尚未启用。用户仍在调用，明确要求先等，不能自行重启或趁短暂空闲部署；等用户表示可切换后再检查空闲窗口。当前镜像、证据及回滚见 [测试部署记录](docs/deployment/2026-10-10/BILLING-TEST-DEPLOYMENT.md) 和 [缓存计费排查](docs/deployment/2026-10-10/CACHE-BILLING-DIAGNOSIS.md)。下方10月9日部署和故障描述为历史基线，实时状态须重新核验。

## 项目目标与代码

使用 ZyphrZero/kiro.rs 作为 Kiro 账号池和上游接口，由另外一台机器上的 Sub2API 分发。用户希望在本地开发、测试后发布，并保留部署证据。

- 本地目录：`E:\cursor2\kiro-rs`。
- 上游：https://github.com/ZyphrZero/kiro.rs 。当前 origin 指向上游，尚未配置用户自己的 fork。
- 当前检出：`f1153704a8a10321d695e7d8aecb123d8eb64f39`，对应本次审查的 v0.9.1。
- 当前生产是发布者预构建镜像；没有发布自己的源码修改。
- 此聊天开始于 C 盘工作目录，随后将源码复制到 E 盘。复制源码不会复制聊天归属或完整聊天上下文。

## 当前部署基线

- VPS：`107.174.53.6`；已配置 SSH 别名 `racknerd-deploy`，登录 deploy 用户。此前没有免密 sudo。
- 公网 API：`https://107.174.53.6`；Claude Code Base URL 追加 `/cc`；后台 `/admin`。
- 公网 Caddy 转发至宿主机 `127.0.0.1:8992`；新版容器 `kiro-rs-zyphrzero-test` 虽保留 test 名称，实际承担生产流量。
- 新版数据目录：`/home/deploy/kiro-zyphrzero-test/data`；Compose 在其父目录。
- 原 `kiro-rs` 容器（8990）已停止并保留，切换时沿用原生产 API Key。
- 公网管理页有两层认证：Caddy 的 manager 用户及入口密码；应用内新版 adminApiKey。不要混用 API Key 和管理员密钥。
- `http://127.0.0.1:8992/admin` 是 Windows SSH 隧道入口，是否可访问取决于隧道当前状态；与公网的浏览器登录状态独立。
- 全局 HTTP 代理已配置，曾实测出口 `198.63.254.19`。认证秘密仅在服务器配置中。

## 已验证与限制

- Sonnet 5.5 公网流式调用成功；Sonnet 4.5 的 Claude Code 兼容接口成功；追踪中也有 Opus 5.5 成功记录。
- 重复相同会话、前缀和显式 cache_control 能返回缓存写/读字段，但实测 usage_source 为 simulated。不能宣称真实上游缓存命中或据此保证实际成本降低。
- 尚未验证另一台 Sub2API 服务器的最终缓存账单。
- 未查到官方固定账号并发上限；并发 1 只是建议的保守起点，并未由本次对话落实到分发端。
- 曾查到 Pro 账号 overageCapable=true；超额开关后来从 false 变为 true，本次排查没有修改。当前状态需重新查询。

## 最近故障与用户当前决定

2026-10-09 北京时间约 04:05，唯一启用的 Pro 账号返回 429 USER_REQUEST_RATE_EXCEEDED，触发反代配置的 1800 秒冷却；另一个账号原本手动停用，导致无可调度账号。后续“所有凭据均已禁用”报错混淆了临时冷却和永久禁用。请求存在重叠，但无法确定上游具体阈值或唯一触发条件。不要把当时推算的 04:36:43 冷却截止时间当作当前状态。

公网管理页曾重复弹 manager 登录框，同时 Chrome 填密码触发 Windows PIN；后来用户确认恢复。只证实公网 Basic Auth 挑战与本机管理鉴权正常，浏览器侧根因未被完整捕获。

2026-10-09 再次排查，通过用户重试和 Caddy 脱敏日志捕获：manager 认证成功后，后台配置接口因应用管理员密钥无效或缺失返回 401；随后浏览器请求不再携带 Basic 认证并被重新挑战。已在公网 Caddy 管理 reverse_proxy 内将上游 401 映射为 403，保持入口 Basic 挑战及模型 API 路由原样；备份和验证见重复登录弹窗记录。临时诊断日志已关闭。用户需在网页内退出失效保存状态并用当前新版 adminApiKey 重新登录，浏览器最终恢复仍待确认。

源码审查提出窗口计量、缓存提交时机、Key 随机源和预算约束问题。用户明确要求“暂时先不弄这个”，之后转为排查调用失败。**不要未经新指令自动开始这些改动或发布。**

## 详细记录

- [部署记录索引](docs/deployment/README.md)
- [部署、验证及回滚手册](docs/deployment/2026-10-09/README.md)
- [现场脱敏证据](docs/deployment/2026-10-09/evidence/verification.json)
- [源码审查与待办](docs/deployment/2026-10-09/SOURCE-REVIEW.md)
- [账号限流故障记录](docs/deployment/2026-10-09/INCIDENT-0405-RATE-LIMIT.md)
- [公网管理页重复登录弹窗排查](docs/deployment/2026-10-09/INCIDENT-ADMIN-BASIC-AUTH.md)
- [开源网关备选比较](docs/deployment/2026-10-09/ALTERNATIVES.md)
- [AIClient2API 重点审查及隔离复现](docs/deployment/2026-10-09/AICLIENT2API-REVIEW.md)

这些文档创建时尚未提交或推送 Git；接手时检查 git status。新部署或排障结果继续留存到 docs/deployment，区分实测、推测和未验证项。
