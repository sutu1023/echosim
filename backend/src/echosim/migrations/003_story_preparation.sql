CREATE TABLE story_tasks (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    kind text NOT NULL CHECK(kind IN ('preparation','simulation')),
    request_key uuid NOT NULL,
    request_data jsonb NOT NULL,
    input_data jsonb NOT NULL,
    configuration_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','stopping','succeeded','failed','stopped')),
    fence uuid,
    lease_until timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id,work_id,kind,request_key),
    UNIQUE(user_id,work_id,id),
    FOREIGN KEY (user_id,work_id) REFERENCES works(user_id,id),
    FOREIGN KEY (user_id,configuration_id) REFERENCES model_configurations(user_id,id)
);
CREATE UNIQUE INDEX one_story_task ON story_tasks(work_id) WHERE state IN ('queued','running','stopping');
CREATE TABLE story_preparation_versions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id,work_id,id) REFERENCES story_tasks(user_id,work_id,id),
    UNIQUE(user_id,work_id,id)
);
CREATE TABLE work_story_heads (
    work_id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    candidate_id uuid,
    confirmed_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id,work_id) REFERENCES works(user_id,id),
    FOREIGN KEY(user_id,work_id,candidate_id) REFERENCES story_preparation_versions(user_id,work_id,id),
    FOREIGN KEY(user_id,work_id,confirmed_id) REFERENCES story_preparation_versions(user_id,work_id,id)
);
ALTER TABLE model_configuration_references ADD CONSTRAINT task_reference
    FOREIGN KEY(id) REFERENCES story_tasks(id) DEFERRABLE INITIALLY DEFERRED;
