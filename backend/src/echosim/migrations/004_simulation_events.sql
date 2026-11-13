CREATE TABLE simulation_events (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    task_id uuid NOT NULL,
    sequence integer NOT NULL CHECK(sequence BETWEEN 1 AND 9),
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(task_id,sequence),
    FOREIGN KEY(user_id,work_id,task_id) REFERENCES story_tasks(user_id,work_id,id)
);
