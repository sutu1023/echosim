-- 0015：第三阶段时序记忆、运行级图谱绑定、按轮读模型与记忆同步任务。
-- 只做前向迁移：旧运行默认 memory_status='unconfigured'，不回填、不改写旧图谱快照。

ALTER TABLE runs DROP CONSTRAINT runs_kind_check;

ALTER TABLE runs ADD CONSTRAINT runs_kind_check
    CHECK (kind IN (
        'test.simulation',
        'document.ingest',
        'evidence.graph.build',
        'agent.environment.prepare',
        'simulation.run',
        'simulation.memory'
    ));

ALTER TABLE simulations
    ADD COLUMN memory_status text NOT NULL DEFAULT 'unconfigured',
    ADD COLUMN memory_last_synced_round integer NOT NULL DEFAULT 0,
    ADD COLUMN memory_started_at timestamptz,
    ADD COLUMN memory_finished_at timestamptz,
    ADD COLUMN memory_failure_code text,
    ADD COLUMN memory_calls_real integer NOT NULL DEFAULT 0,
    ADD COLUMN memory_calls_double integer NOT NULL DEFAULT 0;

ALTER TABLE simulations
    ADD CONSTRAINT simulations_memory_status_check CHECK (memory_status IN (
        'unconfigured', 'pending', 'syncing', 'complete',
        'partial', 'unconfirmed', 'unavailable'
    ));

ALTER TABLE simulations
    ADD CONSTRAINT simulations_memory_round_check CHECK (
        memory_last_synced_round >= 0
    );

ALTER TABLE simulations
    ADD CONSTRAINT simulations_memory_calls_check CHECK (
        memory_calls_real >= 0 AND memory_calls_double >= 0
    );

CREATE TABLE simulation_graph_bindings (
    simulation_id uuid PRIMARY KEY REFERENCES simulations(id) ON DELETE CASCADE,
    create_idempotency_key uuid NOT NULL,
    request_name text NOT NULL,
    request_description text NOT NULL,
    request_time_zone text NOT NULL,
    graph_id uuid,
    state text NOT NULL DEFAULT 'pending',
    created_at timestamptz NOT NULL DEFAULT now(),
    bound_at timestamptz,
    CONSTRAINT simulation_graph_bindings_key_unique
        UNIQUE (create_idempotency_key),
    CONSTRAINT simulation_graph_bindings_graph_unique UNIQUE (graph_id),
    CONSTRAINT simulation_graph_bindings_state_check CHECK (
        state IN ('pending', 'bound')
    ),
    CONSTRAINT simulation_graph_bindings_state_graph_check CHECK (
        (state = 'pending' AND graph_id IS NULL)
        OR (state = 'bound' AND graph_id IS NOT NULL)
    )
);

CREATE TABLE simulation_memory_entries (
    id uuid PRIMARY KEY,
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    action_id uuid NOT NULL REFERENCES simulation_actions(id) ON DELETE CASCADE,
    platform_key text NOT NULL,
    round_index integer NOT NULL,
    action_seq integer NOT NULL,
    actor_role_id uuid NOT NULL,
    actor_display_name text NOT NULL,
    speech_text text NOT NULL DEFAULT '',
    native_action text NOT NULL,
    target_action_id uuid,
    target_engine_record_id text,
    simulated_at timestamptz NOT NULL,
    source_event_seq integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT simulation_memory_entries_action_unique
        UNIQUE (simulation_id, action_id),
    CONSTRAINT simulation_memory_entries_simulation_id_unique
        UNIQUE (simulation_id, id),
    CONSTRAINT simulation_memory_entries_key_check CHECK (
        round_index >= 1 AND action_seq >= 1 AND source_event_seq >= 1
    ),
    CONSTRAINT simulation_memory_entries_platform_check CHECK (
        platform_key IN ('platform_a', 'platform_b')
    )
);

CREATE INDEX simulation_memory_entries_round_idx
    ON simulation_memory_entries (
        simulation_id, platform_key, round_index, action_seq
    );

CREATE TABLE simulation_memory_episodes (
    simulation_id uuid NOT NULL,
    memory_id uuid NOT NULL,
    action_id uuid NOT NULL,
    episode_key text NOT NULL,
    idempotency_key uuid NOT NULL,
    content text NOT NULL,
    content_sha256 char(64) NOT NULL,
    episode_uuid uuid,
    state text NOT NULL DEFAULT 'planned',
    write_outcome text NOT NULL DEFAULT 'not_attempted',
    attempts integer NOT NULL DEFAULT 0,
    add_count integer NOT NULL DEFAULT 0,
    round_index integer NOT NULL,
    simulated_at timestamptz NOT NULL,
    source_event_seq integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, memory_id),
    CONSTRAINT simulation_memory_episodes_memory_fk
        FOREIGN KEY (simulation_id, memory_id)
        REFERENCES simulation_memory_entries (simulation_id, id)
        ON DELETE CASCADE,
    CONSTRAINT simulation_memory_episodes_key_unique UNIQUE (idempotency_key),
    CONSTRAINT simulation_memory_episodes_uuid_unique UNIQUE (episode_uuid),
    CONSTRAINT simulation_memory_episodes_state_check CHECK (state IN (
        'planned', 'accepted', 'matched', 'processed',
        'unconfirmed', 'failed'
    )),
    CONSTRAINT simulation_memory_episodes_write_check CHECK (write_outcome IN (
        'not_attempted', 'in_flight', 'rejected_retryable',
        'accepted', 'unconfirmed'
    )),
    CONSTRAINT simulation_memory_episodes_counts_check CHECK (
        attempts >= 0 AND add_count >= 0
        AND round_index >= 1 AND source_event_seq >= 1
        AND char_length(content_sha256) = 64
    )
);

CREATE INDEX simulation_memory_episodes_state_idx
    ON simulation_memory_episodes (simulation_id, state);

CREATE TABLE simulation_memory_nodes (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    node_key text NOT NULL,
    node_type text NOT NULL,
    label text NOT NULL,
    origin text NOT NULL,
    summary text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, node_key),
    CONSTRAINT simulation_memory_nodes_origin_check CHECK (
        origin IN ('role', 'simulation')
    ),
    CONSTRAINT simulation_memory_nodes_key_check CHECK (
        char_length(btrim(node_key)) >= 1
    )
);

CREATE TABLE simulation_memory_edges (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    edge_key text NOT NULL,
    edge_type text NOT NULL,
    label text NOT NULL,
    origin text NOT NULL,
    summary text,
    source_node_key text NOT NULL,
    target_node_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, edge_key),
    CONSTRAINT simulation_memory_edges_origin_check CHECK (
        origin IN ('role', 'simulation')
    ),
    CONSTRAINT simulation_memory_edges_key_check CHECK (
        char_length(btrim(edge_key)) >= 1
    ),
    CONSTRAINT simulation_memory_edges_source_fk
        FOREIGN KEY (simulation_id, source_node_key)
        REFERENCES simulation_memory_nodes (simulation_id, node_key)
        ON DELETE CASCADE,
    CONSTRAINT simulation_memory_edges_target_fk
        FOREIGN KEY (simulation_id, target_node_key)
        REFERENCES simulation_memory_nodes (simulation_id, node_key)
        ON DELETE CASCADE
);

CREATE TABLE simulation_memory_provenance (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    target_kind text NOT NULL,
    target_key text NOT NULL,
    memory_id uuid NOT NULL,
    facet text NOT NULL,
    action_id uuid NOT NULL,
    episode_uuid uuid,
    platform_key text NOT NULL,
    round_index integer NOT NULL,
    simulated_at timestamptz NOT NULL,
    source_event_seq integer NOT NULL,
    material_snapshot_id uuid,
    material_key text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, target_kind, target_key, memory_id, facet),
    CONSTRAINT simulation_memory_provenance_kind_check CHECK (
        target_kind IN ('node', 'edge')
    ),
    CONSTRAINT simulation_memory_provenance_facet_check CHECK (
        facet IN ('role_speech', 'simulation_result')
    ),
    CONSTRAINT simulation_memory_provenance_platform_check CHECK (
        platform_key IN ('platform_a', 'platform_b')
    ),
    CONSTRAINT simulation_memory_provenance_round_check CHECK (
        round_index >= 1 AND source_event_seq >= 1
    ),
    CONSTRAINT simulation_memory_provenance_memory_fk
        FOREIGN KEY (simulation_id, memory_id)
        REFERENCES simulation_memory_entries (simulation_id, id)
        ON DELETE CASCADE
);

ALTER TABLE simulation_memory_provenance
    ADD COLUMN summary text;

CREATE INDEX simulation_memory_provenance_target_idx
    ON simulation_memory_provenance (simulation_id, target_kind, target_key);

CREATE TABLE simulation_memory_provider_records (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    memory_id uuid NOT NULL,
    action_id uuid NOT NULL,
    idempotency_key uuid NOT NULL,
    content_sha256 char(64) NOT NULL,
    episode_uuid uuid NOT NULL,
    processed boolean NOT NULL DEFAULT true,
    accepted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, memory_id),
    CONSTRAINT simulation_memory_provider_records_key_unique
        UNIQUE (idempotency_key),
    CONSTRAINT simulation_memory_provider_records_uuid_unique
        UNIQUE (episode_uuid),
    CONSTRAINT simulation_memory_provider_records_hash_check CHECK (
        char_length(content_sha256) = 64
    )
);

CREATE INDEX simulation_memory_provider_records_action_idx
    ON simulation_memory_provider_records (simulation_id, action_id);

CREATE TABLE simulation_round_plans (
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    platform_key text NOT NULL,
    round_index integer NOT NULL,
    memory_cutoff_round integer NOT NULL,
    request_payload jsonb NOT NULL,
    request_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (simulation_id, platform_key, round_index),
    CONSTRAINT simulation_round_plans_round_check CHECK (
        round_index >= 1 AND memory_cutoff_round = round_index - 1
    ),
    CONSTRAINT simulation_round_plans_platform_check CHECK (
        platform_key IN ('platform_a', 'platform_b')
    )
);

CREATE TABLE simulation_action_memory_reads (
    action_id uuid NOT NULL REFERENCES simulation_actions(id) ON DELETE CASCADE,
    memory_id uuid NOT NULL,
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    platform_key text NOT NULL,
    round_index integer NOT NULL,
    episode_uuid uuid,
    read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (action_id, memory_id),
    CONSTRAINT simulation_action_memory_reads_memory_fk
        FOREIGN KEY (simulation_id, memory_id)
        REFERENCES simulation_memory_entries (simulation_id, id)
        ON DELETE CASCADE,
    CONSTRAINT simulation_action_memory_reads_round_check CHECK (
        round_index >= 1
    )
);

CREATE TABLE simulation_memory_syncs (
    run_id uuid PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    simulation_id uuid NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    phase text NOT NULL DEFAULT 'queued',
    pending_episodes integer NOT NULL DEFAULT 0,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    CONSTRAINT simulation_memory_syncs_job_unique UNIQUE (job_id),
    CONSTRAINT simulation_memory_syncs_phase_check CHECK (phase IN (
        'queued', 'running', 'succeeded', 'failed', 'canceled'
    )),
    CONSTRAINT simulation_memory_syncs_pending_check CHECK (pending_episodes >= 0)
);

CREATE INDEX simulation_memory_syncs_simulation_idx
    ON simulation_memory_syncs (simulation_id, created_at DESC);
