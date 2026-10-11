# Kiro 测试计费监控工具

第一版：2026-10-10。本工具默认只读监控 `racknerd-deploy` 上的 **8993 测试实例**，对照 Sub2API **账号 #94**。正常发布版仍使用 8992。运行工具不会重启容器、改价格或配置、发推理请求，也不会自动修账。

## 文件与数据流

- `monitoring/billing/src/billing_monitor/cli.py`：命令行入口、Sub2API GET 接口、对账、告警去重，以及中文 HTML/JSON 报告。
- `monitoring/billing/src/billing_monitor/collector.py`：入口通过 SSH 的标准输入发送到服务器执行，不安装服务；SQLite 以 `mode=ro` 打开，在同一读取事务中获取 trace 与 attempt 状态。只传回白名单字段。
- `monitoring/billing/tests/test_billing_monitor.py`：标准库 unittest 回归测试，使用合成 SQLite、API 响应及日志，没有线上写入或模型调用。

`docs/deployment/2026-10-10/billing_monitor.py`、`billing_monitor_collect.py` 和
`test_billing_monitor.py` 保留为兼容入口，实际实现只维护在 `monitoring/billing/`。

采集兼容入口只在本机转发，不可把包装文件源码直接发送至远端 `python3 -`。
旧 SSH 标准输入调用方应在本机执行 `billing_monitor_collect.py --emit-source`，
从标准输入传入 JSON 参数对象（`since`、`until`、`db_path`、`max_rows`、`container`），
再把标准输出的完整独立源码交给远端 `python3 -`。新版 CLI 已自动采用正确路径。
兼容测试入口支持直接指定测试类及方法名。

需要本机 Python 3.9+、SSH，服务器 Python 3.9+，以及 deploy 用户现有 SQLite 和 Docker 读取权限。不需要 pip 安装依赖。默认查询最近 30 分钟，也支持固定起止时间；两者都以 `[开始, 结束)` 为范围，trace 使用 **开始时间 + duration_ms**，Sub2API 使用 **created_at**。跨窗口开始、窗口内完成的长请求仍纳入；尚未完成的请求不在 trace 中。

## 先看测试服务器

在 `E:\cursor2\kiro-rs` 执行：

```powershell
python -B monitoring/billing/src/billing_monitor/cli.py --traces-only --lookback-minutes 30 --output .local-test/billing-monitor/latest.json --html .local-test/billing-monitor/report.html --quiet
```

双击 `.local-test/billing-monitor/report.html` 查看报告。它是每次运行生成的**静态快照**，不是常驻后台或自动刷新的网页。HTML 使用本地样式，不加载外部资源。`--traces-only` 明确表示只看测试端，Sub2API 显示“未接入”，不将 USD 缺失当作零费用。

## 接入 Sub2API 自动采集

默认只调用 `GET https://02acc.itssx.com/api/v1/admin/usage`，固定筛选当前账号 ID，按日期分页后再按精确时间过滤。已核对公开 v0.2.15（commit `f2669c8cf62555cd92389b3f55920e9e6e7c6ff2`）的请求参数、DTO 和账号费用公式；不修改 Sub2API。

凭据只从指定环境变量或隐藏的终端提示读取。**不从浏览器提取令牌、不把凭据写入命令行参数、报告、状态文件或仓库。** 浏览器已登录不代表命令行自动拥有认证信息。

使用已有管理员 API Key（`x-api-key`，不是普通模型调用 Key）：

```powershell
python -B monitoring/billing/src/billing_monitor/cli.py --auth-type api-key --token-env SUB2API_ADMIN_API_KEY --prompt-token --lookback-minutes 30 --output .local-test/billing-monitor/latest.json --html .local-test/billing-monitor/report.html --quiet
```

终端出现隐藏输入提示时输入凭据，勿贴进聊天或代码。无人值守时，由运行环境注入 `SUB2API_ADMIN_API_KEY`，去掉 `--prompt-token`。如果使用管理员登录 JWT，则选 `--auth-type bearer`，默认变量名 `SUB2API_ADMIN_TOKEN`；JWT 到期会报来源不可用，需要正常刷新。工具不会创建或更改任何管理员凭据，也不把现有管理员凭据变成权限受限的只读 Key；只读约束来自本工具仅实现 GET。

当前本机未配置上述凭据，所以 **CLI 的认证 API 联调与无人值守全量账单采集尚未启用**。已完成 SSH 实采、浏览器实际 API 响应脱敏样本联调，以及客户端分页/请求构造的隔离测试。不能把这些解释成完整自动采集已上线。

## 核对规则

2026-10-11 增补：采集 cache/context/usage 三类脱敏诊断。修复后 usage 诊断
中 `provider_usage=false`、`prompt_estimate >= 100000` 且
`prompt_estimate >= 2 * context_tokens > 0` 时提示复核模拟 token 分摊。
比较字段来自同一条诊断，不跨并发日志拼接；仅是估算口径差异线索，
不认定真实上游缓存成本或账单算费错误。缺少有效字段时不触发该规则，
也不代表计量验证通过。沿用事件去重，不恢复定时任务。
三类诊断合计最多保留 50 条，仍受日志尾部 5000 行覆盖范围限制。

| 项目 | 规则 / 范围 |
| --- | --- |
| 用户 USD | 四项费用之和与 `total_cost` 比较，再核对 `total_cost × rate_multiplier = actual_cost`；倍率使用每条记录快照 |
| 账号 USD | `COALESCE(account_stats_cost, total_cost) × COALESCE(account_rate_multiplier, 1)`；自定义账号定价不能拿用户费用公式代替 |
| Kiro credits | 独立汇总测试服务器所有 trace 的上游 credits；不转换为美元，不假定全部属于 #94 |
| 单价 | 默认由每项费用 / token 反推，仅描述该账单的有效单价。零 token 的单价留空，不能据此验证当前或历史价格配置 |
| 独立单价校验 | 可提供有生效时间、模型、分组的价格快照，按 token × 单价独立核验；未提供就明确标为 `derived_rates_only` |
| 跨系统配对 | 上游模型、完成时间（默认 ±10 秒）、四类 token 唯一相符；其次检查输出/缓存写/缓存读唯一相符但输入不同的线索 |
| 歧义 | 无共享 request ID，所有配对均标为推断；三类 token 回退用全部成功 trace 判断候选，即使其中一条已精确配对也不能据此制造唯一性。完整窗口有歧义，或同时存在未配对账单及完成超过两分钟的成功 trace 时，提示人工核对，不认定已确认差异 |
| 缓存 | 首次观测、高写入、TTL 到期均不自动判为缺陷；只有修复后同角色同文 text/string 在旧断点仍误判为变化，才报可能回归 |
| 故障 | 最近 15 分钟连续 3 次 error/interrupted、上游 attempt 的 429、已知无可调度凭据错误、镜像不符、容器停止或重启 |

金额使用 Decimal，默认容差 `$0.000001`。原始 API 费用适用此容差；手工舍入后的截图/Excel不是原始精度，需保留更精确数据或明确调整 `--money-tolerance`。非 token / 媒体计费不套用四类 token 公式，报告记录跳过情况。当前不覆盖所有下游错误、图片/视频计费或账单退款流程。

2026-10-12 增补：明确带有 `image_input_tokens` 或 `search_count` 的账单也标为
`unsupported_billing_mode`，保留金额汇总但跳过该行公式核验，避免把未覆盖的附加项误报为
四项费用差额。任意无法解释的差额仍触发公式告警，不自动归为附加费用。
这些字段的处理经过合成数据测试，未核验线上 Sub2API 的实际版本与附加费实现。

价格快照格式示例（以下是**合成格式示例，不是当前生产价格**；按实际历史规则填写，尤其注意长上下文阶梯、缓存 TTL 及模型映射）：

```json
{
  "rules": [{
    "model": "example-model",
    "group_id": 49,
    "from": "2026-10-10T23:00:00+08:00",
    "until": "2026-10-11T00:00:00+08:00",
    "usd_per_million": {
      "input_tokens": 4,
      "output_tokens": 20,
      "cache_creation_tokens": 5,
      "cache_read_tokens": 0.2
    }
  }]
}
```

用 `--prices-json <路径>` 加载；model 对应账单显示模型、group_id 对应分组，起止时间必须包含时区；重叠规则会告警。第一版一条规则只能描述四种平坦有效单价；分层/混合缓存期限若不能化为该记录的实际有效价格，不应提供猜测值。

## 完整性与告警

Sub2API 使用 ID 降序分页、`exact_total=true`、去重及默认 10000 行上限，使达到整日查询上限时优先保留较新的 ID。ID 不保证时间顺序，所以不会因某页时间早于窗口开始就提前停止。分页数量变化、重复页、空洞或上限阻止读完都标为不完整，不拿不完整数据下“全量一致”的结论。API 只接受 HTTPS origin，拒绝重定向，防止认证头被带到意外地址。

完整性是分页核验，并非跨多个 HTTP 请求的数据库事务快照；延迟落账需在后续有重叠时间的窗口重查。默认 30 分钟窗口适合现有每 15 分钟一次的巡检；每日汇总应重新查询完整固定窗口，**不要把重叠快照的计数和费用相加**。

事件告警（具体费用/输入差异）保存哈希去重，最多留 20000 条；连续故障按条件去重，条件消失后再出现才重报。报告保留全部当前告警，`new_alerts` 只包含本轮新提醒。状态默认位于用户目录 `.kiro-rs/billing-monitor-94-<来源哈希>-state.json`，来源、账号或在线/离线模式不同会自动分开。状态不含凭据、正文或原始日志。需要无状态复验用 `--no-state`；同一状态文件不要并发运行多进程。

来源故障的脱敏原因变化也会重新提醒，原因不变保持去重。状态版本/来源不兼容、结构损坏
或读写失败时，本轮仍保存 JSON/HTML 报告，返回 1，并提示指定新状态文件；不覆盖不兼容
或损坏的原文件。此时无法可靠去重，`--quiet` 也会输出状态故障提醒，需由用户处理。

`--quiet` 在无新告警时不打印，仍更新指定报告。不向邮件、聊天或 Webhook发送消息。用户已取消 `kiro` 自动化；本地工具仍可手动运行，本次没有恢复或新建定时任务。

退出码：

- `0`：采集成功且无新告警（可能仍有已通知的告警；`--traces-only` 也可能返回 0，不等于完整计费已通过）。
- `1`：数据源不可用、数据不完整或运行失败；同一问题的 `new_alerts` 仍会去重。
- `2`：采集完整且有新业务告警。

调度方应看 `new_alerts` 判断是否通知，不能看到 0 就认定无历史告警，也不能只因重复返回 1 就每轮重复通知。默认不因测试端有调用而 #94 无记录就宣称切流失败，因为测试端可能接收直连流量；只有明确确认全部测试请求应来自该账号时才加 `--expect-exclusive-account`。该规则还要求完整账单窗口内没有该账号记录、测试 trace 未截断且至少三条成功调用已完成超过两分钟；输出只是需检查路由和落账延迟的线索。有账单但无法唯一配对时仅提示人工核对。

诊断日志只在服务器内存中读取最多 5000 行，返回固定字段与最多 50 个缓存诊断事件。原始日志、请求/回复正文、trace 原始 ID、会话 ID、邮箱、IP、Key 等不落到本机报告。原始 API 响应中多余的用户/密钥关联对象在单页处理后丢弃。

读取日志达到 5000 行或三类诊断合计超过 50 条时，返回 `diagnostics_truncated=true`、覆盖
告警及退出码 1；仍展示已读到的线索，但不能证明未发现的缓存异常不存在。
启动 SSH 子进程前去掉 `--token-env` 指定变量及标准 Sub2API 管理员凭据变量，不影响
PATH、SSH agent 等普通环境；凭据仅由本进程用于 HTTPS 请求。

## 离线复验与本次证据

`--sub2api-json` 接受 `{ "items": [...], "total": N }` 或注明 `complete` 的脱敏导出；只给数组默认视作不完整。`--traces-json` 可加载采集器的脱敏输出，从而完全离线测试。外部导出的未脱敏原件保留仓库外，不能直接提交。

```powershell
python -B -m unittest discover -s monitoring/billing/tests -p test_billing_monitor.py -v

python -B monitoring/billing/src/billing_monitor/cli.py --since 2026-10-10T23:00:00+08:00 --until 2026-10-10T23:30:00+08:00 --sub2api-json docs/deployment/2026-10-10/evidence/billing-monitor-tool-sub2api-sample.json --no-state --output .local-test/billing-monitor/reconciliation.json --html .local-test/billing-monitor/report.html --quiet
```

第二条仍通过 SSH 读取真实 trace，且因为账单只有一页样本，预期退出码是 **1（覆盖不完整）**。

本次 26 项回归通过。23:00–23:30（北京时间）真实测试端完成 33 次调用，全部 success，20.65612365 credits；容器为修复镜像 `ba74e53a…`、running、重启 0 次。有 28 条 `previous_prefix_available` 诊断，另有首次观测/旧条目缺失；这些诊断不能逐条充当共享请求 ID，也不等于真实上游 cache usage。

从已登录 Sub2API 页面自身的 GET 响应提取账号 #94 第 1 页的 20 条白名单记录，原查询共 401 条、21 页；本次未取其余页。20 条样本的四类 token 全部与 trace 唯一推断配对，时间差 0.006–0.117 秒；费用分项及倍率核验通过，用户 USD 合计 1.20538656、账号 USD 合计 0.71749200。该样本不是整个窗口的完整账单，13 条 trace 未纳入这页样本。没有提供独立历史价格快照，因此不能宣称完成了历史单价配置核验。

脱敏样本：`evidence/billing-monitor-tool-sub2api-sample.json`。工具实测报告：`evidence/billing-monitor-tool-validation.json`。报告展示在 `.local-test/billing-monitor/report.html`，运行时产物由现有 `.local-test/` 忽略规则排除。

2026-10-12 审查修复后，包内 40 项离线回归通过；旧目录发现测试、无参数运行和指定
单项运行均通过，采集兼容入口生成的独立源码及本机转发输出一致（忽略采集时间）。
本轮仅使用合成 SQLite、API、日志和子进程 mock，没有访问线上或发推理请求。
上面的26项测试与真实费用样本属于10月10日历史证据，不代表当前线上状态。

后续认证 CLI 全分页实测需配置运行环境访问凭据并重新授权执行；本轮不恢复已取消的巡检。
长期独立常驻监控、共享请求 ID 贯通和更细分的定价历史可以作为后续版本；监控仍与线上计费处理隔离。
