# 2026-10-09 生产部署记录

本次将 `hank9999/kiro.rs` 替换为 `ZyphrZero/kiro.rs v0.9.1`，已完成切换和公网调用验证。日期使用北京时间（Asia/Shanghai）；服务器日志与备份目录中的时间为 UTC。

## 版本与现状

| 项目 | 记录 |
| --- | --- |
| 源码仓库 | https://github.com/ZyphrZero/kiro.rs |
| 本地开发目录 | `E:\cursor2\kiro-rs` |
| 本地源码 commit | `f1153704a8a10321d695e7d8aecb123d8eb64f39` |
| 发布版本 | `v0.9.1` |
| 实际部署镜像 | `zyphrzero/kiro-rs@sha256:fd6f06cc18427faf6aa4b1088c2ce443088efca3ca67d2f23644bf48cd8a0256` |
| 部署方式 | 发布者预构建 Docker 镜像，按 digest 固定；未从本地源码构建 |
| VPS | `107.174.53.6` |
| 公网 API Base URL | `https://107.174.53.6` |
| Claude Code Base URL | `https://107.174.53.6/cc` |
| 公网管理后台 | `https://107.174.53.6/admin` |
| 新版容器 | `kiro-rs-zyphrzero-test`，运行中 |
| 新版监听 | 宿主机 `127.0.0.1:8992` → 容器 `8990` |
| 旧版容器 | `kiro-rs`，已停止，保留用于回滚 |

新版目录、容器及 Compose 服务仍保留最初的测试名称，但现在承担生产流量。不要因为名称中有 `test` 就清理它们。源码 commit 是本地检出记录，不作为发布镜像可复现构建的证明。

## 请求路径与配置位置

```text
Windows / Sub2API → https://107.174.53.6:443
                  → Caddy → 127.0.0.1:8992
                  → 新版 kiro-rs → HTTP 代理 → Kiro
```

| 用途 | VPS 路径 |
| --- | --- |
| 新版 Compose | `/home/deploy/kiro-zyphrzero-test/compose.yaml` |
| 新版配置、凭据及运行数据 | `/home/deploy/kiro-zyphrzero-test/data/` |
| 新版配置 | 上述目录内 `config.json` |
| 新版账号凭据 | 上述目录内 `credentials.json` |
| 请求追踪 | 上述目录内 `traces.db` |
| 缓存模拟状态 | 上述目录内 `cache_metering.json` |
| Caddy 配置 | `/opt/kiro-rs/Caddyfile` |
| Caddy Compose | `/opt/kiro-rs/compose.public.yaml` |
| 原服务 Compose 与配置 | `/opt/kiro-rs/compose.yaml`、`compose.debug.yaml`、`config/` |
| TLS 证书 | `/etc/letsencrypt/live/kiro-rs-ip/` |
| 切换脚本 | `/home/deploy/kiro-zyphrzero-test/cutover-kiro.py` |
| 切换测试结果（root 可读） | `/home/deploy/kiro-zyphrzero-test/cutover-validation.json` |

鉴权关系：公网 API 沿用原生产 `apiKey`，Sub2API 的 URL 和 Key 无需因本次切换而修改。公网管理页先通过 Caddy Basic Auth（用户名 `manager`，原密码不变），再使用新版 `adminApiKey`。新版管理员密钥与旧版不同。

HTTP 代理地址为 `http://198.63.254.19:443`，已配置认证；不要因端口是 443 改成 HTTPS 代理协议。配置代理访问 IP 检测服务，实测出口 `198.63.254.19`。此验证证明代理连通及出口，不等于抓包审计每一条 Kiro 请求，也不独立证明 IP 的住宅属性。

配置保留 `region=us-east-1`、`tlsBackend=rustls`；`updateAutoApply=false`、`cacheMeteringEnabled=true`、`traceEnabled=true`。实例使用独立数据目录，内存上限 512 MB，容器日志轮转 10 MB × 3，重启策略 `unless-stopped`。

## 切换过程与验证证据

1. 单独部署 v0.9.1 到本机 8992，复制配置和凭据，生成独立测试密钥。
2. 验证模型列表、Sonnet 5.5 流式请求、Sonnet 4.5 的 `/cc/v1/messages` 非流式请求，以及代理出口。
3. 备份两实例配置、凭据与原 Caddyfile，让新版采用原生产 API Key，保留新版管理员密钥。
4. 预校验新 Caddyfile，将两个 upstream 从 8990 改为 8992。
5. 用户在 root 终端执行切换脚本。脚本验证公网鉴权、模型列表、连续两次缓存请求，成功后停止旧容器；异常则恢复原 Caddyfile。
6. 通过 SSH 独立复核实际容器状态、Caddy upstream、公网状态码和数据库追踪。备份中不存在未实际生成的完整归档或独立 rollback.sh。

备份目录：`/home/deploy/kiro-zyphrzero-test/backups/cutover-20261008T191605Z`。内含 `production-config.json`、`production-credentials.json`、`new-config.json`、`new-credentials.json` 和原 `Caddyfile`。这些文件包含密钥，只留在服务器。

切换测试（用户终端输出，随后由 traces.db 验证）：

| 请求 | 普通输入 | 输出 | 缓存写 | 缓存读 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| 第一次 | 3 | 1 | 2144 | 0 | 成功 |
| 第二次 | 3 | 24 | 0 | 2131 | 成功 |

切换后主动公网测试（相同 session、相同长前缀、显式 `cache_control`）：

| 请求 | HTTP | 客户端总耗时 | 普通输入 | 输出 | 缓存写 | 缓存读 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 第一次 | 200 | 1.35 秒 | 6 | 7 | 2194 | 0 |
| 第二次 | 200 | 0.94 秒 | 6 | 7 | 0 | 2194 |

两次回复均为“公网连接测试成功。”服务端耗时与上表的客户端耗时口径不同，以 JSON 中 `duration_ms` 为服务端记录。

现场脱敏证据：[evidence/verification.json](evidence/verification.json)。采集于 2026-10-09 北京时间，包含实际镜像、挂载、重启策略、端口、鉴权结果、代理出口、证书指纹/到期时间和最近请求用量。其中还包含一条 Opus 5.5 成功记录，这是现场已有请求，不是本次文档工作主动发起的测试。

边界说明：

- 所有上述带缓存的实测请求均标记 `usage_source=simulated`，不能宣称上游提供真实缓存命中或实际扣费因此降低。
- 缓存模拟依赖请求条件；未提供有效断点、会话等条件的请求可能无缓存。不能保证任意两次请求都命中。
- 本次没有修改另一台机器上的 Sub2API，也没有核验其实际账单。应通过它发起相同前缀的两次请求后，检查最终用量、缓存费率及账单。
- 无密钥访问公网 `/v1/models`、`/admin` 返回 401 属于预期；使用 API Key 的模型列表及使用管理员密钥的内部管理接口返回 200。

## 日常维护

2026-10-09 公网管理认证补丁：仅在管理 reverse_proxy 中把应用 401 映射为 403，避免应用管理员密钥失效后与 Caddy Basic Auth 挑战相互影响。入口 manager 认证保留。详见 [重复登录弹窗记录](INCIDENT-ADMIN-BASIC-AUTH.md)，其中包含隔离验证、现场触发链及回滚备份。没有发布新的 Rust 或前端源码版本。

在 VPS 上查看状态：

```sh
cd /home/deploy/kiro-zyphrzero-test
docker compose ps
docker inspect kiro-rs-zyphrzero-test --format '{{.State.Status}} restarts={{.RestartCount}}'
```

需要重启新版时使用 `docker compose restart kiro-test`。旧目录的 `docker compose up -d` 会重新启动旧服务，不要把它当作新版重启命令。不要同时长期运行两个共享账号的实例；凭据文件独立，但额度和令牌刷新仍关联同一个上游账号。

Windows 本机访问隧道（保持终端打开）：

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8992:127.0.0.1:8992 racknerd-deploy
```

本机访问 `http://127.0.0.1:8992/admin`，仅需新版管理员密钥。隧道依赖本机 SSH 别名配置，不是其他机器可直接使用的公网 URL。

证书为短周期 IP 证书，已配置 Certbot 续期；此前续期 dry-run 成功。本次再次验证公网 TLS 信任正常，当前证书到期时间见 JSON。后续要检查续期后 Caddy 已加载新证书，不能仅看证书文件更新。本次未重新执行续期演练。

## 回滚步骤（root 终端，按顺序执行并检查）

1. 启动旧服务：`docker start kiro-rs`。先用原 API Key 在 `http://127.0.0.1:8990/v1/messages` 完成一次 Sonnet 4.5 请求，确认账号凭据仍可用。只检查模型列表不足以证明上游调用正常；如令牌失效，先修复旧版凭据，再切公网。
2. 恢复旧 Caddyfile 并重载。使用写入原文件的方式保留 inode，因为容器对单个 Caddyfile 做了 bind mount：

```sh
cp /opt/kiro-rs/Caddyfile /opt/kiro-rs/Caddyfile.before-rollback
cat /home/deploy/kiro-zyphrzero-test/backups/cutover-20261008T191605Z/Caddyfile > /opt/kiro-rs/Caddyfile
docker exec kiro-rs-public caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
# 仅在校验成功后执行：
docker exec kiro-rs-public caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

3. 验证公网真实模型请求成功，再执行 `docker stop -t 60 kiro-rs-zyphrzero-test`。保留新版数据以便排障。回滚后内层管理登录改回旧版管理员密钥。
4. 如校验或重载失败，恢复 `Caddyfile.before-rollback`，重新校验并重载；不要在公网尚未恢复前停止当前正常服务。

回滚不会撤销已产生的调用或费用，也不会把新版缓存状态移植到旧版。以上为运维预案，本次未实际回滚。

## 下次发布

在本地开发分支完成修改和针对性测试；记录 commit 并构建自己的固定版本镜像。先独立验证，再备份当前配置、凭据和路由，切换入口，测试模型、代理、缓存来源及 Sub2API 账单，最后停旧实例。二次开发尚未发布：当前生产仍是上游 v0.9.1 镜像。
