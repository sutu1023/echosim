-- 0009：证据图谱语义来源性质、build 级图谱标识与真实 provider 独立绑定。
-- 只做前向迁移；旧绑定、旧 build 与旧快照保持原值，不回填。

ALTER TABLE graph_edges
    ADD COLUMN claim_nature text;

ALTER TABLE graph_edges
    ADD CONSTRAINT graph_edges_claim_nature_check CHECK (
        claim_nature IS NULL OR claim_nature IN (
            'media_report',
            'party_claim',
            'official_decision',
            'other_material'
        )
    );

ALTER TABLE graph_builds
    ADD COLUMN graph_id uuid;

CREATE INDEX graph_builds_graph_id_idx
    ON graph_builds (graph_id)
    WHERE graph_id IS NOT NULL;

CREATE TABLE project_graph_real_bindings (
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
    CONSTRAINT project_graph_real_bindings_key_unique UNIQUE (create_idempotency_key),
    CONSTRAINT project_graph_real_bindings_graph_unique UNIQUE (graph_id),
    CONSTRAINT project_graph_real_bindings_state_check CHECK (
        state IN ('pending', 'bound')
    ),
    CONSTRAINT project_graph_real_bindings_state_graph_check CHECK (
        (state = 'pending' AND graph_id IS NULL)
        OR (state = 'bound' AND graph_id IS NOT NULL)
    ),
    CONSTRAINT project_graph_real_bindings_request_check CHECK (
        char_length(btrim(request_name)) >= 1
        AND char_length(btrim(request_description)) >= 1
        AND char_length(btrim(request_time_zone)) >= 1
    )
);
