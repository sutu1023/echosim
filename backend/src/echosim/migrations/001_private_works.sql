CREATE TABLE users (
 id uuid PRIMARY KEY, username text NOT NULL UNIQUE, password_hash text NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE TABLE sessions (
 token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), csrf text NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);
CREATE TABLE works (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id), title text NOT NULL,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 CHECK (char_length(title) BETWEEN 1 AND 100), UNIQUE (user_id, id)
);
CREATE INDEX works_page ON works(user_id, created_at DESC, id DESC);
CREATE TABLE work_creation_requests (
 user_id uuid NOT NULL, operation text NOT NULL DEFAULT 'create', request_key uuid NOT NULL,
 work_id uuid NOT NULL, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 PRIMARY KEY (user_id, operation, request_key),
 FOREIGN KEY (user_id, work_id) REFERENCES works(user_id, id)
);
CREATE TABLE login_attempt_windows (
 key text PRIMARY KEY, count integer NOT NULL, until timestamptz NOT NULL
);
