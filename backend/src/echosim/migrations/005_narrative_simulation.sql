ALTER TABLE simulation_events DROP CONSTRAINT simulation_events_sequence_check;
ALTER TABLE simulation_events ADD CONSTRAINT simulation_events_sequence_check CHECK(sequence BETWEEN 1 AND 24);
ALTER TABLE story_tasks ADD COLUMN progress jsonb NOT NULL DEFAULT '{}';
