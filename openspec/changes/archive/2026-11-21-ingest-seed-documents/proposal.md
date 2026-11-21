# Proposal

## Why

控制台的材料入口目前只是一句“尚未开放”的说明，项目里没有任何材料数据源，因此证据图谱、角色与环境等阶段只能显示空态。要让五阶段推演真正开始，第一步是把项目私有材料变成**带来源定位的可检索文本**：用户上传文本型 PDF、Markdown 或 TXT 后，能看到材料与阅读摘录，并从摘录回到原始 PDF 的物理页或文本的原始段落。这一步不做检索排序，也不构建图谱；它只保证“材料是真的、位置是真的”。

## What Changes

- 新增 `0003` 前向迁移：`documents`（材料与解析状态）、`document_chunks`（可定位分块），并给 `runs` 增加可空的 `document_id`、把 `runs_kind_check` 扩展到 `document.ingest`。既有表结构与约束只做扩展，不改语义。
- 新增项目私有材料存储：原件写入 Compose 数据卷，按项目与材料 ID 分目录保存，数据库记录 `sha256` 与相对路径；查看来源时以原件为准，清洗后的文本只用于阅读摘录。
- 新增材料接口：上传（multipart，单文件 ≤ 20 MiB）、列表、详情、分块分页、文本原文视图、原件读取。全部复用 `resolve_owned_project`，匿名 401、他人项目 404、他人或不存在材料 404 对外一致。
- 把解析交给现有 worker：用 `runs.kind = document.ingest` 与 `jobs.operation_key = document-ingest:v1:<documentId>` 与连通性测试任务（`test.simulation` / `test-simulation:v1`）区分；材料有自己的 `queued / parsing / ready / failed` 状态与可读失败原因。
- 定义解析流水线：读取、清洗、分块三段分开。PDF 逐页读取并记录从 1 开始的物理页码，空白页计入总页数且产生零个分块；Markdown/TXT 优先 UTF-8，兼容可可靠识别的中文编码，先记录原始段落位置再统一换行；长段落优先在中文或英文句末切分，允许少量重叠但每块独立定位；正文中的命令与提示一律按普通文本处理。
- 定义去重与幂等：同一项目重复上传相同内容只保留一套有效材料与分块，上传方拿到已有的材料标识；解析失败、重试或 worker 中断接手后，材料列表不出现半成品或重复分块。
- 前端：控制台材料入口从“尚未开放”改为真实入口——访客点击先登录，已登录用户进入自己的项目办理上传；项目详情与第一阶段工作台提供上传、材料列表、阅读摘录与来源定位；材料就绪后证据图谱仍显示“待构建”。
- 验收：默认 `make verify` 增加一条轻量材料回归（小 TXT 上传 → `ready` → 列表可见且至少 1 个分块 → 匿名 401），因为 CI 实际运行这个入口；`CHANGE=2026-11-21-ingest-seed-documents` 追加三页 PDF 页码、PDF/文本清洗、emoji 与 GB18030 偏移、20 MiB 边界、页数上限、去重、401/404、排队取消与慢解析接手。25 页私有样本只在项目作者的 Mac 浏览器人工验收，落页结论由作者签收，不进入仓库、CI 或 `make verify`。

## Capabilities

### New Capabilities

- `document-ingest`: 项目私有材料的原件保存与哈希、支持的类型与大小边界、PDF/Markdown/TXT 的读取与解码、清洗与可定位分块、解析任务的状态与失败原因、同项目内容去重，以及原件与分块的统一归属边界。

### Modified Capabilities

- `guided-console`: “材料入口”由“说明能力尚未开放”改为真实入口：按访客/已登录身份导航到登录或自己的项目，公开控制台仍不展示任何项目数据。
- `project-workbench`: 新增项目材料列表、无材料时的上传入口、材料处理状态，以及从阅读摘录查看原件物理页或文本原始段落的行为；材料就绪不得把证据图谱标记为已完成。
- `reproducible-verification`: 新增 `CHANGE=2026-11-21-ingest-seed-documents` 的材料入库专属场景与报告证据，并声明 25 页私有样本只用于人工验收。

## Impact

- 数据：新增 `app/migrations/0003_documents.sql`（`documents`、`document_chunks`、`runs.document_id`、扩展 `runs_kind_check`）；`documents` 使用 `(project_id, sha256)` 部分唯一索引只约束 `queued/parsing/ready`，失败材料允许重新上传。
- 后端：新增 `app/services/documents.py`、`app/services/ingest.py`（或同职责的拆分模块）、`app/routers/documents.py`、`app/ingest_extract.py`；扩展 `app/services/worker.py` 的任务分发与长步骤心跳；`app/errors.py` 增加材料不存在映射；`app/schemas.py` 增加上传校验。
- 依赖：`app/requirements.txt` 增加纯 Python 的 PDF 文本提取库与 multipart 表单支持，均锁定精确版本。
- 编排：`docker-compose.yml` 增加材料数据卷并挂载到 `app` 与 `worker`；新增 `DOCUMENT_STORAGE_DIR`、`DOCUMENT_MAX_BYTES`、`DOCUMENT_MAX_PAGES`、分块目标/重叠与 `DOCUMENT_INGEST_DELAY_MS`（测试用慢解析窗口）等配置，同步 `.env.example`、`README.md` 配置表与 `app/config.py`。
- 前端：新增材料列表/上传/详情与来源定位组件、`web/src/i18n.ts` 中英文案、项目详情与控制台入口；`WorkbenchView.vue` 的第一阶段接入真实材料数据，同时保持 `layout`/`lang` 与“图谱待构建”状态。
- 验收：`scripts/verify.py` 增加 `CHANGE` 注册、自制 PDF/文本夹具生成、材料场景与 `document_evidence` 报告字段；`web/tests/` 增加纯函数回归。
- 兼容性：不改变 `/health`、`/livez`、`/api/auth/*`、`/api/projects*`、`/api/history`、`/api/projects/*/jobs*` 的既有契约；`make verify` 不带 `CHANGE` 时仍执行 Change 01–04 全部场景，`CHANGE=2026-11-21-track-background-jobs` 仍保留其全部断言。
- 非目标：OCR 与扫描件、图片/图表抽取、向量化与检索排序、证据图谱计算、真实双平台推演、报告生成、批量上传、材料删除/重命名/移动、跨项目去重、对象存储与 CDN。
