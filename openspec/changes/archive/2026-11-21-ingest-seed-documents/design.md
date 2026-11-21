# Design

## Context

Change 04 合并提交为 `c2570c1`。当前后端已有 `health`、`auth`、`projects`、`history`、`jobs` 五组路由，`app/services/worker.py` 以 `runs.kind` 未知的方式只实现了 `test.simulation` 一种执行器：`execute_job()` 直接按“10 步 + 每步一条 `run_steps`”循环，`runs_kind_check` 只允许 `test.simulation`。`app/db.py` 的连接与命令超时均为 2 秒，`app/services/projects.py` 的 `resolve_owned_project` 是唯一归属入口。

前端已有五阶段注册表、三种布局、`layout`/`lang` 查询参数、`JobPanel` 测试任务面板；项目详情只显示名称与描述，工作台第一阶段的材料区是硬编码空态（`stageHasData = false`，文案写“材料入库尚未开放”）。

本变更引入第一个“文件 + 长任务 + 可定位文本”的路径，必须在既有 401/404/503 契约、`/health` 与 `/livez` 语义、任务租约机制不变的前提下完成。需求见 `specs/document-ingest/spec.md`、`specs/project-workbench/spec.md`、`specs/guided-console/spec.md` 与 `specs/reproducible-verification/spec.md`。

## Goals / Non-Goals

**Goals:**

- 让“原件、清洗文本、位置”三者的关系在数据模型里显式存在，任何摘录都能回到唯一确定的物理页或原始段落。
- 让解析复用现有队列与租约机制，并且“分块可见”只有一次提交，使中断、重试、取消都不可能留下半成品或重复块。
- 让去重由数据库约束保证，而不是靠应用层先查后写。
- 让 20 MiB 上限、空文件、无文字层、无法解码各自有稳定原因标识，页面与验收都能断言。

**Non-Goals:**

- 不做 OCR、版面分析、表格与图片抽取；不实现检索排序、向量化或证据图谱计算。
- 不引入对象存储、任务队列中间件、Alembic 或浏览器端 E2E 框架。
- 不为材料提供删除、重命名、移动、批量上传或版本管理。
- 不改动既有测试任务执行器、SSE 契约、`/health` 与 `/livez`。

## Decisions

### 1. 数据模型：材料与分块分开，位置写成显式列

新增 `app/migrations/0003_documents.sql`（只前向，纯新增对象 + 两处约束扩展）：

```sql
CREATE TABLE documents (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uploaded_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name text NOT NULL,          -- 已去除路径与控制字符，仅用于展示与下载建议名
    kind text NOT NULL,                  -- 'pdf' | 'markdown' | 'text'
    content_type text NOT NULL,          -- 规范化后的 MIME
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 char(64) NOT NULL,
    storage_path text NOT NULL,          -- 相对 DOCUMENT_STORAGE_DIR，由 id 派生
    text_encoding text,                  -- 文本材料解析时采用的编码，供原文视图确定性重解码
    status text NOT NULL DEFAULT 'queued',
    page_count integer,                  -- 仅 PDF，就绪后 >= 1
    chunk_count integer NOT NULL DEFAULT 0,
    failure_code text,
    failure_message text,                -- 面向用户的一句话，不含堆栈与真实路径
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    CONSTRAINT documents_kind_check CHECK (kind IN ('pdf','markdown','text')),
    CONSTRAINT documents_status_check CHECK (status IN ('queued','parsing','ready','failed')),
    CONSTRAINT documents_page_count_check CHECK (page_count IS NULL OR page_count >= 1),
    CONSTRAINT documents_chunk_count_check CHECK (chunk_count >= 0)
);

-- 只有“有效材料”参与去重：失败材料不阻止重新上传。
CREATE UNIQUE INDEX documents_project_content_idx
    ON documents (project_id, sha256)
    WHERE status IN ('queued','parsing','ready');

CREATE INDEX documents_project_created_idx ON documents (project_id, created_at DESC);

CREATE TABLE document_chunks (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    page_no integer,                     -- PDF：从 1 开始的物理页码；文本为 NULL
    paragraph_start integer,             -- 文本：原始段落序号（从 1 开始）；PDF 为 NULL
    paragraph_end integer,
    source_start integer NOT NULL,       -- PDF：该页抽取文本内的偏移；文本：解码后原文内偏移
    source_end integer NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (document_id, chunk_index),
    CONSTRAINT document_chunks_index_check CHECK (chunk_index >= 1),
    CONSTRAINT document_chunks_page_check CHECK (page_no IS NULL OR page_no >= 1),
    CONSTRAINT document_chunks_paragraph_check CHECK (
        (paragraph_start IS NULL AND paragraph_end IS NULL)
        OR (paragraph_start >= 1 AND paragraph_end >= paragraph_start)
    )
);

CREATE INDEX document_chunks_page_idx ON document_chunks (document_id, page_no);

ALTER TABLE runs ADD COLUMN document_id uuid REFERENCES documents(id) ON DELETE CASCADE;
ALTER TABLE runs DROP CONSTRAINT runs_kind_check;
ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN ('test.simulation','document.ingest'));
```

`runs.document_id` 只对 `document.ingest` 有意义；`runs.total_steps` 保持 1–100 的既有约束，解析用固定 3 步（见决策 6），因此不需要放宽约束。`ALTER ... DROP CONSTRAINT` 是必要的一次性扩展：`runs_kind_check` 原本是封闭枚举。

**备选方案**：把 `page_no`/段落位置塞进 `run_steps.payload`。否决原因：分块查询需要按 `(document_id, chunk_index)` 与 `page_no` 过滤，JSON 无法承担唯一性与索引职责。

### 2. 原件存储：按标识分目录，数据库只存相对路径

- 存储根由 `DOCUMENT_STORAGE_DIR` 配置（容器内 `/app/data/documents`，本地默认 `<仓库>/data/documents`），Compose 新增命名卷 `echosim-documents`，同时挂载给 `app`（读写）与 `worker`（只读）；`.gitignore` 增加 `data/`，避免本地默认目录下的原件被误提交。
- 正式路径为 `<root>/<project_id>/<document_id>/original.<ext>`，`ext` 由判定出的 `kind` 决定，**完全不含用户文件名**；数据库存的是相对路径。
- 上传时以 64 KiB 为单位流式写入 `runtime` 之外的临时文件，同时计算 SHA-256 与字节数；只有大小校验通过后才落正式路径。

**备选方案**：把原件存进 PostgreSQL `bytea`。否决原因：20 MiB 行会把备份、迁移与连接内存一起放大，且与 `make verify` 的停库/恢复场景互相干扰。

### 3. 上传边界：同步校验，异步解析

| 情况 | 处理时机 | 结果 |
| --- | --- | --- |
| 恰好 20 MiB | 上传请求内 | 通过，进入解析 |
| > 20 MiB（含 +1 字节） | 流式读取中一旦超限即中止 | 413 `file_too_large`，临时文件删除 |
| 0 字节 | 上传请求内 | 400 `empty_file` |
| 扩展名/内容不是 PDF、MD、TXT | 上传请求内（先看扩展名，再看 PDF 魔数） | 415 `unsupported_type` |
| 无文字层、无法解码、原件缺失 | worker 解析时 | 材料 `failed` + 原因 |

类型判定不信任客户端 `Content-Type`：`.pdf` 且前 5 字节为 `%PDF-` 记为 `pdf`；`.md`/`.markdown` 记为 `markdown`；`.txt` 记为 `text`。

上传成功返回 201 `{"document": {...}, "deduplicated": false, "jobId": ..., "runId": ...}`；重复内容返回 200 `{"document": {...}, "deduplicated": true}`，已就绪时不带 `jobId`。

### 4. 解析流水线：读取、清洗、分块三段分开

新增 `app/services/document_extract.py`（纯函数，可单测，不碰数据库）：

```
read(kind, data)            -> RawDocument   # PDF: [(page_no, page_text)]；文本: decoded_text + paragraph spans
clean(raw_document)         -> CleanedText   # 去页眉/页脚/打印路径、统一换行、折叠空白
chunk(cleaned)              -> list[Chunk]   # 句末优先切分 + 定位
```

- **PDF 读取**：逐页调用文本提取库（`pypdf`），`page_no = index + 1`，一次只处理一页，绝不先拼全文。空白页保留 `page_no` 且文本为空。
- **文本读取**：`utf-8-sig` → 带 BOM 的 `utf-16` → `gb18030` 依次尝试；`gb18030` 只有整份解码成功、无替换字符、且控制字符比例低于阈值时才接受。段落按空行切分，记录 `(段落序号, 起止字符偏移)`；此时记录的是**解码后、归一化前**的原文偏移。
- **清洗**：PDF 逐页处理，判定“重复页眉/页脚”用“同一归一化行出现在 ≥ max(3, 60% 页数) 页的页首/页尾区域”，打印路径用模式匹配（`file:///`、`C:\`、`/Users/`、`/home/`、`第 N 页 共 M 页`、孤立页码）。清理只移除命中行，且**只有当页内还剩其他内容时**才执行，避免把有内容的页清成空页。**Markdown/TXT 只做换行归一与空白折叠，不识别也不删除任何路径/URL**，避免误删正文。清洗同时移除 NUL 与其他控制字符（PostgreSQL `text` 不接受 NUL），并保证已记录的偏移仍然指向原文。
- **分块**：目标 `DOCUMENT_CHUNK_TARGET_CHARS=1200`，重叠 `DOCUMENT_CHUNK_OVERLAP_CHARS=120`。先按段落聚合到目标长度；单段超长时按中文句末（`。！？；…`）、英文句末（`. `、`! `、`? `、`; `）、空白、硬切的顺序切分。重叠只发生在同一段落被拆开的相邻块之间，重叠区用原文偏移回退取得，因此每块的 `source_start/source_end` 都是自洽区间，`paragraph_start == paragraph_end`。

**备选方案**：先拼全文再按页偏移反推页码。否决原因：清洗删行后偏移必然漂移，正是需求禁止的做法。

### 5. 定位语义：一列一种基准，前端不做页码算术

- PDF 分块：`page_no` 为物理页码，`source_start/source_end` 是**该页抽取文本内**的偏移。
- 文本分块：`paragraph_start/paragraph_end` 为原始段落序号，`source_start/source_end` 是**解码后原文**内的偏移。
- **偏移单位与区间固定为 UTF-16 代码单元 + 左闭右开**：Python 在记录位置时把前缀编码成 `utf-16-le` 后除以 2 得到数字，前端可直接用 `text.slice(start, end)` 切片；`source_end` 指向最后一个字符之后。这样含 emoji（非 BMP 字符）时 Python 计数与浏览器计数不会错位。
- 服务端为 PDF 分块生成 `sourceUrl = /api/projects/<p>/documents/<d>/file#page=<page_no>`；前端只渲染这个字符串，不自行计算 `page_no`。
- 文本材料的原文视图 MUST 取 `GET .../documents/<d>/source`：服务端用材料记录的 `text_encoding` 重新解码原件并返回 `text/plain; charset=utf-8` 的**未清洗原文**。前端用 `source_start/source_end` 选中的区间与解析时的基准文本完全同源，因此 GB18030 等非 UTF-8 原件也不会因浏览器猜测编码而错位。MUST NOT 让浏览器直接解码 `/file` 原文来做偏移定位。

### 6. 任务接入：用 `kind` 与 `operation_key` 区分两种任务

| 维度 | 连通性测试任务 | 材料解析任务 |
| --- | --- | --- |
| `runs.kind` | `test.simulation` | `document.ingest` |
| `runs.stage` | 五个阶段之一 | 固定 `evidence-graph` |
| `runs.document_id` | NULL | 材料标识 |
| `jobs.operation_key` | `test-simulation:v1` | `document-ingest:v1:<documentId>` |
| 步骤 | 10 步，每步一条 `run_steps` 测试副作用 | 3 步：`read`、`clean`、`index` |
| 终态 | `succeeded/failed/canceled` | 同左，并同步材料状态 |

- `worker.py` 新增按 `runs.kind` 分发：`test.simulation` 走现有 `execute_job`，`document.ingest` 走新增 `execute_ingest`。`Claim` 增加 `kind` 与 `document_id`；领取 SQL 一并返回这两列，不新增查询。
- **材料状态与步骤的对应**：领取时 `documents.status: queued → parsing`；`read`/`clean` 只提交 `run_steps` 与 `progress` 事件（不写分块）；`index` 在**同一事务**内完成“`DELETE` 该材料旧分块 → 批量 `INSERT` 新分块 → `documents.status='ready'`、`page_count`、`chunk_count`、`ready_at` → `run_steps` 行 → `completed` 事件 → job `succeeded`”。因此分块要么全在、要么全不在。
- **失败**：任一步抛出的领域错误在同一事务内写 `failed` 事件、job `failed`、`documents.status='failed'` 与 `failure_code/message`；分块数为 0。
- **取消（与既有取消事务绑定）**：既有取消接口在同一个事务内写 job=`canceled`、追加一条 `canceled` 终态事件，并对 `document.ingest` 额外把材料置 `failed` + `failure_code='canceled'`。因为不依赖 worker，`queued` 任务在 worker 停止时也能立即取消；若完成事务先提交，job 已是 `succeeded`，取消只返回已持久化结果，材料保持 `ready`（`WHERE status IN ('queued','parsing')` 保证不回写）。
- **长步骤心跳**：`pypdf` 的文本提取是同步 CPU 工作，MUST 放在 `asyncio.to_thread()` 里执行，以免阻塞事件循环；`execute_ingest` 在计算期间同时运行一个心跳协程（间隔 `JOB_HEARTBEAT_SECONDS`），提交前停止。续租仍用既有 `heartbeat()`（锁行 + `clock_timestamp()` + 令牌守卫），旧令牌一律零写入。为让验收不依赖“赶在解析完前停 worker”，增加测试用配置 `DOCUMENT_INGEST_DELAY_MS`（默认 0），在线程内解析前等待指定毫秒，把慢解析窗口变成可控同步点。
- **接手**：接手从 `next_step+1` 继续；因为 `read/clean` 不落业务数据、`index` 一次性发布，接手只会重新解析，不会产生重复分块。终态检查先于任何写入。
- **任务创建与可见性**：材料服务在插入材料后直接创建 `runs`（`kind='document.ingest'`、`stage='evidence-graph'`、`total_steps=3`、`document_id`）与 `jobs`（`operation_key='document-ingest:v1:<documentId>'`），不复用只接受 `test-simulation` 的 `jobs.start_job`，也不改变该接口的入参枚举。`GET /api/projects/{p}/jobs` MUST 只返回 `runs.kind='test.simulation'`，因此解析任务不会出现在“测试任务”面板；任务的详情、事件与取消仍按 `jobId` 可查，材料详情把 `jobId` 交给既有取消接口用于取消入库。

**备选方案**：每页一步、边解析边写分块。否决原因：页数会突破 `total_steps 1–100`，且边写边发布必然暴露半成品，需要额外的“代次”列来隐藏，复杂度更高。

### 7. 去重：由部分唯一索引裁决

上传流程：流式写临时文件并算哈希 → `INSERT ... ON CONFLICT (project_id, sha256) WHERE status IN ('queued','parsing','ready') DO NOTHING RETURNING id` → 若没有返回行，说明同内容有效材料已存在，删除临时文件并返回 200 + 既有材料（若其 `queued/parsing`，附上其活动 `jobId`）；若插入成功，把临时文件 `rename` 到正式路径并创建 run/job，再提交。

并发两个相同上传会一个插入成功、一个走冲突分支，因此“只留一套”不依赖应用层先查后写。失败材料不在索引范围内，重新上传同一内容会创建新行与新任务。

**已知代价**：文件重命名与数据库提交不在同一事务内。若进程在两者之间崩溃，可能留下没有数据库记录的孤儿文件；它不可见、不影响去重，只在磁盘上留痕。验收不依赖磁盘清理，风险与后续处理见下。

### 8. 失败原因：稳定标识 + 一句话说明

| `failure_code` | 触发 | 面向用户的说明（中文） |
| --- | --- | --- |
| `empty_file` | 0 字节（同步拒绝） | 文件为空，请选择有内容的文件 |
| `file_too_large` | > 20 MiB（同步拒绝） | 文件超过 20 MiB 上限 |
| `unsupported_type` | 扩展名/魔数不符（同步拒绝） | 仅支持 PDF、Markdown 与 TXT |
| `decode_failed` | 所有候选编码均失败 | 无法以 UTF-8 或可识别的中文编码解码 |
| `no_text_layer` | PDF 全部页面无文字 | 该 PDF 没有文字层，请上传文本型 PDF |
| `original_missing` | 正式路径读取失败 | 原件不可读取，请重新上传 |
| `too_many_pages` | 页数超过 `DOCUMENT_MAX_PAGES`（默认 2000） | 该 PDF 共 N 页，超过当前上限 2000 页 |
| `parse_error` | 其他提取异常 | 解析失败，请重试或更换文件 |
| `canceled` | 用户取消 | 入库已取消 |

同步拒绝返回 `{"detail": <说明>, "code": <标识>}`；异步失败通过材料详情的 `failure` 字段返回同样的 `code` 与说明。日志只记录 `document_id`、`code` 与异常类型，不记录正文、文件名全文或真实路径。

### 9. API 契约

| 方法与路径 | 成功 | 失败 |
| --- | --- | --- |
| `POST /api/projects/{p}/documents`（multipart `file`） | 201 创建 / 200 重复 | 400/401/404/413/415/503 |
| `GET /api/projects/{p}/documents` | 200 `{"documents":[...]}` | 401/404/503 |
| `GET /api/projects/{p}/documents/{d}` | 200 `{"document":{...}}` | 401/404 `材料不存在`/503 |
| `GET /api/projects/{p}/documents/{d}/chunks?after=<序号>&limit=<≤50>` | 200 `{"chunks":[...],"total":N}` | 401/404/422/503 |
| `GET /api/projects/{p}/documents/{d}/source`（仅文本材料） | 200 解码后的未清洗原文（`text/plain; charset=utf-8`） | 401/404/409/503 |
| `GET /api/projects/{p}/documents/{d}/file` | 200 原件字节 | 401/404/503 |

`document` 字段：`documentId`、`name`、`kind`、`contentType`、`sizeBytes`、`sha256`、`status`、`pageCount`、`chunkCount`、`failure`、`createdAt`、`readyAt`、`jobId`、`runId`（活动或最近一次解析任务）。

`chunk` 字段：`index`、`pageNo`、`paragraphStart`、`paragraphEnd`、`sourceStart`、`sourceEnd`、`text`、`sourceUrl`。

`/file` 响应头：`Content-Type: application/pdf` 或 `text/plain; charset=utf-8`，`X-Content-Type-Options: nosniff`，`Content-Disposition: inline; filename*=UTF-8''<percent-encoded>`，`Content-Length` 取数据库记录。所有路由注册在 SPA 回退之前。

### 10. 前端：材料详情留在项目页内

- 新增 `web/src/materials/` 纯逻辑模块：状态映射、大小格式化、`#page=` 链接拼装、原文区间切片；配套 `web/tests/material-source.test.mjs` 做隔离回归（不进入产品数据）。
- `ConsoleView.vue`：材料入口按身份导航（访客 → `/login?next=/projects`，已登录 → `/projects`）；文案改为支持类型与 20 MiB 上限；控制台仍不出现项目名与材料内容。
- `ProjectDetailView.vue` 与 `WorkbenchView.vue` 第一阶段接入同一 `MaterialsPanel` 组件：上传、列表、状态、失败原因与重试、摘录分页。材料详情用 `?document=<id>` 表示（`router.replace` 只改查询参数），不新增路由层级，因此身份、归属、阶段校验与返回路径全部复用现有逻辑，`layout`/`lang` 不丢失。**完整 URL 即材料深链接**：该地址经过登录守卫、项目归属与阶段校验后，必须能刷新或粘贴回到同一材料；匿名访问走既有 `next` 机制回到该完整地址。
- PDF 来源：`<a :href="chunk.sourceUrl" target="_blank" rel="noopener">`，地址由服务端给出。
- 文本来源：同页 `<pre>` 渲染 `/source` 返回的未清洗原文（Vue 插值天然转义），用 `Range` + `scrollIntoView` 定位 `source_start/source_end` 并高亮；MUST NOT 用 `TextDecoder` 自己解码 `/file` 后定位。
- 解析中每 2 秒轮询材料详情，终态后停止；轮询失败不把材料标记为失败，只显示“暂时无法刷新”。

### 11. 验收夹具与断言

`scripts/verify.py` 继续只用标准库：

- **默认流程的轻量材料场景**：生成一份含两段中文的小 TXT，上传后轮询到 `ready`，断言列表可见、分块数至少为 1、匿名读取列表为 401；同一环境连续两次 `make verify` 都必须通过，且不引入 PDF、20 MiB、跨账户或接手场景。
- **三页 PDF**：手写最小 PDF（Helvetica、3 个页对象、第 2 页内容流为空），第 1/3 页写 ASCII 标记 `PAGE-ONE-ECHOSIM-MARKER` / `PAGE-THREE-ECHOSIM-MARKER`（避免引入中文字体嵌入）。断言 `pageCount=3`、第 2 页 0 块、第 3 页 `pageNo=3` 且文本等于第 3 页标记。
- **来源定位**：断言 `sourceUrl` 以 `#page=3` 结尾、`/file` 字节 SHA-256 等于上传值，并用一个约 40 行的结构检查按 `/Kids` 顺序取第 3 个页对象的内容流，确认其中含第 3 页标记。这一步证明“第 3 页摘录对应原件第 3 页”；浏览器 PDF 阅读器是否真的停在第 3 页留给人工记录。
- **PDF 清洗夹具**：三页每页带同一个页眉与含打印路径的页脚，正文写已知行；断言页眉/页脚/打印路径被移除、正文保留、`pageNo` 不变。
- **文本清洗夹具**：含 `CRLF`/`CR`/`LF`、连续空格、emoji 与正文里合法出现的 `C:\data\report.csv`、`file:///tmp/a.txt`；断言换行与空白已归一、路径保留、`source_start/end` 在 `/source` 中切出内容且 emoji 前不偏移。
- **GB18030 夹具**：断言 `/file` 字节哈希与上传一致、`/source` 为 UTF-8 原文且偏移可切回。
- **恰好 20 MiB**：生成“少量正文 + 大段可折叠空白”的 TXT 并精确补齐到 20971520 字节；清洗会折叠空白，因此解析与入库都很快，不会让每次验收插入上万条分块；首次上传必须新增一份材料并最终 `ready`。
- **20 MiB + 1 / 空文件 / 无文字层 PDF**：分别断言 413、400 与材料 `failed: no_text_layer`；只有后两次拒绝断言“材料数不变”。
- **页数上限**：以 `DOCUMENT_MAX_PAGES=3` 启动 worker，断言 3 页 PDF 就绪、4 页 PDF 以 `too_many_pages` 失败且文案包含 3，结束后恢复默认配置。
- **重复上传**：同文件第二次上传断言 200 + 相同 `documentId` + 材料数与分块数不变。
- **隔离**：匿名请求列表/原件/分块为 401；B 请求 A 的原件与分块为 404，响应体与随机标识一致。
- **取消**：worker 停止时上传得到 `queued` 任务，调用取消后不依赖 worker 立即得到 `canceled` 事件与材料 `failed: canceled`、0 块，并能重新上传；另测完成后再取消保持 `ready`。
- **慢解析与接手**：以 `DOCUMENT_INGEST_DELAY_MS` 放大单步耗时并超过租约，断言解析期间不出现 `awaitingTakeover`、`attempts` 不因续租失败而增长；在延迟窗口内停止 worker，租约到期后断言等待接手，恢复后 `attempts` 增加且分块数与一次性解析一致。
- 报告新增 `document_evidence` 列表，字段见 `specs/reproducible-verification/spec.md`；夹具写入系统临时目录并在结束时删除。

### 12. 安全与隐私

- 材料内容按“不可信输入”处理：不执行正文、不解析正文中的指令、不作为 HTML 返回、不写日志。
- 原件路径完全由服务端标识派生；`display_name` 只用于响应头与页面展示，且经过去路径与控制字符处理。
- 上传是写请求，沿用现有同源校验中间件；未携带 Cookie 时不访问数据库。
- 材料接口同样受 `resolve_owned_project` 约束；材料级 404 与项目级 404 用不同文案，但同一类别内部完全等价。

## Risks / Trade-offs

- [`pypdf` 对某些 PDF 的文本抽取质量不稳定，可能把行切碎或漏字] → 以真实 25 页样本做人工验收；清洗与分块不依赖完美抽取，只要求每块保留页与偏移；抽取为空时明确失败而不是产出空块。
- [`gb18030` 几乎能解码任意字节，误判会产出乱码] → 只在整份解码无替换字符、控制字符比例低于阈值、且 CJK/可打印字符占多数时接受；否则按 `decode_failed` 失败。
- [页眉页脚启发式可能误删正文] → 只在“跨页重复 + 位于页首/页尾 + 删除后页内仍有内容”三条同时满足时删除；验收用已知正文核对未被删除。
- [3 步解析中单步耗时可能超过租约] → 计算期间运行独立心跳任务，提交时仍以锁行 + `clock_timestamp()` + 令牌三重守卫判定；失去租约即放弃写入，由接手者重新解析。
- [孤儿原件（文件已写入、数据库未提交）] → 不影响可见性、去重与验收；在 `README` 排障中说明路径可按 `<project>/<document>` 人工清理，后续变更再做自动清理。
- [20 MiB 边界夹具若用 2000 万字符正文会产出上万块并拖慢重复验收] → 夹具用可折叠空白填充字节数，正文保持很小；解析出的分块数只有十几块。
- [材料详情用查询参数而非独立路由，刷新与分享链接依赖查询参数] → 刷新时按 `?document=` 重新读取材料，缺少该参数即回到材料列表；不额外引入一套身份/归属校验。

## Migration Plan

1. 先在已经存在 Change 04 任务的旧数据卷上验证升级：保留一个已完成的 `test.simulation` 任务及其事件，启动新版本让容器入口应用 `0003`，确认旧任务详情、事件与副作用仍可读取，`runs_kind_check` 同时接受两种 kind。
2. 重启 `app` 与 `worker`，确认旧任务仍可读取；再上传一份材料，确认 `document.ingest` 新任务可执行到成功终态。
3. 再次执行 `make migrate` 必须输出“无待应用迁移”；干净数据卷上的首次启动也必须一次成功。
4. 回滚策略：本变更不删除任何既有对象；如需回滚代码，`documents`/`document_chunks` 与新列可保留不动，旧版本代码不会读取它们，`runs_kind_check` 扩展也不影响 `test.simulation`。
5. Compose 新增卷不需要人工初始化；`make reset` 会连同材料一起清除，符合“破坏性”语义。
