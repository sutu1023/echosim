-- 0002：后台任务队列、run 事件流与步骤副作用。
-- 仅前向迁移：应用一次后记录在 schema_migrations，不做 down。

CREATE TABLE runs (
    id uuid PRIMARY KEY,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind text NOT NULL,
    stage text NOT NULL,
    total_steps integer NOT NULL,
    fail_at_step integer,
    simulated_epoch timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT runs_kind_check CHECK (kind IN ('test.simulation')),
    CONSTRAINT runs_stage_check CHECK (stage IN (
        'evidence-graph',
        'roles-environment',
        'dual-platform-run',
        'report',
        'interaction'
    )),
    CONSTRAINT runs_total_steps_check CHECK (total_steps BETWEEN 1 AND 100),
    CONSTRAINT runs_fail_at_step_check CHECK (
        fail_at_step IS NULL
        OR (fail_at_step >= 1 AND fail_at_step <= total_steps)
    )
);

CREATE INDEX runs_project_created_idx ON runs (project_id, created_at DESC);

CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    operation_key text NOT NULL,
    status text NOT NULL DEFAULT 'queued',
    next_step integer NOT NULL DEFAULT 0,
    attempts integer NOT NULL DEFAULT 0,
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    cancel_requested_at timestamptz,
    terminal_reason text,
    started_at timestamptz,
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT jobs_status_check CHECK (status IN (
        'queued', 'running', 'succeeded', 'failed', 'canceled'
    )),
    CONSTRAINT jobs_run_unique UNIQUE (run_id),
    CONSTRAINT jobs_next_step_check CHECK (next_step BETWEEN 0 AND 100),
    CONSTRAINT jobs_attempts_check CHECK (attempts >= 0)
);

-- 同一项目同一操作最多一个有效任务；终态后允许再次开始。
CREATE UNIQUE INDEX jobs_active_operation_idx
    ON jobs (project_id, operation_key)
    WHERE status IN ('queued', 'running');

-- 领取候选同时包含“从未开始”和“租约已过期的 running”。
CREATE INDEX jobs_claimable_idx ON jobs (created_at)
    WHERE status IN ('queued', 'running');

CREATE INDEX jobs_lease_idx ON jobs (lease_expires_at)
    WHERE status = 'running' AND lease_expires_at IS NOT NULL;

CREATE TABLE run_steps (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index integer NOT NULL,
    step_key text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    applied_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_index),
    CONSTRAINT run_steps_key_unique UNIQUE (run_id, step_key),
    CONSTRAINT run_steps_step_index_check CHECK (step_index >= 1)
);

CREATE TABLE run_events (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    seq integer NOT NULL,
    type text NOT NULL,
    stage text NOT NULL,
    simulated_at timestamptz NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq),
    CONSTRAINT run_events_type_check CHECK (type IN (
        'progress', 'failed', 'canceled', 'completed'
    )),
    CONSTRAINT run_events_seq_check CHECK (seq >= 1)
);

CREATE INDEX run_events_run_created_idx ON run_events (run_id, created_at);
