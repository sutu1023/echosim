ALTER TABLE story_tasks DROP CONSTRAINT story_tasks_kind_check;
ALTER TABLE story_tasks ADD CONSTRAINT story_tasks_kind_check CHECK(kind IN ('preparation','simulation','adoption','graph'));
CREATE TABLE story_graph_baselines (
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    version_id uuid NOT NULL,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,work_id,version_id),
    FOREIGN KEY(user_id,work_id,version_id) REFERENCES story_preparation_versions(user_id,work_id,id)
);
