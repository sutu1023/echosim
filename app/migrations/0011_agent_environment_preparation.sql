-- 0011：角色与环境准备的任务类型、准备绑定、角色分层、环境配置与冻结选择。
-- 只做前向迁移：旧 run、build、快照、材料与测试任务保持原值，不回填。

ALTER TABLE runs DROP CONSTRAINT runs_kind_check;

ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN (
        'test.simulation',
        'document.ingest',
        'evidence.graph.build',
        'agent.environment.prepare'
    ));

-- 复合外键需要稳定的“同一 build / 同一项目”引用目标。
ALTER TABLE graph_snapshots
    ADD CONSTRAINT graph_snapshots_build_id_id_unique UNIQUE (build_id, id);

ALTER TABLE graph_builds
    ADD CONSTRAINT graph_builds_project_id_id_unique UNIQUE (project_id, id);

CREATE TABLE environment_preparations (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    build_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question text NOT NULL,
    phase text NOT NULL DEFAULT 'preparing',
    duration_hours integer NOT NULL DEFAULT 72,
    minutes_per_round integer NOT NULL DEFAULT 60,
    rounds_mode text NOT NULL DEFAULT 'auto',
    manual_rounds integer,
    rounds integer NOT NULL DEFAULT 72,
    settings_revision integer NOT NULL DEFAULT 1,
    confirmed_settings_revision integer,
    preview_role_count integer NOT NULL DEFAULT 0,
    profile_batch_count integer NOT NULL DEFAULT 0,
    provider_calls_real integer NOT NULL DEFAULT 0,
    provider_calls_double integer NOT NULL DEFAULT 0,
    failure_code text,
    failure_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT environment_preparations_run_unique UNIQUE (run_id),
    CONSTRAINT environment_preparations_id_snapshot_unique UNIQUE (id, snapshot_id),
    CONSTRAINT environment_preparations_build_project_fk
        FOREIGN KEY (project_id, build_id)
        REFERENCES graph_builds (project_id, id) ON DELETE CASCADE,
    CONSTRAINT environment_preparations_snapshot_build_fk
        FOREIGN KEY (build_id, snapshot_id)
        REFERENCES graph_snapshots (build_id, id) ON DELETE CASCADE,
    CONSTRAINT environment_preparations_phase_check CHECK (phase IN (
        'preparing', 'ready', 'failed', 'canceled'
    )),
    CONSTRAINT environment_preparations_question_check CHECK (
        char_length(btrim(question)) BETWEEN 1 AND 500
    ),
    CONSTRAINT environment_preparations_rounds_mode_check CHECK (
        rounds_mode IN ('auto', 'manual')
    ),
    CONSTRAINT environment_preparations_rounds_check CHECK (
        duration_hours BETWEEN 1 AND 720
        AND minutes_per_round BETWEEN 1 AND 1440
        AND rounds BETWEEN 1 AND 1000
        AND (manual_rounds IS NULL OR manual_rounds BETWEEN 1 AND 1000)
        AND (rounds_mode <> 'manual' OR manual_rounds IS NOT NULL)
    ),
    CONSTRAINT environment_preparations_revision_check CHECK (
        settings_revision >= 1
        AND (confirmed_settings_revision IS NULL
             OR confirmed_settings_revision >= 1)
    ),
    CONSTRAINT environment_preparations_counts_check CHECK (
        preview_role_count >= 0
        AND profile_batch_count >= 0
        AND provider_calls_real >= 0
        AND provider_calls_double >= 0
    )
);

-- 同一 build 最多一个活动准备；失败/取消后允许显式重试创建新的准备。
CREATE UNIQUE INDEX environment_preparations_active_build_idx
    ON environment_preparations (build_id)
    WHERE phase IN ('preparing', 'ready');

CREATE INDEX environment_preparations_project_created_idx
    ON environment_preparations (project_id, created_at DESC, id DESC);

CREATE TABLE environment_roles (
    role_id uuid PRIMARY KEY,
    preparation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    node_key text NOT NULL,
    display_name text NOT NULL,
    role_type text NOT NULL,
    batch_index integer NOT NULL DEFAULT 0,
    mention jsonb NOT NULL DEFAULT '{}'::jsonb,
    background jsonb NOT NULL DEFAULT '{}'::jsonb,
    expressed_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
    involved_topics jsonb NOT NULL DEFAULT '[]'::jsonb,
    simulation jsonb,
    preview_ready_at timestamptz,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT environment_roles_preparation_node_unique
        UNIQUE (preparation_id, node_key),
    CONSTRAINT environment_roles_preparation_role_unique
        UNIQUE (preparation_id, role_id),
    CONSTRAINT environment_roles_preparation_snapshot_role_unique
        UNIQUE (preparation_id, snapshot_id, role_id),
    CONSTRAINT environment_roles_preparation_fk
        FOREIGN KEY (preparation_id, snapshot_id)
        REFERENCES environment_preparations (id, snapshot_id) ON DELETE CASCADE,
    CONSTRAINT environment_roles_node_fk
        FOREIGN KEY (snapshot_id, node_key)
        REFERENCES graph_nodes (snapshot_id, node_key) ON DELETE CASCADE,
    CONSTRAINT environment_roles_batch_check CHECK (batch_index >= 0),
    CONSTRAINT environment_roles_display_check CHECK (
        char_length(btrim(display_name)) >= 1
    ),
    CONSTRAINT environment_roles_publish_check CHECK (
        published_at IS NULL OR preview_ready_at IS NOT NULL
    )
);

CREATE INDEX environment_roles_preparation_batch_idx
    ON environment_roles (preparation_id, batch_index, role_id);

CREATE TABLE environment_role_sources (
    preparation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    role_id uuid NOT NULL,
    material_field text NOT NULL,
    field_index integer NOT NULL DEFAULT 0,
    source_index integer NOT NULL,
    target_kind text NOT NULL,
    target_key text NOT NULL,
    evidence_index integer NOT NULL,
    claim_nature text,
    PRIMARY KEY (
        preparation_id, role_id, material_field, field_index, source_index
    ),
    CONSTRAINT environment_role_sources_field_check CHECK (material_field IN (
        'mention', 'background', 'expressedTopics', 'involvedTopics'
    )),
    CONSTRAINT environment_role_sources_index_check CHECK (
        field_index >= 0 AND source_index >= 1
    ),
    CONSTRAINT environment_role_sources_kind_check CHECK (
        target_kind IN ('node', 'edge')
    ),
    CONSTRAINT environment_role_sources_claim_check CHECK (
        claim_nature IS NULL OR claim_nature IN (
            'media_report',
            'party_claim',
            'author_recommendation',
            'official_decision',
            'other_material'
        )
    ),
    CONSTRAINT environment_role_sources_role_fk
        FOREIGN KEY (preparation_id, snapshot_id, role_id)
        REFERENCES environment_roles (preparation_id, snapshot_id, role_id)
        ON DELETE CASCADE,
    CONSTRAINT environment_role_sources_evidence_fk
        FOREIGN KEY (snapshot_id, target_kind, target_key, evidence_index)
        REFERENCES graph_evidence (
            snapshot_id, target_kind, target_key, evidence_index
        ) ON DELETE CASCADE
);

CREATE INDEX environment_role_sources_role_idx
    ON environment_role_sources (preparation_id, role_id, material_field);

CREATE TABLE environment_platforms (
    preparation_id uuid NOT NULL
        REFERENCES environment_preparations(id) ON DELETE CASCADE,
    platform_key text NOT NULL,
    native_platform text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (preparation_id, platform_key),
    CONSTRAINT environment_platforms_key_check CHECK (
        platform_key IN ('platform_a', 'platform_b')
    ),
    CONSTRAINT environment_platforms_mapping_check CHECK (
        (platform_key = 'platform_a' AND native_platform = 'twitter')
        OR (platform_key = 'platform_b' AND native_platform = 'reddit')
    )
);

CREATE TABLE environment_topics (
    preparation_id uuid NOT NULL
        REFERENCES environment_preparations(id) ON DELETE CASCADE,
    topic_id text NOT NULL,
    topic_index integer NOT NULL,
    text text NOT NULL,
    origin text NOT NULL,
    claim_nature text,
    claim_nature_known boolean NOT NULL DEFAULT false,
    sources jsonb NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (preparation_id, topic_id),
    CONSTRAINT environment_topics_index_unique UNIQUE (preparation_id, topic_index),
    CONSTRAINT environment_topics_topic_check CHECK (
        char_length(btrim(topic_id)) >= 1 AND topic_index >= 0
        AND char_length(btrim(text)) >= 1
    ),
    CONSTRAINT environment_topics_origin_check CHECK (origin IN (
        'question', 'material', 'simulation'
    )),
    CONSTRAINT environment_topics_claim_check CHECK (
        claim_nature IS NULL OR claim_nature IN (
            'media_report',
            'party_claim',
            'author_recommendation',
            'official_decision',
            'other_material'
        )
    ),
    CONSTRAINT environment_topics_known_check CHECK (
        NOT claim_nature_known OR claim_nature IS NOT NULL
    )
);

CREATE TABLE environment_activation (
    preparation_id uuid NOT NULL
        REFERENCES environment_preparations(id) ON DELETE CASCADE,
    position integer NOT NULL,
    role_id uuid NOT NULL,
    is_first_speaker boolean NOT NULL DEFAULT false,
    first_topic_id text,
    PRIMARY KEY (preparation_id, position),
    CONSTRAINT environment_activation_role_unique
        UNIQUE (preparation_id, role_id),
    CONSTRAINT environment_activation_position_check CHECK (position >= 1),
    CONSTRAINT environment_activation_role_fk
        FOREIGN KEY (preparation_id, role_id)
        REFERENCES environment_roles (preparation_id, role_id) ON DELETE CASCADE,
    CONSTRAINT environment_activation_topic_fk
        FOREIGN KEY (preparation_id, first_topic_id)
        REFERENCES environment_topics (preparation_id, topic_id)
        ON DELETE RESTRICT
);

CREATE INDEX environment_activation_role_idx
    ON environment_activation (preparation_id, role_id);

CREATE TABLE environment_frozen_selections (
    selection_id uuid PRIMARY KEY,
    preparation_id uuid NOT NULL UNIQUE
        REFERENCES environment_preparations(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    build_id uuid NOT NULL REFERENCES graph_builds(id) ON DELETE CASCADE,
    snapshot_id uuid NOT NULL REFERENCES graph_snapshots(id) ON DELETE CASCADE,
    settings_revision integer NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT environment_frozen_selections_revision_check CHECK (
        settings_revision >= 1
    ),
    CONSTRAINT environment_frozen_selections_payload_check CHECK (
        jsonb_typeof(payload) = 'object'
    )
);
