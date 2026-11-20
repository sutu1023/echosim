# 待验收事项：CI 证据（未完成）

## 现状

- 本地仓库当前**没有配置任何 Git 远程**（`git remote -v` 为空），因此无法推送提交，也无法触发 `.github/workflows/verify.yml`。
- 已完成的本地等价验证：
  - `make verify` 在干净工作树上连续多次通过；
  - `EXPECTED_SHA=$(git rev-parse HEAD) make verify` 复现 CI 调用方式并通过；
  - `.github/workflows/verify.yml` 通过 YAML 解析与 `actionlint` 校验（表达式、触发器、步骤均无错误）。
- 因此“CI 运行成功”和“CI 产物 `tested_sha` 与触发 SHA 相同”这两项**尚无证据**，不能视为已验收。

## 待办（配置远程后可完成）

1. 添加远程并推送当前分支：

   ```bash
   git remote add origin <repo-url>
   git push -u origin HEAD
   ```

2. 等待 `verify` 工作流成功，记录运行链接与触发 SHA：

   ```bash
   gh run list --workflow verify --limit 5
   gh run view <run-id> --log
   ```

3. 下载产物 `verify-report-<sha>`，核对 `last-run.json`：

   ```bash
   gh run download <run-id> -n "verify-report-$(git rev-parse HEAD)"
   python3 -c "import json; d=json.load(open('verify-report-<sha>/last-run.json')); print(d['tested_sha'], d['result'], d['dirty'])"
   ```

4. 把 CI 运行链接与产物 SHA 补进 `docs/acceptance/2026-11-20-02a46cb.md` 的“CI”与“证据”字段，并据此更新该记录的结论。

## 影响范围

- 不影响本地启动、健康检查与本地可重复验收的结论。
- 影响的是“CI 与干净克隆测试同一 SHA”这一证据链环节；在完成上述步骤前，交付说明中必须将其列为未完成项。
