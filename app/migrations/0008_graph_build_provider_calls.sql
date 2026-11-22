-- 0008：记录每个业务建图在 provider 边界实际发生的真实/替身调用次数。
-- 只做前向迁移；旧 build 默认 0，不由计划数或快照大小推算。

ALTER TABLE graph_builds
    ADD COLUMN provider_calls_real integer NOT NULL DEFAULT 0;

ALTER TABLE graph_builds
    ADD COLUMN provider_calls_double integer NOT NULL DEFAULT 0;

ALTER TABLE graph_builds
    ADD CONSTRAINT graph_builds_provider_calls_check CHECK (
        provider_calls_real >= 0 AND provider_calls_double >= 0
    );
