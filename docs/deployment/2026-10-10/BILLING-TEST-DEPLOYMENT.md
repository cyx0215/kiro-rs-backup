# 计费兼容测试实例（2026-10-10）

当前状态（2026-10-10 21:04 北京时间核验）：8993测试容器运行 `kiro-rs:billing-diag2-20261010`，镜像ID `sha256:671f73b4199a44c47dd4b62a08e2b9f9c1e852cd2df5fc1bee118ad2131bdfbd`，running、重启计数0；该诊断v2于20:53:59部署，尚未包含缓存匹配修复。生产8992仍为原发布镜像。版本字符串未改，区分以镜像标签/ID及诊断开关为准。发布证据 `evidence/diag2-deployment.json`。

真实调用已确认：同一文字从text对象变为纯字符串后，被原始JSON缓存签名误判为前缀变化，导致本应复用的历史部分重新归入模拟缓存写入。修复候选 `kiro-rs:billing-fix-20261010` 已构建、上传且通过794项Rust测试，**尚未替换测试容器**。用户明确表示“我还在调用，先等一会儿”，因此暂停部署；必须等用户表示当前调用结束、可以切换后，再检查空闲状态并更新测试容器。不能仅凭监测到短暂空闲自动重启。详见 [缓存计费排查](CACHE-BILLING-DIAGNOSIS.md)。

此前18:16:48部署的诊断v1为 `kiro-rs:billing-diag-20261010`，证据 `evidence/billing-diagnostics-deployment.json`。v1/v2只增加诊断，不调整计费算法或价格。后续重建当前诊断v2须同时使用基础Compose与 `compose.billing-diagnostics2.yaml`，否则会恢复旧测试镜像并关闭诊断。

2026-10-10 17:33 缓存分类排查：参见 [缓存计费排查](CACHE-BILLING-DIAGNOSIS.md)。多数 Opus 长请求反复归入模拟缓存写入，但 17:30:35 已出现完整长前缀命中；Sub2API 已匹配样本的算费正确，具体前缀失配原因仍待结构诊断核实。本轮未修改线上计费逻辑。

用户授权将本地修改部署为独立测试实例，生产入口不切换。

## 正常发布版与测试版

| 项目 | 正常使用的发布版 | 本地修改测试版 |
| --- | --- | --- |
| 来源 | 上游预构建 v0.9.1 | v0.9.1 源码加本地未提交修改 |
| 镜像 | `zyphrzero/kiro-rs@sha256:fd6f06cc18427faf6aa4b1088c2ce443088efca3ca67d2f23644bf48cd8a0256` | 当前 `kiro-rs:billing-diag2-20261010`；修复版已上传、待切换 |
| 容器 | `kiro-rs-zyphrzero-test`（历史名称，实际生产） | `kiro-rs-billing-test-20261010` |
| 端口 | `127.0.0.1:8992` | `127.0.0.1:8993` |
| 公网入口 | 现有 HTTPS 入口 | 一天测试已恢复 `/billing-test` API白名单；后台仅 SSH 隧道 |
| 数据目录 | `/home/deploy/kiro-zyphrzero-test/data` | `/home/deploy/kiro-billing-test-20261010/data` |
| 兼容开关 | 未新增开启 | `sub2apiUsageCompat=true` |
| 用途 | 日常使用 | 计费兼容验证 |

本地 Cargo.toml 与 Cargo.lock 的项目版本号设为 `0.9.1-billing-test.20261010`，依赖版本未变，前端 package 仍保留 v0.9.1；镜像与容器均标记 `io.kiro.deployment=billing-test`。用户要求明确区分两者，后续发布记录必须保留此区别。

最初尝试在两核、2 GB 内存服务器单任务编译，耗时过长；用户要求更换发布方式后已取消该构建，保留源码、日志与缓存。正常发布版只需下载预构建镜像，因此此前部署更快。

当前方式改为本机 Docker 构建 Linux amd64 镜像，8任务并行，BuildKit 缓存依赖和编译产物，再导出镜像包传输、校验并在服务器加载。服务器只运行成品镜像，不再编译。尚未创建 GitHub 仓库或配置云端 CI；未来可沿用 Dockerfile 迁入 CI 和镜像仓库。

本地重复构建命令：

```powershell
./docs/deployment/2026-10-10/build-test-image.ps1
```

脚本通过源码白名单在 TEMP 准备构建上下文，不携带运行配置、账号、日志、Git 历史及本地依赖目录。输出镜像包与 SHA256；发布记录应保存镜像 ID 和镜像包校验和。

## 首次构建与隔离（历史基线）

- 源码基线：`f1153704a8a10321d695e7d8aecb123d8eb64f39` 加本地未提交计费兼容与单位显示修改。
- 首次服务器构建尝试的源码包 SHA256：`fa32d9fef6c0bb96d46857fe693a9651e36261f40765614ddfda072ed37eaffe`；该构建已取消，此包不是最终测试镜像的源码快照（后续增加了测试版本标识）。
- 服务器目录：`/home/deploy/kiro-billing-test-20261010`。
- 构建定义：`Dockerfile.billing-test`；Bun 使用冻结锁文件，Cargo 使用 `--locked`。
- 当前在本机并行编译，测试构建关闭 release LTO；服务端不编译。
- 容器：`kiro-rs-billing-test-20261010`；镜像标签：`kiro-rs:billing-test-20261010`。
- 监听：`127.0.0.1:8993` → 容器 `8990`；不对公网开放。
- 独立数据目录 `data/`；不复制生产缓存、trace、client keys 或使用统计。
- 配置和账号凭据仅在服务器内部复制，生成独立 API Key；管理员密钥沿用现有配置，未输出。
- `sub2apiUsageCompat=true`；`updateAutoApply=false`；测试容器不设置自动重启。
- 账号额度与令牌刷新仍关联相同的上游账号，不能视为上游账号隔离。

## 使用和停止

本机隧道命令：

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8993:127.0.0.1:8993 racknerd-deploy
```

管理地址：`http://127.0.0.1:8993/admin`。通过已有应用管理员密钥登录。

```sh
cd /home/deploy/kiro-billing-test-20261010
docker compose -f compose.billing-test.yaml stop
docker compose -f compose.billing-test.yaml start
```

停止测试实例不影响生产。不要长期并行使用同一组上游账号；测试结束后可停止容器保留镜像和数据。

## 验证状态

本地791项 Rust 测试通过；冻结锁文件的前端构建通过。服务器首次编译已取消，本机镜像构建、上传、加载及测试实例启动已完成。运行中的程序版本确认是 `0.9.1-billing-test.20261010`。

本机首次 Rust release 构建耗时3分19秒，不包括首次基础镜像下载和导出传输时间。BuildKit依赖与target缓存已保留。

- 镜像包大小：13,327,872 bytes。
- 镜像包 SHA256：`b192d9e7ce108f2c01b576157c10a2f8ea67ae7e618f1775e2c250a8c41917f5`，服务器校验通过。
- 本地与服务器镜像 inspect ID：`sha256:5ad4e546f051e5ee734ed24e3b5cb0c163da1be0646a06d5ef5902147f66be8e`。
- 未认证模型请求401；认证模型列表200；SSH隧道管理页面200。
- 生产与测试容器均 running、重启次数0。生产入口和渠道未改。

真实测试调用 `claude-sonnet-4` 两次，每次请求 max_tokens=32。上游实际输出第二次为56，不应把该参数理解成严格费用上限。本次未修改该行为。

| 次序 | HTTP | 未缓存输入 | 输出 | 缓存写入 | 缓存读取 | 上游 credits |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 200 | 0 | 2 | 5545 | 0 | 0.023423280348258708 |
| 2 | 200 | 0 | 56 | 0 | 5529 | 0.018184147976782753 |

两次开始事件input/output为0，最终事件均完整结束；四类token与trace完全一致，来源均为simulated。按Sub2API正值更新规则解析已收到的用量，结果与最终四类token一致。合计消耗约0.04160743 credits。

首次验证脚本错误地将最终事件额外的credit字段纳入对象相等比较，导致断言失败；已修正为只比较四类token，使用保存结果`--check-only`复验通过，没有额外推理调用。脱敏结果见 `evidence/billing-test-validation.json`。

首次验证边界：当时尚未接入真实Sub2API/New API测试渠道；后续 Sub2API 实测与当前状态见文末。测试容器保持运行供用户检查，通过独立SSH隧道访问，避免长期共享上游账号并行运行。

## 用户委托复测（14:24 北京时间）

2026-10-10 再次核验服务器：测试版运行约18分钟，正常发布版运行约35小时；测试程序版本仍为 `0.9.1-billing-test.20261010`。重新执行验证脚本，两次真实 Sonnet 4 流式请求均 HTTP 200 且收到 message_stop；未认证模型接口401，认证模型接口200。

| 次序 | 未缓存输入 | 输出 | 缓存写入 | 缓存读取 | 上游 credits |
| --- | --- | --- | --- | --- | --- |
| 1 | 0 | 52 | 5529 | 0 | 0.028306515406301823 |
| 2 | 0 | 103 | 0 | 5520 | 0.02349825671641791 |

开始事件四类token均为0；最终四类token与trace一致，按Sub2API正值更新规则解析结果也一致。合计约0.05180477 credits。用量来源仍为 simulated，不能解释为真实上游缓存。max_tokens=32依然未限制实际输出长度。脱敏证据：`evidence/billing-test-validation-rerun.json`。本轮未接入真实Sub2API/New API渠道，未修改生产入口。

## 真实 Sub2API 联调（14:45 北京时间）

2026-10-10 已通过 Sub2API v0.2.15 的真实下游 Key 发出两次 Sonnet 4 流式请求，均 HTTP 200 且完整结束。专用分组、上游账号和下游 Key 均名为 `kiro-billing-test-20261010`；上游账号 ID94，用户和账号倍率均 1x，并发1，分组 RPM6。下游 Key 限额 $1，Kiro 专用 Key ID1 限额1 credit。额度不构成严格的单次请求费用上限。

| 次序 | 北京时间 | 普通输入 | 输出 | 缓存写入 | 缓存读取 | Sub2API 扣费 USD | Kiro credits |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 14:45:15 | 0 | 33 | 5536 | 0 | 0.02125500 | 0.026439396119402984 |
| 2 | 14:45:18 | 0 | 64 | 0 | 5529 | 0.00261870 | 0.01933314446102819 |

浏览器 Token 明细、费用明细与服务器只读 trace 核对一致。费用明细显示输出单价 $15/百万 token；由实际缓存费用及精确 token 数核算，缓存写入有效单价为 $3.75/百万，缓存读取为 $0.30/百万。普通输入为0，本轮不能验证普通输入单价。

计算：`(33*15 + 5536*3.75)/1000000 = $0.02125500`；`(64*15 + 5529*0.30)/1000000 = $0.00261870`。总扣费 $0.02387370。没有残留估算普通输入费用；不通过改变正常分组价格或倍率强制对齐。Kiro 合计约0.04577254 credits，不能与 USD 直接比较，也不能从这两次样本确定固定兑换率。缓存来源仍是 simulated，不能视为真实上游缓存节省。完整流兼容验证通过；中断流、大上下文、其他模型及 New API 尚未实测。

脱敏证据：`evidence/sub2api-billing-validation.json`。`verify-sub2api-billing.py` 在运行前读取 TEMP 内专用临时凭据并立即删除，只保存用量，不保存回复正文。Python 默认 User-Agent 被站点拒绝（HTTP403、1010）；使用标准 curl User-Agent 后成功，前两次拒绝未产生测试分组账单。

## 临时入口及结束状态

曾尝试独立 Caddy8443，但 Sub2API 到该端口超时。随后在现有 `kiro-rs-public` Caddy443增加严格路径白名单 `/billing-test/v1/models`、`/billing-test/v1/messages`、`/billing-test/v1/messages/count_tokens` 和 `/billing-test/cc/v1/messages`、`/billing-test/cc/v1/messages/count_tokens`，移除前缀后代理8993；不开放测试后台。Sub2API 上游 Base URL 配置为 `https://107.174.53.6/billing-test`。

入口脚本 `enable-billing-test-route.py` 校验配置后加载；原始配置备份只存服务器 `/home/deploy/kiro-billing-test-20261010/Caddyfile.before-billing-test`，mode600，不进入仓库。正常 API 与管理路由保留8992，生产服务镜像、数据和价格未切换。

测试结束已：暂停 Sub2API 账号94调度、禁用下游测试 Key、禁用 Kiro 专用 Key ID1、停止 `kiro-rs-billing-test-edge-20261010`（8443）。通过 `disable-billing-test-route.py` 检查线上配置仍匹配本次配置后恢复原配置，避免覆盖别人期间的修改。恢复后生产 `/v1/models` 未认证401，测试 `/billing-test/v1/models`404。测试应用8993仍运行，管理后台通过本机SSH隧道供用户查看。专属测试分组及用户的测试分组权限保留，因没有调度账号和有效测试 Key，当前不承接请求。

重新联调前先核验实时状态，再启用已有测试 Key 和账号；不要创建重复对象。入口启用脚本首次运行备份采用独占创建；后续仅当现有原始备份与线上配置、测试备份与生成配置均一致时允许重用，否则停止而不覆盖。结束时运行：

```sh
python3 /home/deploy/kiro-billing-test-20261010/disable-billing-test-route.py
```

## 一天使用测试已开启（15:53 北京时间）

2026-10-10 用户要求通过 Sub2API 使用测试版一天。重新核验后已恢复443的白名单测试入口、启用已有账号94调度、启用已有下游测试 Key 和 Kiro 专用 Key ID1，没有创建重复渠道。正常发布版仍使用8992，测试版仍使用8993、版本 `0.9.1-billing-test.20261010`。

用户端 Base URL 为 `https://02acc.itssx.com/`，在 Sub2API「API 密钥」页使用 `kiro-billing-test-20261010` 这一把 Key（「使用密钥」查看客户端配置或复制按钮复制）。它只绑定同名私有测试分组，账号94的上游 Base URL 为 `https://107.174.53.6/billing-test`。原有普通 Key 不自动切换到测试版。

为避免验证用的小额限制阻断日常测试，专用下游 Key 的 $1 限额已设为0（无限制），Kiro 专用 Key 的1 credit上限已清空；未重置累计用量。仍受用户余额、上游账号余额及实际限流约束。分组 RPM6、上游并发1、倍率1x保留。测试与正常发布版使用同一组实际上游账号，会共同消耗 credits；测试数据目录隔离不等于账号余额隔离。

15:52:53 从真实 Sub2API 下游入口执行一条短 Sonnet 4 流式请求，HTTP200且收到 message_stop。开始四类token均为0，最终普通输入4103、输出2、缓存写入0、缓存读取0，上游 credits为0.017516580514096186。脱敏证据为 `evidence/sub2api-daily-test-smoke.json`，使用 `verify-sub2api-billing.py --smoke`，未覆盖14:45的计费核对证据。公网测试模型接口无Key返回401，仍要求认证。本轮证明当前链路可用，不能替代一天使用后的账单核对。

计划观察至2026-10-11同一时段；未配置自动停用或定时任务，Key目前永久有效。结束时先暂停账号94调度、禁用两侧专用测试 Key，再运行上述入口恢复脚本；如需停止8993测试容器，使用本文Compose停止命令。核对账单时筛选同名测试分组和相同时间范围，分别比较四类token与USD公式；credits单独记录。

## 接入 claude2（16:40 北京时间）

2026-10-10 按用户指令，将新测试服务器对应的 Sub2API 账号94 `kiro-billing-test-20261010` 加入 `claude2（kiro）`，并将该上游账号并发从1改为2。保留原专用测试分组，测试 Key 仍可使用。保存后账号表显示容量 `0 / 2`、调度开启、同时属于两个分组。上游地址仍为 `/billing-test`，未切换部署镜像。

claude2原有1个上游账号，本次是增加测试账号而非替换其他账号。因此绑定claude2的Key现在可以调度到测试版，但不能保证每个请求都命中账号94；核对测试账单应按账号94筛选。并发2是账号94在Sub2API中的并发容量，不是用户或整个分组的并发上限。此轮仅验证配置保存，未额外发起推理请求。

编辑时页面显示claude2与专用测试分组的费率均为1.68x（不同于15:53记录的测试分组1x）；本轮未修改任何分组费率，后续对账以实际记录为准。

## 调用监控已开启（17:05 北京时间）

2026-10-10 用户委托监控新账号。Sub2API 用量明细按账号94筛选，17:03:30已有新增调用，列表当时共27条（含此前联调，且数据持续增加）。约17:00账号页显示并发1/2、状态正常、调度开启；累计21次、约6.6M token、账号费用约$26.16、用户费用约$43.93，仅为当时页面近似值。

服务器只读trace在17:04:30观察到16:40以来30条，全部final_status=success，合计54.44501064 credits。该范围是测试服务器全部trace，不保证与账号94、Sub2API已落账数量一一对应；流开始时间与Sub2API记录时间也不同。后续快照见 `evidence/billing-monitor-baseline.json`，脚本 `monitor-billing-test.py` 可通过PowerShell管道送入 `ssh -o BatchMode=yes racknerd-deploy python3 -` 运行，不读取正文或密钥。

17:00:44的Opus流式样本，Sub2API精确token与trace一致：普通输入0、输出1135、缓存写入603439、缓存读取9396。费用明细输出$0.0227、缓存写入$3.017195、缓存读取$0.0018792；有效单价分别为$20、$5、$0.2/百万。`(1135*20+603439*5+9396*0.2)/1000000 = $3.0417742`，乘1.68为$5.110180656，与显示用户扣费一致。账号倍率1x；上游消耗2.79965436 credits单独记录。证据见 `evidence/billing-monitor-sub2api-sample.json`。通过模型及精确token匹配，未验证共享请求ID。

该样本证明token转交和USD计算一致，不能证明模拟缓存分类合理；多条Opus重复出现约60万缓存写入token，单次用户费用约$5，是后续监控重点。不能将simulated解释为真实上游缓存，也不能将credits与USD直接等同。当前success仅指本项目trace终态，未覆盖Sub2API在到达测试服务器前的全部错误。

已通过Codex app tools创建线程监控 `kiro`（名称：Kiro 测试账号调用与计费监控），每15分钟检查，只在失败、429、调度异常、token/费用不一致或访问阻碍时通知，正常新增调用保持安静。计划观察到2026-10-11 16:45左右，到期后首轮汇总并暂停监控；不会自动停用账号或服务。依赖Codex自动化运行环境、SSH及后台登录可用，不是服务器常驻监控。本次只读核验，没有发起推理请求或修改线上配置。

## 缓存签名修复版重新构建与部署（22:58 北京时间）

用户确认继续发布后，从当前本地源码重新构建并上传独立镜像包，未复用21:04的旧包。`cargo test --locked` 794项通过，`git diff --check` 通过；构建标签为 `kiro-rs:billing-fix-20261010`，平台 `linux/amd64`，镜像ID `sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a`。服务器存档为 `billing-fix-rebuild-2256-image.tar`（13,366,784 bytes）及同名前缀的 `.sha256`；本机和服务器SHA256均为 `8389ee957daf5f8434ea275a3112cc9e86dbe115e6cdf0845726694522f246c2`。服务器的发布脚本和 `compose.billing-fix.yaml` 与本地文件哈希一致。原修复包保留，未覆盖。

发布脚本确认旧测试镜像为诊断v2 `sha256:671f73b4199a44c47dd4b62a08e2b9f9c1e852cd2df5fc1bee118ad2131bdfbd`、无活动上游连接且最近trace已静默至少60秒后，仅重建8993测试容器。UTC 14:58:45发布成功，测试管理页200、容器running、重启次数0、结构诊断仍开启。生产8992保持原镜像 `sha256:fd6f06cc18427faf6aa4b1088c2ce443088efca3ca67d2f23644bf48cd8a0256` 且重启次数0。公网测试和正式模型入口的无密钥请求均返回401，认证边界保留。

发布后22:59:07测试trace仍为369条，最新调用始于22:55:20。23:01追加只读核验，22:59:46出现一条新镜像下的Opus自然调用，终态success，普通输入36、输出67、缓存写入136521、读取14279，0.56006245 Kiro credits；结构诊断为`first_observation`，无法判断连续缓存效果。**Sub2API费用及修复效果仍未验证。** 后续需同一身份与模型至少两轮连续自然长调用，并按时间、模型和精确四类token与账号94对照；两系统无共享请求ID。Kiro credits与USD分别统计，simulated用量不等于上游精确cache usage。脱敏部署证据为 `evidence/billing-fix-deployment-2258.json`。

如需回滚8993到诊断v2（不影响8992）：

```sh
docker compose -f /home/deploy/kiro-billing-test-20261010/compose.billing-test.yaml -f /home/deploy/kiro-billing-test-20261010/compose.billing-diagnostics2.yaml up -d --no-deps billing-test
```
