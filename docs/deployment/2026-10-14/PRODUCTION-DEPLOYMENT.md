# 8992 生产部署

完成时间：2026-10-14 02:26:22（Asia/Shanghai）；02:26:45复验。用户本轮明确要求部署8992，授权范围为生产版本切换，未包含主动推理测试。

## 实测结果

- 源码提交：`62ae53467ec98403a5fe02d0317600820dd2e4b1`，全量上游更新、自己的代理热更新、上游计费实现；本地计费兼容已移除。
- 程序版本：`0.9.1-upstream-proxy.20261014`。本机构建 Linux amd64 release 成功，断网执行镜像 `--version` 成功。此前本地863项测试通过、1项忽略，Clippy及前端构建通过。
- 构建输入通过 `git archive 62ae534` 白名单导出，只含源码、依赖锁文件及构建文件；未包含工作区未提交监控改动、运行配置、凭据或原始日志。生产 Dockerfile 使用现有缓存构建方式，更新镜像版本/用途/源码标签。
- 新镜像：`sha256:8009e1a137d85c4d1404922acb589075265a076cbe52947f7128c847c7e95895`。
- 旧镜像：`sha256:fd6f06cc18427faf6aa4b1088c2ce443088efca3ca67d2f23644bf48cd8a0256`，复验时仍在服务器。
- 上传归档 SHA256：`1130243f13d2f97f898e2e3225d16bb1af746ce7b6ebd3ebfc822f07a81f4cd4`，服务器校验一致。
- 生产容器 `kiro-rs-zyphrzero-test` 于02:26:20启动，仍绑定 `127.0.0.1:8992`，挂载原data目录；复验运行正常，重启计数0。
- 内部管理页200、未认证模型401、认证模型200、管理员账号读取200；Windows访问公网 `https://107.174.53.6/v1/models` 无认证返回401（TLS正常验证）。
- 配置逐字节未变，环境变量未变；调度仍为 balanced，账号2/3/4/5均启用、priority均为0，运行时读取与落盘一致。未调整账号、代理或价格。
- 8993的容器ID、镜像、启动时间均未改变。

切换前再次检查，最近20分钟未观察到messages请求或活动出站连接。请求匹配方法是日志模型/开始时间与trace的启发式关联，并非严格的请求计数器。停止生产容器后完整复制data，再启动新镜像。

## 回滚

当前生效部署文件：

`/home/deploy/kiro-zyphrzero-test/compose.upstream-62ae534-20261013T182549Z.json`

备份目录（服务器权限0700，敏感内容没有复制进本地仓库）：

`/home/deploy/kiro-zyphrzero-test/rollback-upstream-20261013T182549Z`

包含停止后的完整data副本、原Compose副本、固定旧镜像的回滚Compose和脱敏结果。仅回退代码、保留当前数据，在服务器执行：

```sh
docker compose -p kiro-zyphrzero-test -f /home/deploy/kiro-zyphrzero-test/rollback-upstream-20261013T182549Z/compose.rollback.json up -d --no-deps kiro-test
```

回滚会短暂中断8992。不要直接恢复旧data覆盖新产生的令牌或用量；如需恢复数据，应先停止并备份当前数据，再核验兼容性。旧镜像存在和回滚文件已核验，未再次切换演练。部署脚本仅在本次启动/健康/配置验证失败时尝试恢复旧镜像，本次未触发，非持续自动回滚机制。

## 未验证

未主动发送推理请求，复验时新实例启动后暂无自然调用trace。真实模型响应、长会话、代理运行时切换及Sub2API最终账单尚未验证。8993用户反馈的计费异常未修复或判定；本次未操作8993。未push。

证据：[构建](evidence/production-build.json)、[部署](evidence/production-deployment.json)、[复验](evidence/production-post-deploy.json)。脚本：[部署](deploy-production.py)、[只读复验](verify-production.py)、[Dockerfile](Dockerfile.production)。
