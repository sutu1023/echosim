CREATE TABLE model_configurations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    base_url text NOT NULL DEFAULT 'https://opencode.ai/inference/go/openai/v1',
    model text NOT NULL,
    ciphertext text,
    key_id text NOT NULL DEFAULT 'environment-v1',
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, id),
    CHECK (NOT active OR ciphertext IS NOT NULL)
);
CREATE UNIQUE INDEX one_active_model ON model_configurations(user_id) WHERE active;
CREATE TABLE model_configuration_references (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    configuration_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (user_id,configuration_id) REFERENCES model_configurations(user_id,id)
);
