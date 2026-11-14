ALTER TABLE story_tasks DROP CONSTRAINT story_tasks_kind_check;
ALTER TABLE story_tasks ADD CONSTRAINT story_tasks_kind_check CHECK(kind IN ('preparation','simulation','adoption'));
ALTER TABLE story_tasks ADD COLUMN decision text NOT NULL DEFAULT 'ineligible' CHECK(decision IN ('pending','checking','adopted','discarded','ineligible'));
ALTER TABLE work_story_heads ADD COLUMN canon_revision integer NOT NULL DEFAULT 0;
ALTER TABLE work_story_heads ADD COLUMN pending_simulation_id uuid;
ALTER TABLE work_story_heads ADD FOREIGN KEY(user_id,work_id,pending_simulation_id) REFERENCES story_tasks(user_id,work_id,id);
CREATE TABLE story_canons (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    revision integer NOT NULL CHECK(revision>0),
    simulation_id uuid NOT NULL UNIQUE,
    adoption_task_id uuid NOT NULL UNIQUE,
    content jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id,work_id,revision),
    FOREIGN KEY(user_id,work_id,simulation_id) REFERENCES story_tasks(user_id,work_id,id),
    FOREIGN KEY(user_id,work_id,adoption_task_id) REFERENCES story_tasks(user_id,work_id,id)
);
CREATE TABLE story_decisions (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    work_id uuid NOT NULL,
    operation text NOT NULL,
    request_key uuid NOT NULL,
    simulation_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id,work_id,operation,request_key),
    FOREIGN KEY(user_id,work_id,simulation_id) REFERENCES story_tasks(user_id,work_id,id)
);
