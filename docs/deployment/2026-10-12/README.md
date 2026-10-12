# 8993 增量流部署与账号同步

2026-10-12 19:37最新状态：已按用户授权部署合并版本7d6bad3，新镜像b0ee6edf9817；旧镜像和数据可回滚。详见[上游合并版本部署](UPSTREAM-DEPLOYMENT.md)。下文为此前部署/回滚历史。

最新状态：2026-10-12 14:01 已按用户要求回滚至部署前镜像 ba74e53a06cf，当前账号和配置保留。见[回滚记录](ROLLBACK-CC-VERSION.md)。以下是 13:03 部署时的历史记录。

时间：2026-10-12 13:03（Asia/Shanghai）。用户本次授权部署8993、同步账号并保留回滚。

## 实测

- 构建来源：74d8492，Linux amd64镜像；前端 bun run build 成功。
- 先前 cargo test：794通过；本次 cargo clippy 完成，有116条警告（包含未使用缓冲代码）。
- 镜像归档SHA256：e4072a07cf89d875e583ac63fcd78601a8e14f7858b0cc792e2c673f18c0fbe3。
- 切换前无已建立上游连接；备份在服务器，未复制凭据到本地。
- 8993管理页200，未认证模型接口401；8992镜像未变。
- 从8992同步到8993：更新4个已有账号、增加1个，共5个。按email或refreshToken匹配，保留测试账号ID。
- 8993配置、入口密钥未修改；凭据记录的生产侧字段已同步。

## 未验证

未发推理请求，未测真实首字耗时、账号可用性或Sub2API最终账单。先前将151秒总耗时直接当作首字延迟的描述没有实测依据。修复只涉及/cc入口，实际调用路由尚需核对。

## 回滚

备份目录：`/home/deploy/kiro-billing-test-20261010/rollback-cc-20261012T050316Z`。
包含旧数据目录副本和固定旧镜像的compose.rollback.yaml；权限受限，只存服务器。

仅回滚代码（保留新增账号），在服务器执行：

```sh
docker compose -f /home/deploy/kiro-billing-test-20261010/compose.billing-test.yaml -f /home/deploy/kiro-billing-test-20261010/rollback-cc-20261012T050316Z/compose.rollback.yaml up -d --no-deps billing-test
```

若还需要回滚账号，应先停止测试容器，再从备份data/credentials.json恢复账号文件，再运行上述命令。不要直接覆盖其他运行数据；后续产生新调用或刷新token后，应先重新备份并评估。

部署脚本在启动/健康/鉴权检查失败时自动恢复旧镜像和账号；本次检查成功，未触发回滚。未来功能异常需人工执行回滚，不存在持续自动回滚监控。
