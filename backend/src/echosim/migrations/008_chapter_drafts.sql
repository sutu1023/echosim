ALTER TABLE story_tasks DROP CONSTRAINT story_tasks_kind_check;
ALTER TABLE story_tasks ADD CONSTRAINT story_tasks_kind_check CHECK(kind IN
 ('preparation','simulation','adoption','graph','chapter','chapter_review','chapter_polish'));
CREATE TABLE chapter_drafts (
 id uuid PRIMARY KEY REFERENCES story_tasks(id),
 user_id uuid NOT NULL, work_id uuid NOT NULL, version integer NOT NULL CHECK(version>0),
 title text NOT NULL, body text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(user_id,work_id,id) REFERENCES story_tasks(user_id,work_id,id)
);
CREATE INDEX chapter_tasks_by_simulation ON story_tasks(user_id,work_id,(input_data->>'simulation_id'),created_at DESC)
 WHERE kind IN ('chapter','chapter_review','chapter_polish');
-- Forward recovery: retain chapter_drafts and completed job snapshots. Apply any
-- schema correction in a later numbered migration after draining active chapter jobs.
