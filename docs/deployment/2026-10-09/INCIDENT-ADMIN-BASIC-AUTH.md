# 公网管理页重复登录弹窗排查

排查日期：2026-10-09。状态：已捕获触发链并部署 Caddy 修复，等待用户确认浏览器恢复。

## 现象及已验证事实

- 用户在 `https://107.174.53.6/admin` 输入正确的 manager 入口密码后，仍反复出现 Chrome 原生 HTTP Basic 登录框，概览页停留在加载状态。
- 线上 Caddy v2.11.7 对 `/admin`、`/admin/*`、`/api/admin`、`/api/admin/*` 使用同一 Basic Auth，转发到 `127.0.0.1:8992`。
- 无入口认证访问公网管理页和管理 API 均返回 401，带 `WWW-Authenticate: Basic realm="restricted"`。只有管理员密钥时也被入口认证阻止。
- 内部 `/api/admin/credentials` 无管理员密钥时返回 401，不含 `WWW-Authenticate`；正确管理员密钥返回 200。
- 前端 Axios 和导入请求使用 `x-api-key`，没有发现覆盖 Basic `Authorization` 的代码。不能归因于 Bearer 和 Basic 认证头冲突。
- 多个统计请求与定时刷新可能使入口认证挑战重复出现，但不能据此确定为什么正确密码后仍被挑战。
- 浏览器工具无法直接获取用户 Chrome 现场：连接报告 `Codex auth token is unavailable`。通过用户主动重试与服务端脱敏日志捕获了下面的触发链，未读取浏览器存储的密钥值。

## 捕获到的触发链

同一次用户重试中，`/admin` 先无认证返回 401，随后 Basic 认证成功，记录的用户为 manager，页面返回 200。紧接着 `/api/admin/config/self-heal`、`/api/admin/system/update/check`、`/api/admin/config/account-throttle`、`/api/admin/config/load-balancing` 都在 manager 入口认证已通过的情况下返回 401。

对上述内部接口分别使用当前有效管理员密钥及无管理员密钥进行独立探测：有效密钥返回 200，无密钥返回 `Invalid or missing admin API key` 的 401，不带挑战头。这证明用户当前后台请求未通过应用层鉴权，而 manager 密码确实已通过。

随后统计、分组、客户端 Key 等请求开始不携带 Basic 认证，返回 Caddy 的 401 挑战。概览页的多个并行请求、失败重试和自动刷新会放大重复挑战。实际观察到的次序与浏览器在收到应用 401 后不再复用 Basic 凭据一致；没有检查 Chrome 内部实现，不能进一步断定浏览器内部状态切换机制。

未读取客户端密钥，不能区分旧密钥、错误密钥或缺失密钥。后台在已有 localStorage 密钥时直接显示控制台，没有先验证有效性；这使失效的保存状态也可以进入加载中的控制台。

## 已部署修复

在现有 Caddy 管理路由的 reverse_proxy 内，把上游应用返回的 401 映射成 403，保留原错误正文。Caddy 自己的 manager 认证失败仍返回带 Basic 挑战的 401，入口保护保留。模型 API 路由不改。

```caddy
reverse_proxy 127.0.0.1:8992 {
    # Keep application admin-key failures separate from browser Basic challenges.
    @invalid_admin_key status 401
    replace_status @invalid_admin_key 403
}
```

应用时间：2026-10-09 08:57:46 UTC（北京时间 16:57:46）。备份：`/opt/kiro-rs/Caddyfile.before-admin-auth-20261009T085746Z`。保留原文件 inode，Caddy 热重载成功。没有重启账号池，也没有发布本地源码修改。

隔离验证使用仅绑定 loopback 的临时 Caddy 实例和合成 Basic 凭据，完成后已删除临时容器：

| 请求 | 结果 |
| --- | --- |
| 无 Basic，管理页 | 401，带 Basic 挑战 |
| 有效 Basic，管理页 | 200 |
| 有效 Basic，无应用管理员密钥，管理 API | 403，原 JSON 错误，无 Basic 挑战 |
| 有效 Basic，有效应用管理员密钥，管理 API | 200 |
| 无 API Key，模型列表 | 401，无 Basic 挑战 |
| 有效 API Key，模型列表 | 200 |

公网部署后复核了原入口保护和模型列表；未使用用户入口密码主动测试公网双层成功认证，需要用户浏览器确认。用户需要退出网页内保存的失效登录状态，再用当前新版管理员密钥登录。该改动解决认证响应冲突，但不自动修复浏览器内失效密钥；前端全局鉴权失败处理仍是独立改进项。

## 临时诊断

诊断阶段通过 VPS 本机 Caddy 管理 API 加载临时配置，当时未修改 `/opt/kiro-rs/Caddyfile`；后续修复阶段才持久化上述响应码映射。没有修改 manager 密码、应用密钥、账号调度或公网 API 认证。

日志为 Caddy 容器内 `/tmp/admin-auth-diagnostic.jsonl`，权限 0600，1 MiB 轮转，保留 2 个备份，只记录管理路径。过滤掉完整请求对象、响应头和默认 user_id；附加无查询字符串的路径、认证类型（basic/bearer/absent_or_other）及是否认证为 manager。不记录 Authorization 值、密码、密钥、请求正文或查询参数。

两次受控负向探测已验证：无认证和已知无效 Basic 认证均返回 401；日志可以区分 absent_or_other/basic，认证用户名为空。它们是主动探测，不能当作用户故障证据。

恢复原运行配置：

```sh
docker exec kiro-rs-public caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

诊断配置只存在于当时的运行态。部署修复时已移除诊断日志并恢复按原 Caddyfile 加载（含上述修复）；已检查运行 JSON 不再包含 admin_auth_diagnostic。脱敏旧日志仍保存在容器 /tmp 内，不再追加。

## 诊断日志判读（修复前）

- `auth_kind=absent_or_other` 且 401：该请求未携带有效格式的 Basic 认证；进一步检查浏览器认证范围、并发挑战与自动填充。
- `auth_kind=basic`、认证用户名为空且 401：Caddy 没有接受该次 Basic 凭据；不能仅凭此断定用户记错密码。
- 认证用户名为 manager 且 401：入口认证通过，检查应用管理员密钥或上游管理接口。
- 当前已捕获实际重试，未取消管理入口保护。需要用户验证重新登录与统计加载，不仅判断弹窗是否消失。

## 回滚

在 VPS root 终端写回备份并重载；写回时保留 inode：

```sh
cat /opt/kiro-rs/Caddyfile.before-admin-auth-20261009T085746Z > /opt/kiro-rs/Caddyfile
docker exec kiro-rs-public caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
# 校验成功后：
docker exec kiro-rs-public caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

回滚将恢复应用管理 API 原始 401，可能再次触发原故障。没有自动回滚或实际执行回滚。
