# 8993 上游合并版本部署

19:42验证补充：用户明确授权真实调用后，从本地SSH在服务器回环地址测试8993，Sonnet 5.5的`/v1/messages`与`/cc/v1/messages`各1次，均200且返回OK、正常message_stop/end_turn。首文本1089.1ms/634.2ms，总耗时1115.0ms/634.8ms；初始用量均0，最终input1295/output1，报告credit合计约0.029482。这是两次短文本冒烟测试，不是性能对照或最终账单核验。未更改代理、配置或8992。见[调用证据](evidence/upstream-smoke.json)。下文“未发线上推理”为部署阶段记录。

完成时间：2026-10-12 19:36:46（Asia/Shanghai）；19:37:28再次检查。用户明确同意先部署8993，并在确认可回滚后再次要求“好的，部署吧”。8992不在本次变更范围。

## 实测

- 源码：`7d6bad3622b5e42fcf876cf5921f0bd2eb908256`。包含上游#110/#112，以及本地缓存计费、Sub2API兼容、代理热更新。
- 187个构建输入文件与本地源码逐字节一致；采用源码白名单构建Linux amd64镜像，未打包运行配置、凭据、Git历史或本地日志。
- 本轮Linux release构建成功，镜像离线执行`--version`成功。之前合并验证820项测试、Clippy和前端构建通过；本机回环延迟基准也已通过。
- 新镜像：`sha256:b0ee6edf9817de86523744c625ff4e18611ed9d84e3ec5a249f4751591b84e5b`。
- 旧镜像：`sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a`，仍保留在服务器。
- 镜像归档SHA256：`38e6a100ab3315dc7a4a5c43e675145a438656dfd0473f3eccd84e46ee5dba65`，上传后校验一致。
- 8993容器于19:36:44启动，绑定仍为`127.0.0.1:8993`，挂载原data目录；19:37:28运行正常，重启计数0。
- 管理页200、未认证模型列表401、带当前API Key模型列表200、管理员账号读取200。
- 配置逐字节未变、环境变量未变；Sub2API兼容开启、诊断开启、调度模式保持实际的`balanced`。4个账号ID/优先级/禁用状态与切换前一致，未同步生产账号。
- 8992容器ID、镜像和启动时间未变。

切换前因新请求到达，首次执行被安静窗口检查拒绝，未停止容器。之后最近20分钟观察的50个请求均按模型与开始时间匹配到结束trace，距最近请求106秒，仍有1条出站连接；这是无共享请求ID的启发式检查，不是严格的活动请求计数器，也未宣称无连接。确认安静窗口后停止8993、完整备份data，再启动新镜像。

## 运行与回滚

当前部署文件（只含8993服务，使用固定镜像ID）：

`/home/deploy/kiro-billing-test-20261010/compose.upstream-7d6bad3-20261012T113613Z.json`

服务器备份目录（权限0700，不复制敏感数据到本地）：

`/home/deploy/kiro-billing-test-20261010/rollback-upstream-20261012T113613Z`

包含停止后的完整data副本、旧Compose副本、固定旧镜像的`compose.rollback.json`及脱敏结果。仅回滚代码并保留当前数据，在服务器执行：

```sh
docker compose -p kiro-billing-test-20261010 -f /home/deploy/kiro-billing-test-20261010/rollback-upstream-20261012T113613Z/compose.rollback.json up -d --no-deps billing-test
```

回滚会短暂中断8993。不要直接恢复旧data覆盖新产生的账号令牌或用量记录；如确需恢复数据，须先停止并备份当前数据，再判断兼容性。旧镜像存在、回滚文件的镜像引用和端口已核验，但没有为演练再次切换服务。

部署脚本在启动/健康/配置验证异常时尝试恢复旧镜像，本次没有触发；不是持续自动回滚监控。

## 未验证与限制

本轮没有发送线上推理、切换代理或修改账号/配置。19:37:28检查时新版本启动后暂无完成trace；真实Claude长会话、工具调用、代理切换及Sub2API最终账单仍待验证。

镜像中的程序版本字符串仍为`0.9.1-billing-test.20261010`，请用源码提交和镜像ID区分版本。没有push或更新fork。

脚本：[deploy-upstream.py](deploy-upstream.py)。脱敏证据：[构建](evidence/upstream-build.json)、[部署](evidence/upstream-deployment.json)、[复验](evidence/upstream-post-deploy-check.json)。原始构建输出仅保存在忽略目录`.local-test/`。
