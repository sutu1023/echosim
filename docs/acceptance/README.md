# 人工验收记录

本目录保存**人工**验收记录，与 CI 上传的 `runtime/verify/` 机器报告严格分离。

- `make verify` 与 CI 工作流不会创建、修改或删除本目录中的任何文件。
- 每条记录对应一次人工验收，文件名格式为 `<YYYY-MM-DD>-<sha7>.md`，`sha7` 为被测提交 SHA 的前 7 位。
- 记录必须能通过 SHA 与同一次被测代码的机器报告、CI 产物相互对应。
- 新建记录时复制 [`TEMPLATE.md`](TEMPLATE.md)，填写后由验收人署名。

## 一次完整人工验收包括

1. 确认工作树干净：`git status --porcelain` 无输出。
2. 记录被测 SHA 与分支：`git rev-parse HEAD`、`git rev-parse --abbrev-ref HEAD`。
3. 启动环境：`make up`。
4. 场景一：浏览器（或 `curl -fsS http://127.0.0.1:8080/`）看到 `EchoSim` 与“材料驱动的双平台推演工作台”，并确认状态徽标显示“可用”。
5. 场景二：`docker compose stop db`，随后 `curl -i http://127.0.0.1:8080/health` 在 5 秒内返回 `503` 与 `{"status":"degraded","database":"unavailable"}`，同时 `/livez` 仍为 200。
6. 场景三：`docker compose start db`，随后 `/health` 重新返回 `200` 与 `{"status":"ok","database":"ok"}`，且 `docker compose ps` 显示 app 容器未重启。
7. 连续执行两次 `make verify` 均以退出码 0 结束，记录两次报告时间戳。
8. 从 CI 运行页复制运行链接；下载产物 `verify-report-<sha>` 并核对 `last-run.json` 的 `tested_sha`、`result`、`dirty`。
9. 按模板填写记录，在“证据”中列出报告文件位置、CI 运行链接与产物 SHA，并署名。

## 注意

- 被测 SHA 必须是干净工作树上的提交；覆盖未提交改动或 `dirty: true` 的结果不能作为验收证据。
- 没有可用的 CI 运行时，在记录中明确写“CI 待完成”，并同步维护 [`docs/PENDING-CI-ACCEPTANCE.md`](../PENDING-CI-ACCEPTANCE.md)。
