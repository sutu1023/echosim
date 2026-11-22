-- 0005：业务证据图谱的项目绑定、建图任务、幂等 episode、快照、节点、边与来源。
-- 仅前向迁移：扩展 runs.kind，并新增表、约束与索引；旧材料与旧任务数据保持不变。

ALTER TABLE runs DROP CONSTRAINT runs_kind_check;

ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN ('test.simulation', 'document.ingest', 'evidence.graph.build'));

CREATE TABLE project_graph_bindings (
    project_id uuid PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    create_idempotency_key uuid NOT NULL,
    request_name text NOT NULL,
    request_description text NOT NULL,
    request_time_zone text NOT NULL,
    graph_id uuid,
    state text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    bound_at timestamptz,
    CONSTRAINT project_graph_bindings_key_unique UNIQUE (create_idempotency_key),
    CONSTRAINT project_graph_bindings_graph_unique UNIQUE (graph_id),
    CONSTRAINT project_graph_bindings_state_check CHECK (
        state IN ('pending', 'bound')
    ),
    CONSTRAINT project_graph_bindings_state_graph_check CHECK (
        (state = 'pending' AND graph_id IS NULL)
        OR (state = 'bound' AND graph_id IS NOT NULL)
    ),
    CONSTRAINT project_graph_bindings_request_check CHECK (
        char_length(btrim(request_name)) >= 1
        AND char_length(btrim(request_description)) >= 1
        AND char_length(btrim(request_time_zone)) >= 1
    )
);

CREATE TABLE graph_builds (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question text NOT NULL,
    phase text NOT NULL DEFAULT 'preparing',
    planned_episodes integer NOT NULL DEFAULT 0,
    accepted_episodes integer NOT NULL DEFAULT 0,
    confirmed_episodes integer NOT NULL DEFAULT 0,
    can_continue boolean NOT NULL DEFAULT false,
    failure_code text,
    failure_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT graph_builds_run_unique UNIQUE (run_id),
    CONSTRAINT graph_builds_question_check CHECK (
        char_length(btrim(question)) BETWEEN 1 AND 500
    ),
    CONSTRAINT graph_builds_phase_check CHECK (phase IN (
        'preparing', 'waiting_index', 'drawing', 'ready', 'failed', 'canceled'
    )),
    CONSTRAINT graph_builds_counts_check CHECK (
        planned_episodes >= 0
        AND accepted_episodes >= 0
        AND confirmed_episodes >= 0
    )
);

CREATE INDEX graph_builds_project_created_idx
    ON graph_builds (project_id, created_at DESC, id DESC);

CREATE TABLE graph_build_documents (
    build_id uuid NOT NULL REFERENCES graph_builds(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_sha256 char(64) NOT NULL,
    chunk_count integer NOT NULL DEFAULT 0,
    ordinal integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (build_id, document_id),
    CONSTRAINT graph_build_documents_ordinal_unique UNIQUE (build_id, ordinal),
    CONSTRAINT graph_build_documents_ordinal_check CHECK (ordinal >= 1),
    CONSTRAINT graph_build_documents_chunk_count_check CHECK (chunk_count >= 0)
);

CREATE INDEX graph_build_documents_document_idx
    ON graph_build_documents (document_id);

CREATE TABLE graph_episodes (
    build_id uuid NOT NULL REFERENCES graph_builds(id) ON DELETE CASCADE,
    episode_key text NOT NULL,
    idempotency_key uuid NOT NULL,
    content text NOT NULL,
    content_sha256 char(64) NOT NULL,
    episode_uuid uuid,
    state text NOT NULL DEFAULT 'planned',
    attempts integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (build_id, episode_key),
    CONSTRAINT graph_episodes_idempotency_unique UNIQUE (idempotency_key),
    CONSTRAINT graph_episodes_state_check CHECK (state IN (
        'planned', 'written', 'matched', 'processed', 'failed'
    )),
    CONSTRAINT graph_episodes_attempts_check CHECK (attempts >= 0)
);

CREATE INDEX graph_episodes_build_state_idx
    ON graph_episodes (build_id, state);

CREATE INDEX graph_episodes_uuid_idx
    ON graph_episodes (episode_uuid)
    WHERE episode_uuid IS NOT NULL;

CREATE TABLE graph_snapshots (
    id uuid PRIMARY KEY,
    build_id uuid NOT NULL REFERENCES graph_builds(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    complete boolean NOT NULL DEFAULT false,
    published boolean NOT NULL DEFAULT false,
    node_count integer NOT NULL DEFAULT 0,
    edge_count integer NOT NULL DEFAULT 0,
    episode_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz,
    CONSTRAINT graph_snapshots_revision_unique UNIQUE (build_id, revision),
    CONSTRAINT graph_snapshots_revision_check CHECK (revision >= 1),
    CONSTRAINT graph_snapshots_counts_check CHECK (
        node_count >= 0 AND edge_count >= 0 AND episode_count >= 0
    )
);

CREATE UNIQUE INDEX graph_snapshots_final_idx
    ON graph_snapshots (build_id)
    WHERE complete AND published;

CREATE INDEX graph_snapshots_published_idx
    ON graph_snapshots (build_id, revision DESC)
    WHERE published;

CREATE TABLE graph_nodes (
    snapshot_id uuid NOT NULL REFERENCES graph_snapshots(id) ON DELETE CASCADE,
    node_key text NOT NULL,
    node_type text NOT NULL,
    label text NOT NULL,
    origin text NOT NULL,
    summary text,
    PRIMARY KEY (snapshot_id, node_key),
    CONSTRAINT graph_nodes_origin_check CHECK (
        origin IN ('material', 'hypothesis')
    ),
    CONSTRAINT graph_nodes_key_check CHECK (char_length(btrim(node_key)) >= 1)
);

CREATE TABLE graph_edges (
    snapshot_id uuid NOT NULL REFERENCES graph_snapshots(id) ON DELETE CASCADE,
    edge_key text NOT NULL,
    edge_type text NOT NULL,
    label text NOT NULL,
    origin text NOT NULL,
    summary text,
    source_node_key text NOT NULL,
    target_node_key text NOT NULL,
    PRIMARY KEY (snapshot_id, edge_key),
    CONSTRAINT graph_edges_origin_check CHECK (
        origin IN ('material', 'hypothesis')
    ),
    CONSTRAINT graph_edges_key_check CHECK (char_length(btrim(edge_key)) >= 1),
    CONSTRAINT graph_edges_source_fk FOREIGN KEY (snapshot_id, source_node_key)
        REFERENCES graph_nodes (snapshot_id, node_key) ON DELETE CASCADE,
    CONSTRAINT graph_edges_target_fk FOREIGN KEY (snapshot_id, target_node_key)
        REFERENCES graph_nodes (snapshot_id, node_key) ON DELETE CASCADE
);

CREATE TABLE graph_evidence (
    snapshot_id uuid NOT NULL REFERENCES graph_snapshots(id) ON DELETE CASCADE,
    target_kind text NOT NULL,
    target_key text NOT NULL,
    evidence_index integer NOT NULL,
    document_id uuid NOT NULL,
    chunk_index integer NOT NULL,
    page_no integer,
    paragraph_start integer,
    paragraph_end integer,
    source_start integer NOT NULL,
    source_end integer NOT NULL,
    quote text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (snapshot_id, target_kind, target_key, evidence_index),
    CONSTRAINT graph_evidence_target_kind_check CHECK (
        target_kind IN ('node', 'edge')
    ),
    CONSTRAINT graph_evidence_index_check CHECK (evidence_index >= 1),
    CONSTRAINT graph_evidence_page_check CHECK (page_no IS NULL OR page_no >= 1),
    CONSTRAINT graph_evidence_source_check CHECK (
        source_start >= 0 AND source_end > source_start
    ),
    CONSTRAINT graph_evidence_paragraph_check CHECK (
        (paragraph_start IS NULL AND paragraph_end IS NULL)
        OR (paragraph_start >= 1 AND paragraph_end >= paragraph_start)
    ),
    CONSTRAINT graph_evidence_quote_check CHECK (char_length(quote) >= 1),
    CONSTRAINT graph_evidence_chunk_fk FOREIGN KEY (document_id, chunk_index)
        REFERENCES document_chunks (document_id, chunk_index) ON DELETE CASCADE
);

CREATE INDEX graph_evidence_document_chunk_idx
    ON graph_evidence (document_id, chunk_index);

CREATE INDEX graph_evidence_snapshot_target_idx
    ON graph_evidence (snapshot_id, target_kind, target_key);
