-- 0013：双平台运行的任务类型、运行记录、平台状态、轮次游标与动作记录。
-- 只做前向迁移：旧 run、准备、冻结选择、图谱与材料保持原值，不回填。

ALTER TABLE runs DROP CONSTRAINT runs_kind_check;

ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN (
        'test.simulation',
        'document.ingest',
        'evidence.graph.build',
        'agent.environment.prepare',
        'simulation.run'
    ));

-- 运行通过复合外键绑定“同一项目的某一份冻结选择”与“同一项目的准备”。
ALTER TABLE environment_preparations
    ADD CONSTRAINT environment_preparations_project_id_unique
    UNIQUE (project_id, id);

ALTER TABLE environment_frozen_selections
    ADD CONSTRAINT environment_frozen_selections_project_selection_unique
    UNIQUE (project_id, selection_id);

CREATE TABLE simulations (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    selection_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    build_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    question text NOT NULL,
    rounds integer NOT NULL,
    minutes_per_round integer NOT NULL,
    duration_hours integer NOT NULL,
    engine_mode text NOT NULL,
    selection_payload jsonb NOT NULL,
    phase text NOT NULL DEFAULT 'queued',
    failure_code text,
    failed_platforms text[] NOT NULL DEFAULT '{}'::text[],
    simulated_start timestamptz NOT NULL,
    provider_calls_real integer NOT NULL DEFAULT 0,
    provider_calls_double integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT simulations_run_unique UNIQUE (run_id),
    CONSTRAINT simulations_id_selection_unique UNIQUE (id, selection_id),
    CONSTRAINT simulations_phase_check CHECK (phase IN (
        'queued', 'running', 'succeeded', 'failed', 'canceled'
    )),
    CONSTRAINT simulations_engine_mode_check CHECK (
        engine_mode IN ('double', 'oasis')
    ),
    CONSTRAINT simulations_rounds_check CHECK (
        rounds BETWEEN 1 AND 1000
        AND minutes_per_round BETWEEN 1 AND 1440
        AND duration_hours BETWEEN 1 AND 720
    ),
    CONSTRAINT simulations_question_check CHECK (
        char_length(btrim(question)) BETWEEN 1 AND 500
    ),
    CONSTRAINT simulations_counts_check CHECK (
        provider_calls_real >= 0 AND provider_calls_double >= 0
    ),
    CONSTRAINT simulations_payload_check CHECK (
        jsonb_typeof(selection_payload) = 'object'
    ),
    CONSTRAINT simulations_failed_platforms_check CHECK (
        failed_platforms <@ ARRAY['platform_a', 'platform_b']::text[]
    ),
    CONSTRAINT simulations_selection_fk
        FOREIGN KEY (project_id, selection_id)
        REFERENCES environment_frozen_selections (project_id, selection_id)
        ON DELETE CASCADE,
    CONSTRAINT simulations_build_fk
        FOREIGN KEY (project_id, build_id)
        REFERENCES graph_builds (project_id, id) ON DELETE CASCADE,
    CONSTRAINT simulations_snapshot_fk
        FOREIGN KEY (build_id, snapshot_id)
        REFERENCES graph_snapshots (build_id, id) ON DELETE CASCADE,
    CONSTRAINT simulations_preparation_fk
        FOREIGN KEY (project_id, preparation_id)
        REFERENCES environment_preparations (project_id, id) ON DELETE CASCADE
);

CREATE INDEX simulations_project_created_idx
    ON simulations (project_id, created_at DESC, id DESC);

CREATE INDEX simulations_selection_created_idx
    ON simulations (project_id, selection_id, created_at DESC);

CREATE TABLE simulation_platforms (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    platform_key text NOT NULL,
    native_platform text NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    rounds_completed integer NOT NULL DEFAULT 0,
    action_count integer NOT NULL DEFAULT 0,
    by_action jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_code text,
    engine_rel_dir text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (simulation_id, platform_key),
    CONSTRAINT simulation_platforms_key_check CHECK (
        platform_key IN ('platform_a', 'platform_b')
    ),
    CONSTRAINT simulation_platforms_mapping_check CHECK (
        (platform_key = 'platform_a' AND native_platform = 'twitter')
        OR (platform_key = 'platform_b' AND native_platform = 'reddit')
    ),
    CONSTRAINT simulation_platforms_status_check CHECK (
        status IN ('pending', 'running', 'succeeded', 'failed', 'canceled')
    ),
    CONSTRAINT simulation_platforms_counts_check CHECK (
        rounds_completed >= 0 AND action_count >= 0
        AND jsonb_typeof(by_action) = 'object'
    )
);

CREATE TABLE simulation_rounds (
    simulation_id uuid NOT NULL,
    platform_key text NOT NULL,
    round_index integer NOT NULL,
    simulated_at timestamptz NOT NULL,
    action_count integer NOT NULL DEFAULT 0,
    engine_write_outcome text NOT NULL DEFAULT 'committed',
    committed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, platform_key, round_index),
    CONSTRAINT simulation_rounds_index_check CHECK (round_index >= 1),
    CONSTRAINT simulation_rounds_count_check CHECK (action_count >= 0),
    CONSTRAINT simulation_rounds_outcome_check CHECK (
        engine_write_outcome IN ('in_flight', 'committed', 'unconfirmed')
    ),
    CONSTRAINT simulation_rounds_platform_fk
        FOREIGN KEY (simulation_id, platform_key)
        REFERENCES simulation_platforms (simulation_id, platform_key)
        ON DELETE CASCADE
);

CREATE TABLE simulation_actions (
    id uuid PRIMARY KEY,
    simulation_id uuid NOT NULL,
    preparation_id uuid NOT NULL,
    platform_key text NOT NULL,
    native_platform text NOT NULL,
    round_index integer NOT NULL,
    action_seq integer NOT NULL,
    native_action text NOT NULL,
    actor_role_id uuid NOT NULL,
    actor_display_name text NOT NULL,
    content text NOT NULL DEFAULT '',
    engine_record_id text NOT NULL,
    engine_sequence bigint,
    target_engine_record_id text,
    target_action_id uuid,
    target_platform_key text,
    target_native_action text,
    target_resolved boolean NOT NULL DEFAULT false,
    simulated_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT simulation_actions_record_unique
        UNIQUE (simulation_id, platform_key, engine_record_id),
    CONSTRAINT simulation_actions_seq_unique
        UNIQUE (simulation_id, action_seq),
    CONSTRAINT simulation_actions_seq_check CHECK (
        action_seq >= 1 AND round_index >= 1
    ),
    CONSTRAINT simulation_actions_native_check CHECK (
        (platform_key = 'platform_a' AND native_action IN (
            'create_post', 'repost', 'like_post'
        ))
        OR (platform_key = 'platform_b' AND native_action IN (
            'create_post', 'create_comment', 'like_post', 'dislike_post'
        ))
    ),
    CONSTRAINT simulation_actions_mapping_check CHECK (
        (platform_key = 'platform_a' AND native_platform = 'twitter')
        OR (platform_key = 'platform_b' AND native_platform = 'reddit')
    ),
    CONSTRAINT simulation_actions_round_fk
        FOREIGN KEY (simulation_id, platform_key, round_index)
        REFERENCES simulation_rounds (simulation_id, platform_key, round_index)
        ON DELETE CASCADE,
    CONSTRAINT simulation_actions_role_fk
        FOREIGN KEY (preparation_id, actor_role_id)
        REFERENCES environment_roles (preparation_id, role_id)
        ON DELETE CASCADE,
    CONSTRAINT simulation_actions_target_fk
        FOREIGN KEY (target_action_id)
        REFERENCES simulation_actions (id) ON DELETE SET NULL
);

CREATE INDEX simulation_actions_platform_seq_idx
    ON simulation_actions (simulation_id, platform_key, action_seq);

CREATE INDEX simulation_actions_target_idx
    ON simulation_actions (simulation_id, target_engine_record_id);
