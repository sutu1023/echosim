---
name: echosim-acceptance
description: Run EchoSim human acceptance end to end. Use when the user asks to accept, verify, or sign off an EchoSim commit, or wants browser checks plus two make verify runs tied to the same Git SHA. Produces a docs/acceptance record without touching CI artifacts.
---

# EchoSim 人工验收

按本技能走完一次完整人工验收，并把结论写入 `docs/acceptance/<YYYY-MM-DD>-<sha7>.md`。所有证据必须绑定同一个被测 SHA。

## 0. 选择被测提交

- 确认工作树干净（`make verify` 默认拒绝脏工作树）：

  ```bash
  git status --porcelain
  git rev-parse HEAD
  git rev-parse --abbrev-ref HEAD
  ```

- 若存在改动，先提交或改用干净克隆；不要用 `ALLOW_DIRTY=1` 的结论做验收。
- 记录完整 SHA 与短 SHA（前 7 位）。

## 1. 启动环境

```bash
make up
```

- 缺少 `.env` 时会自动从 `.env.example` 生成。
- 失败时先看 `docker compose ps` 与 `docker compose logs app db`，不要跳到后面的步骤。

## 2. 场景一：首页与同域状态

在浏览器打开 <http://127.0.0.1:8080/>，确认：

- 标题 `EchoSim` 与副标题“材料驱动的双平台推演工作台”；
- 状态区域（`aria-live`）显示“可用”；
- 页面没有跨域错误。

无图形浏览器时使用等价命令：

```bash
curl -fsS http://127.0.0.1:8080/ | grep -o "EchoSim"
```

## 3. 场景二：停止数据库后降级

```bash
docker compose stop db
time curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/livez
```

确认 `/health` 在 5 秒内返回 `503` 与 `{"status":"degraded","database":"unavailable"}`，且 `/livez` 仍为 `200`（数据库故障不等于进程死亡）。

记录实际耗时与响应体，不要把耗时记成容器停止命令本身的耗时。

## 4. 场景三：恢复数据库后就绪

```bash
docker compose start db
until curl -fsS http://127.0.0.1:8080/health | grep -q '"status":"ok"'; do sleep 1; done
docker compose ps app
```

确认 `/health` 重新返回 `200` 与 `{"status":"ok","database":"ok"}`，并记录 app 容器 ID / 启动时间，确认未重启（`RESTARTS` 未增加）。

## 5. 连续两次机器验收

```bash
make verify
make verify
```

- 两次都必须退出码 0；第二次前不需要人工清理。
- 记录两次报告的 `started_at`，并核对 `runtime/verify/last-run.json`：
  - `tested_sha` 等于 `git rev-parse HEAD`；
  - `dirty` 为 `false`；
  - `result` 为 `passed`；
  - 场景 `database-stop-degrades` 的 `degrade_seconds` 不超过 5；
  - `app` 身份的 `id`、`pid`、`started_at`、`restart_count` 在停库前后一致。

一次性断言命令：

```bash
python3 - <<'PY'
import json, subprocess
d = json.load(open("runtime/verify/last-run.json"))
assert d["tested_sha"] == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert d["dirty"] is False and d["result"] == "passed" and d["exit_code"] == 0
assert d["degrade_seconds"] is not None and d["degrade_seconds"] <= 5
assert all(s["status"] == "passed" for s in d["scenarios"])
print("verify report ok")
PY
```

## 6. 对齐 CI 产物

- 打开 CI `verify` 运行页，复制运行链接。
- 下载产物 `verify-report-<sha>`，确认其中的 `last-run.json`：
  - `tested_sha` 等于本次人工验收的被测 SHA；
  - `result` 为 `passed`；
  - `dirty` 为 `false`。
- CI 不可用时（例如未配置远程），在记录中写“CI 待完成”，并同步维护 `docs/PENDING-CI-ACCEPTANCE.md`，不要把本地结果说成 CI 证据。

## 7. 生成记录

复制模板并填写：

```bash
cp docs/acceptance/TEMPLATE.md "docs/acceptance/$(date +%F)-$(git rev-parse --short=7 HEAD).md"
```

必须填写：被测 SHA、分支、日期、验收人、环境、三个场景结果、两次 `make verify` 结果、CI 链接与产物、报告文件位置，并在末尾署名。记录只写 `docs/acceptance/`；`make verify` 与 CI 都不会触碰该目录。

## 8. 收尾

```bash
make down
```

确认 `127.0.0.1:8080` 已释放、无遗留项目容器（`docker compose ps -a` 为空），数据卷按需保留（破坏性清理用 `make reset`）。

## 禁止事项

- 不得在脏工作树上出具验收结论。
- 不得用 `dirty: true` 的报告或不同 SHA 的 CI 产物“拼”证据。
- 不得修改 `runtime/verify/` 中的报告内容；报告由 `make verify` 生成。
