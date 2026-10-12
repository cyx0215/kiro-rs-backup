# 8993 版本回滚

完成时间：2026-10-12 14:01:01（Asia/Shanghai）。用户本次明确要求“我们回滚一下版本吧”，据此将 8993 回滚至 13:03 部署前镜像，保留当前账号和配置。

## 实测结果

- 原镜像：`sha256:e54e5be78a04abbb4fe7fda1748d75d0d22a6c981b1c1d8be9266665b1c8ffd2`。
- 回滚后镜像：`sha256:ba74e53a06cf097c587f658ae6222047b404f9045f1c9e08f67c55bdbfdc359a`。
- 容器 `kiro-rs-billing-test-20261010`，启动时间 14:00:59；仍绑定 `127.0.0.1:8993`，使用原 data 目录。
- 切换前 20 分钟内观察到的 40 条消息请求均有同模型、开始时间差不超过 20 秒的已结束 trace；最近请求开始距切换检查 140.6 秒。仍有 1 条已建立出站 TCP 连接，未把 TCP 存在直接当作推理活跃或宣称没有连接。上述关联没有共享请求 ID，不是严格的活动请求计数器。
- 停止容器后完整备份当前 data，使用已保存的旧镜像覆盖文件重建 8993。未还原旧账号文件或旧数据库。
- 管理页 HTTP 200，未认证 `/v1/models` 返回 401，带当前 API Key 的模型列表返回 200，管理员账号读取返回 200。
- 配置文件与停止后备份逐字节相同。落盘和运行时账号 ID、优先级、禁用状态相符：#1/#3 停用，#2/#4 启用且 priority=1，#5 启用且 priority=0；调度模式仍为 priority。共 5 个账号。
- 8992 容器 ID、启动时间均未变，未部署或重启。

## 运行文件与恢复依据

当前有效 Compose：

```sh
docker compose -f /home/deploy/kiro-billing-test-20261010/compose.billing-test.yaml -f /home/deploy/kiro-billing-test-20261010/rollback-cc-20261012T050316Z/compose.rollback.yaml up -d --no-deps billing-test
```

当前回滚前数据备份：`/home/deploy/kiro-billing-test-20261010/before-version-rollback-20261012T060028Z`，访问权限受限，仅保留在服务器。该目录还保存指向回滚前镜像的 `compose.restore-current.yaml` 和脱敏 `result.json`。后续重建应使用当前有效覆盖文件；使用旧 `compose.cc-stream.yaml` 会再次启用增量流版本。所有后续线上切换仍需适用授权。

执行脚本：[rollback-cc-version.py](rollback-cc-version.py)，默认只读检查，`--execute` 需要明确授权。健康或配置验证失败时脚本会尝试恢复回滚前镜像，本轮未触发。

脱敏结果：[rollback-cc-version-result.json](evidence/rollback-cc-version-result.json)。密钥、账号令牌及原始日志没有复制到证据。

## 未验证

本轮未主动发送推理请求。健康检查只证明旧版本已运行、接口可访问且鉴权/账号配置保留，不证明 Opus 故障已经消失，也不证明故障由新版造成。此前四组测试授权已经消耗完毕。

回滚恢复旧版 `/cc` 的完整响应缓冲行为；用户此前实际故障请求走 `/v1/messages`。本地源码仍保留 `74d8492`，没有执行 Git 回退或 push；本次回滚仅指服务器部署镜像。
