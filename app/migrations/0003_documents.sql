-- 0003：项目材料、可定位分块与材料解析任务类型。
-- 仅前向迁移：新增表、列与索引，并在同一事务内扩展 runs.kind 约束。

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uploaded_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    display_name text NOT NULL,
    kind text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL,
    sha256 char(64) NOT NULL,
    storage_path text NOT NULL,
    text_encoding text,
    status text NOT NULL DEFAULT 'queued',
    page_count integer,
    chunk_count integer NOT NULL DEFAULT 0,
    failure_code text,
    failure_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    ready_at timestamptz,
    CONSTRAINT documents_kind_check CHECK (kind IN ('pdf', 'markdown', 'text')),
    CONSTRAINT documents_status_check CHECK (status IN (
        'queued', 'parsing', 'ready', 'failed'
    )),
    CONSTRAINT documents_size_check CHECK (size_bytes > 0),
    CONSTRAINT documents_page_count_check CHECK (page_count IS NULL OR page_count >= 1),
    CONSTRAINT documents_chunk_count_check CHECK (chunk_count >= 0)
);

-- 只有“有效材料”参与去重；失败材料允许重新上传同一内容。
CREATE UNIQUE INDEX documents_project_content_idx
    ON documents (project_id, sha256)
    WHERE status IN ('queued', 'parsing', 'ready');

CREATE INDEX documents_project_created_idx
    ON documents (project_id, created_at DESC);

CREATE TABLE document_chunks (
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index integer NOT NULL,
    page_no integer,
    paragraph_start integer,
    paragraph_end integer,
    source_start integer NOT NULL,
    source_end integer NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (document_id, chunk_index),
    CONSTRAINT document_chunks_index_check CHECK (chunk_index >= 1),
    CONSTRAINT document_chunks_page_check CHECK (page_no IS NULL OR page_no >= 1),
    CONSTRAINT document_chunks_source_check CHECK (
        source_start >= 0 AND source_end >= source_start
    ),
    CONSTRAINT document_chunks_paragraph_check CHECK (
        (paragraph_start IS NULL AND paragraph_end IS NULL)
        OR (paragraph_start >= 1 AND paragraph_end >= paragraph_start)
    )
);

CREATE INDEX document_chunks_page_idx
    ON document_chunks (document_id, page_no);

ALTER TABLE runs
    ADD COLUMN document_id uuid REFERENCES documents(id) ON DELETE CASCADE;

ALTER TABLE runs DROP CONSTRAINT runs_kind_check;

ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN ('test.simulation', 'document.ingest'));
