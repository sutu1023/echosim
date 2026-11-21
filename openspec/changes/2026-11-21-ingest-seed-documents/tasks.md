# Tasks

## 1. 迁移与数据模型

- [x] 1.1 新增 `app/migrations/0003_documents.sql`：创建 `documents`（含 `documents_project_content_idx` 部分唯一索引：仅约束 `queued/parsing/ready`）、`document_chunks`（主键 `(document_id, chunk_index)`、`page_no`/段落/偏移列与检查约束），并执行 `ALTER TABLE runs ADD COLUMN document_id`、在同一事务内替换 `runs_kind_check` 为 `('test.simulation','document.ingest')`；先在已有数据卷上连续执行两次 `make migrate`，确认第一次输出 `已应用迁移: 0003_documents`、第二次输出 `无待应用迁移`，再用 `docker compose exec -T db psql -U echosim -d echosim -c '\d documents' -c '\d document_chunks' -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='runs_kind_check'"` 核对索引、外键与约束
- [x] 1.2 在 `tests/test_migrate.py` 增加断言：`discover_migrations` 返回 0001/0002/0003 且顺序正确；`split_sql_statements` 能从 0003 中拆出两张建表、部分唯一索引与两条 `ALTER` 语句，且两条 `ALTER` 留在同一个迁移事务内；运行 `.venv/bin/python -m unittest tests.test_migrate` 通过
- [x] 1.3 在含 Change 04 旧任务的数据卷上验证升级：保留一个已完成的 `test.simulation` 任务及其事件，升级到 `0003` 后确认旧任务详情、事件与副作用可读取；`docker compose restart app worker` 后再次读取仍成功；随后上传并解析一份材料，确认新 `document.ingest` 任务可执行到成功终态；把升级前后的 `jobId`/`runId` 与结果写入任务 9 清单
- [x] 1.4 用真实数据库验证约束语义：插入同项目同 `sha256` 的两条 `ready` 材料必须被部分唯一索引拒绝，把其中一条改为 `failed` 后第二次插入成功；插入违反分块 `chunk_index`/`page_no` 检查约束的非法行必须失败。以 `docker compose exec -T app python -c` 一次性脚本执行并记录结果，确认失败路径不留下部分行

## 2. 存储、配置与上传边界

- [x] 2.1 在 `app/config.py` 增加 `document_storage_dir`（默认 `PROJECT_ROOT / "data" / "documents"`）、`document_max_bytes`（默认 `20971520`）、`document_max_pages`（默认 `2000`）、`document_chunk_target_chars`（默认 1200）、`document_chunk_overlap_chars`（默认 120）、`document_ingest_delay_ms`（默认 0，仅用于验收放大慢解析窗口）六个带安全默认值的配置；同步写入 `.env.example`、`docker-compose.yml`（`app` 与 `worker` 均设置 `DOCUMENT_STORAGE_DIR=/app/data/documents`）与 `README.md` 配置表并在表中写清页数上限与慢解析开关，并在 `.gitignore` 增加 `data/`；用 `python -c "from app.config import settings; print(settings.document_max_bytes, settings.document_max_pages)"` 确认默认值可加载，并逐项核对四处一致
- [x] 2.2 在 `docker-compose.yml` 新增命名卷 `echosim-documents`，挂载到 `app`（`/app/data/documents` 读写）与 `worker`（同路径只读）；`make up` 后 `docker compose exec -T app sh -c 'test -w /app/data/documents && echo ok'` 与 `docker compose exec -T worker sh -c 'test -r /app/data/documents && echo ok'` 均输出 `ok`，且 `docker compose config` 中两个服务都出现该卷
- [x] 2.3 新增 `app/services/document_storage.py`：按 `<root>/<project_id>/<document_id>/original.<ext>` 派生路径、流式写入临时文件（64 KiB/块）并同时计算 SHA-256 与字节数、超限立即中止并删除临时文件、校验通过后原子 `rename`、按记录读取原件字节流；在 `tests/test_document_storage.py` 用临时目录覆盖“恰好上限通过”“上限 +1 中止且不留残片”“路径不含用户文件名”“hash 与字节一致”，运行 `.venv/bin/python -m unittest tests.test_document_storage` 通过
- [x] 2.4 新增 `app/services/documents.py` 的上传路径：文件类型判定（扩展名 + `%PDF-` 魔数，不信任 `Content-Type`）、名称清洗（去路径分隔符与控制字符、空名回退“材料”、长度上限）、`INSERT ... ON CONFLICT (project_id, sha256) WHERE status IN ('queued','parsing','ready') DO NOTHING` 去重、成功后直接创建 `document.ingest` 的 run（`kind`/`stage`/`total_steps=3`/`document_id`）与 job（`operation_key='document-ingest:v1:<documentId>'`）；在 `tests/test_documents_service.py` 用假 session 覆盖“重复内容返回既有材料不新建”“失败材料不阻止新建”“并发冲突分支不产生孤立 run”“创建的任务 kind 与 operation_key 不属于测试任务”，运行 `.venv/bin/python -m unittest tests.test_documents_service` 通过

## 3. 依赖、解析、清洗与分块

- [x] 3.1 在 `app/requirements.txt` 增加 `pypdf` 与 multipart 表单依赖并锁定精确版本，重建镜像后运行 `docker compose exec -T app python -c "import pypdf, multipart; print(pypdf.__version__)"` 确认可导入，且 `make up` 后 `app` 与 `worker` 均为 healthy
- [x] 3.2 新增 `app/services/document_extract.py` 的文本读取：`utf-8-sig` → 带 BOM 的 `utf-16` → `gb18030` 候选顺序，`gb18030` 仅在整份解码成功、无替换字符、控制字符比例低于阈值时采用；解码失败抛领域错误 `decode_failed`；在归一化前记录段落序号与原文起止偏移（UTF-16 代码单元、左闭右开），并把选中的编码写入材料的 `text_encoding`；`tests/test_document_extract.py` 覆盖“UTF-8 混合换行偏移可切回原文”“emoji 之前的偏移与 JS `slice` 一致”“GB18030 中文成功且编码被记录”“二进制字节失败”“UTF-16 BOM 成功”，运行 `.venv/bin/python -m unittest tests.test_document_extract` 通过
- [x] 3.3 实现 PDF 逐页读取：按页提取，`page_no = index + 1`，空白页保留页码与空文本，整份无文字时抛 `no_text_layer`，页数超过 `document_max_pages` 抛 `too_many_pages`；在 `tests/test_document_extract.py` 用仓库内生成的三页夹具（第 2 页空白）断言 `[(1, 正文), (2, ''), (3, 正文)]`，并断言不会把三页拼成一段后推算页码
- [x] 3.4 实现清洗：Markdown/TXT 只做换行归一与空白折叠，MUST NOT 删除正文中的路径/URL，沿用已记录的段落偏移；PDF 逐页移除“跨页重复且位于页首/页尾”的页眉页脚与打印路径模式（`file:///`、`C:\`、`/Users/`、`/home/`、`第 N 页 共 M 页`、孤立页码），且仅当页内仍有内容时才删除；所有类型都移除 NUL 与控制字符并保持偏移可切回原文；`tests/test_document_extract.py` 覆盖“PDF 页眉与打印路径被移除且 pageNo 不变”“PDF 正文行不被误删”“文本混合换行与空白归一”“正文合法路径保留”“空白页仍为 0 块”“含 NUL 的抽取文本可写入数据库”
- [x] 3.5 实现分块：目标长度/重叠取自配置，优先中文与英文句末、其次空白、最后硬切；重叠仅发生在同一段落被拆开的相邻块之间，并回退原文偏移得到区间；每块产出 `chunk_index`、`page_no` 或 `paragraph_start/end`、`source_start/end`；`tests/test_document_extract.py` 覆盖“长段落按句末切分”“重叠块各自可独立切片还原开头”“单句过长才硬切”“正文命令与提示原样保留且不产生副作用”，运行 `.venv/bin/python -m unittest tests.test_document_extract` 通过

## 4. 解析任务与材料状态

- [x] 4.1 扩展 `app/services/worker.py` 的领取与分发：`CLAIM_SQL` 增加返回 `r.kind` 与 `r.document_id`，`Claim` 增加同名字段，按 `kind` 分发到既有测试执行器与新增 `execute_ingest`；在 `tests/test_worker_steps.py` 增加断言：`test.simulation` 仍走原 10 步路径、`document.ingest` 的步骤不写入测试副作用语义（`/effects` 仍以 `test.simulation` 的步骤为判定基础），运行 `.venv/bin/python -m unittest tests.test_worker_steps` 通过
- [x] 4.2 实现 `execute_ingest` 的三步事务：领取时把材料 `queued → parsing`；`read`/`clean` 只提交步骤与 `progress` 事件，不写分块；`index` 在同一事务内完成“删除该材料旧分块 → 批量插入新分块 → 材料置 `ready` 并写 `page_count`/`chunk_count`/`ready_at` → `run_steps` → `completed` 事件 → job `succeeded`”；每一步先用行锁 + `clock_timestamp()` + `lease_token` 守卫，旧令牌零写入；在 `tests/test_worker_ingest.py` 用可注入假 session 覆盖“ready 前分块不可见”“失败不留分块”“旧令牌被拒”，运行 `.venv/bin/python -m unittest tests.test_worker_ingest` 通过
- [x] 4.3 把同步提取放到 `asyncio.to_thread()` 并加长步骤心跳：`read/clean/chunk` 计算期间以 `JOB_HEARTBEAT_SECONDS` 间隔运行心跳协程（复用既有 `heartbeat()`，锁行后读 `clock_timestamp()`），提交前停止；新增测试用配置 `DOCUMENT_INGEST_DELAY_MS` 在线程内解析前等待，制造可控慢解析；用真实 PostgreSQL 验证：租约 2 秒、延迟 6 秒时任务始终 `awaitingTakeover=false` 且 `attempts=1`，记录 `jobId`/`runId` 与耗时
- [x] 4.4 实现失败与取消映射：`decode_failed`、`no_text_layer`、`original_missing`、`too_many_pages`、`parse_error` 在同一事务内写 job 终态、`failed` 事件与材料的 `failed` + `failure_code/message`；扩展既有取消事务：同一事务内写 job=`canceled`、一条 `canceled` 终态事件、材料置 `failed` 且 `failure_code='canceled'`（限定 `status IN ('queued','parsing')`），使 worker 停止时取消 `queued` 也立即生效；完成已提交时取消不改写 `ready`；`tests/test_worker_ingest.py` 与 `tests/test_jobs_service.py` 覆盖“失败不留分块”“排队任务取消立即生效”“完成先提交保持 ready”“失败后可重新上传”，运行 `.venv/bin/python -m unittest tests.test_worker_ingest tests.test_jobs_service` 通过
- [x] 4.5 用真实环境验证中断接手（不依赖“赶在解析完前停 worker”）：以 `DOCUMENT_INGEST_DELAY_MS` 拉长解析窗口，上传三页 PDF 后在窗口内 `docker compose stop worker`，确认材料为 `parsing`、分块接口为空；租约到期后查询任务详情 `awaitingTakeover=true`；`docker compose start worker` 后确认材料变为 `ready`、`attempts=2`、分块数与一次性解析一致且无重复序号；再以 worker 停止状态上传并取消一个 `queued` 任务，确认立即 `canceled` 且可重传，把两组的 `jobId`/`runId`/`attempts`/分块数记录到任务 9 的待签收清单

## 5. HTTP 契约与归属边界

- [x] 5.1 新增 `app/errors.py` 的 `DocumentNotFound`（404 `{"detail":"材料不存在"}`）与 `app/main.py` 异常处理；新增 `app/routers/documents.py` 注册上传、列表、详情、分块、原文视图、原件六个接口，顺序固定为“`require_user` → `resolve_owned_project` → 项目内解析 `documentId`”；`tests/test_documents_routes.py` 断言匿名访问六个接口均为 401 且不访问数据库、他人项目为 404 `{"detail":"项目不存在"}`、自己项目内不存在/非法/他人材料均为 404 `{"detail":"材料不存在"}`、非法 `limit`/`after` 为 422
- [x] 5.2 实现上传的同步拒绝：0 字节 → 400 `{"detail":"文件为空","code":"empty_file"}`，超限 → 413 `{"detail":"文件超过 20 MiB 上限","code":"file_too_large"}`，类型不符 → 415 `{"detail":"仅支持 PDF、Markdown 与 TXT","code":"unsupported_type"}`；`tests/test_documents_routes.py` 断言三种情况的码值与文案、材料列表数量不变、`runtime` 之外不留下临时文件，运行 `.venv/bin/python -m unittest tests.test_documents_routes` 通过
- [x] 5.3 实现分块分页契约：`after` 为“已读取的最后序号”、`limit` 默认 20 且上限 50、按 `chunk_index` 升序、返回 `{"chunks":[...],"total":N}`；PDF 分块返回服务端拼装的 `sourceUrl`（以 `#page=<pageNo>` 结尾），文本分块返回段落与偏移；`tests/test_documents_routes.py` 断言顺序稳定、重复读取一致、`pageNo` 与 `sourceUrl` 一致，运行 `.venv/bin/python -m unittest tests.test_documents_routes` 通过
- [x] 5.4 实现原件与原文视图响应头：原件 PDF 为 `application/pdf`、文本为 `text/plain; charset=utf-8`，`X-Content-Type-Options: nosniff`，`Content-Disposition: inline; filename*=UTF-8''<percent-encoded>`，`Content-Length` 取自材料记录；`/source` 用材料的 `text_encoding` 重新解码并返回未清洗原文，非文本材料返回 409；`tests/test_documents_routes.py` 断言含 `<script>` 的 TXT 不以 HTML 返回、中文文件名按 RFC 5987 编码、`../` 不出现在响应头与响应体、GB18030 材料的 `/source` 偏移可切回原文
- [x] 5.5 让任务列表只包含连通性测试任务：`GET /api/projects/{p}/jobs` 在查询层限定 `runs.kind='test.simulation'`，任务详情/事件/取消仍按 `jobId` 可查；`tests/test_jobs_routes.py` 新增断言“项目内有解析任务时列表不返回它”且既有列表/开始/取消用例全部通过，运行 `.venv/bin/python -m unittest tests.test_jobs_routes` 通过
- [x] 5.6 用 `curl` 对真实环境核对契约：上传返回 201 与 `documentId`/`jobId`/`runId`，重复上传返回 200 与 `deduplicated=true`，列表/详情/分块/原件均可读；匿名与 B 会话的 401/404 文案与 `tests/` 断言一致；停库时带 Cookie 的列表请求 5 秒内 503 且不清 Cookie，恢复后原 Cookie 可继续读取
- [x] 5.7 确认材料内容不进入日志：在解析成功、`decode_failed`、`no_text_layer` 与原件读取失败四种情况下检查 `docker compose logs app` 与 `docker compose logs worker`，确认没有正文片段、完整文件名或真实存储路径，只有 `documentId`、`code` 与异常类型

## 6. 前端材料区与来源定位

- [x] 6.1 新增 `web/src/materials/` 纯逻辑：状态到中英文文案的映射、字节格式化、PDF `#page=` 链接拼装、文本区间切片与段落范围文案、失败原因到提示的映射，以及材料地址的查询参数拼装/解析（保留 `layout`/`lang`/`document`）；新增 `web/tests/material-source.test.mjs` 覆盖“第 3 页链接为 `#page=3`”“含 emoji 的区间切片与原文一致”“切换材料不丢 `layout`/`lang`”“失败原因不落到空态文案”，运行 `cd web && npm test` 通过
- [x] 6.2 新增 `MaterialsPanel.vue`（上传、列表、状态、失败原因与重新上传、摘录分页、材料详情 `?document=` 切换）并接入 `ProjectDetailView.vue` 与 `WorkbenchView.vue` 第一阶段；上传与切换材料使用 `router.replace` 只改查询参数，保持项目、阶段、`layout`、`lang`；解析中每 2 秒轮询材料详情、终态后停止，轮询失败只提示“暂时无法刷新”；运行 `cd web && npm run typecheck && npm run build` 通过
- [ ] 6.3（代码已完成，浏览器核对待作者签收）实现来源定位与材料深链接：PDF 分块渲染 `target="_blank" rel="noopener"` 的原件链接（地址取 `sourceUrl`），文本分块在同页 `<pre>` 渲染 `/source` 原文并用 `Range` + `scrollIntoView` 定位与高亮 `source_start/source_end`；在浏览器验证三页 PDF 的第 3 页链接地址以 `#page=3` 结尾、文本高亮取自未清洗原文且含 emoji 时不偏移、刷新或粘贴完整 `?document=` 地址回到同一材料、匿名打开后登录回到原地址、B 打开 A 的材料地址只看到“项目不存在”
- [ ] 6.4（代码已完成，浏览器核对待作者签收）改造控制台材料入口（`ConsoleView.vue`）：访客点击进入 `/login?next=/projects`，已登录进入 `/projects`；文案改为支持 PDF/Markdown/TXT 与 20 MiB 上限；控制台不出现项目名、材料列表或上传控件；在浏览器分别以访客与已登录身份验证，并确认中英文切换后入口文案与去向不变
- [x] 6.5 在 `web/src/i18n.ts` 补齐材料区中英文案：空态、排队中/解析中/就绪/失败、大小上限、被拒绝原因、服务不可用、摘录分页、查看原件/原文；运行 `cd web && npm run typecheck` 通过，并在浏览器以 `lang=en` 核对不出现中文残留（项目名与文件名除外）

## 7. 文档与工程规则

- [x] 7.1 更新 `README.md`：新增材料接口表、支持类型与 20 MiB 边界、状态与失败原因表、物理页码与段落定位说明、材料存储卷与清理方式、`make verify CHANGE=2026-11-21-ingest-seed-documents` 用法、人工走查步骤（含私有样本）；逐条复制文档中的命令与 `curl` 示例实际执行，确认输出与文档一致
- [x] 7.2 更新 `AGENTS.md`：新增“材料入库与来源定位”规则——原件私有保存与哈希、20 MiB 边界、PDF 物理页码与空白页、清洗保留来源、分块可独立定位、同项目去重、解析只能由 worker 执行且分块与 ready 同事务发布、材料接口复用 `resolve_owned_project`、私有样本不入 Git/CI、`make verify` 不宣称浏览器落页已验证；逐条与 delta 规范对照，确认没有弱化既有要求
- [x] 7.3 在 `README.md` 的人工走查清单中补充“材料入库”步骤（上传、总页数、第 1/3 页跳转、私有样本不提交），MUST NOT 修改 `docs/acceptance/` 下的任何文件（包括模板与历史记录）；用 `git status --porcelain docs/acceptance` 确认该目录无改动

## 8. 验收脚本与 CHANGE 注册

- [x] 8.1 在 `scripts/verify.py` 增加夹具生成：小 TXT（默认冒烟）、手写最小三页 PDF（Helvetica、ASCII 标记、第 2 页空内容流）、带重复页眉与打印路径的三页 PDF、用于页数上限的 3 页与 4 页 PDF、含混合换行 + 连续空白 + emoji + 正文合法路径的 TXT、GB18030 中文 TXT、恰好 20971520 字节的 TXT（少量正文 + 可折叠空白精确补齐）、20971521 字节 TXT、0 字节文件、无文字层 PDF；全部在内存中生成、随进程结束释放（不落仓库、不进入 CI 产物）；新增 `tests/test_verify_fixtures.py` 断言页对象顺序与标记位置、各字节数精确、无文字层夹具抽取文本为空，运行 `.venv/bin/python -m unittest tests.test_verify_fixtures` 通过
- [x] 8.2 在 `scripts/verify.py` 增加 `DOCUMENT_CHANGE_ID = "2026-11-21-ingest-seed-documents"` 并加入 `KNOWN_CHANGES`，在 `new_report` 中初始化 `document_evidence`；在默认流程新增轻量材料场景：小 TXT 上传后轮询到 `ready`、列表可见、分块数 ≥ 1、匿名读取列表 401；`python3 -m py_compile scripts/verify.py` 通过
- [x] 8.3 增加 CHANGE 专属 PDF 页码场景：三页 PDF 断言 `pageCount=3`、第 2 页 0 块、第 3 页 `pageNo=3` 且文本等于第 3 页标记；`sourceUrl` 以 `#page=3` 结尾、原件 SHA-256 等于上传值、按 `/Kids` 顺序检查第 3 个页对象内容流含第 3 页标记；在 `document_evidence` 记录 `documentId`/`jobId`/`runId`/`pageCount`/`chunkCount`/`page3ChunkPageNo`/`page3SourceUrl`/`originalSha256`
- [x] 8.4 增加 CHANGE 专属清洗场景：带页眉与打印路径的 PDF 断言三者被移除、正文保留、`pageNo` 不变；文本夹具断言混合换行与空白归一、正文合法路径保留、`sourceStart/End` 在 `/source` 中切片一致、emoji 前不偏移；GB18030 夹具断言 `/file` 字节哈希与响应头、`/source` 为 UTF-8 且偏移可切回；在 `document_evidence` 记录清洗前后行数
- [x] 8.5 增加 CHANGE 专属边界与失败场景：恰好 20 MiB 首次上传新增一份材料并最终 `ready`；20 MiB + 1 返回 413、空文件返回 400，且只有这两次拒绝断言材料数不变、无残片；无文字层 PDF 为 `failed: no_text_layer` 且 0 块；以 `DOCUMENT_MAX_PAGES=3` 重启 worker 后断言 3 页就绪、4 页 `too_many_pages` 且文案含 3，结束后恢复默认
- [x] 8.6 增加去重、隔离与控制流场景：同文件第二次上传返回 200、`deduplicated=true`、相同 `documentId`，材料数与分块总数不变、解析任务数不增加；匿名请求列表/原件/分块为 401 JSON；B 请求 A 的原件与分块为 404 且与随机 `documentId` 响应体一致；worker 停止时取消 `queued` 任务立即得到 `canceled` 事件与材料 `failed: canceled`、0 块且可重传；完成后再取消保持 `ready`
- [x] 8.7 增加慢解析与接手场景：设 `DOCUMENT_INGEST_DELAY_MS` 使单步耗时超过租约，断言解析期间 `awaitingTakeover=false`、`attempts` 保持 1；在延迟窗口内停 worker，租约到期后断言等待接手，恢复后 `attempts` 增加、分块数与同内容一次性解析一致、无重复 `chunkIndex`，并把接手前后数据写入 `document_evidence`
- [x] 8.8 运行 `CHANGE=not-a-change make verify` 确认仍以退出码 2 结束且报告写入“未注册的变更”；再运行默认 `make verify` 确认包含轻量材料场景、不含 PDF/20 MiB/跨账户/接手场景、报告 `change` 为 `null`，且 Change 01–04 全部通过

## 9. 集成验收与人工签收准备

- [ ] 9.1 提交后确认 `git status --porcelain` 为空，在干净提交上带 `EXPECTED_SHA=$(git rev-parse HEAD)` 连续两次执行 `make verify` 与两次 `make verify CHANGE=2026-11-21-ingest-seed-documents`，四次退出码为 0、`tested_sha` 均等于该 SHA、`dirty=false`；核对两份变更报告的场景数、`document_evidence` 与两次运行一致，并列出四份报告路径
- [x] 9.2 运行 `cd web && npm test && npm run typecheck && npm run build` 与 `.venv/bin/python -m unittest discover -s tests`，确认全部通过且 `stage-links`、`job-log`、`job-panel-state` 等既有回归未被削弱
- [ ] 9.3 准备待人工验收清单（不写入 `docs/acceptance/`）：三页 PDF 在浏览器中总页数为 3、第 1 与第 3 页摘录跳回原件的实际页码；材料深链接的四项浏览器检查（所有者刷新/粘贴回同一材料、匿名登录后回原地址、B 打开时提示项目不存在、`layout`/`lang` 保留）；25 页私有样本的总页数与第 1/3 页落页；截图位置与同 SHA 报告路径；清单标注“未签收前不归档”
- [ ] 9.4 由项目作者在自己的 Mac 浏览器上传 25 页私有样本并核对总页数与第 1、3 页跳转；样本只放在仓库外或已忽略的 `private-inputs/`，确认它与其正文没有出现在 `git status`、`runtime/`、报告中或任何提交里，验收结论只写入 `docs/acceptance/`
