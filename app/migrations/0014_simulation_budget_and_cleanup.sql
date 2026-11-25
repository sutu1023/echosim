-- 0014：双平台运行的共享动作预算与清理结果记录。
-- 只做前向迁移：已有运行默认预算为 0、清理结果为 NULL。

ALTER TABLE simulations
    ADD COLUMN reserved_actions integer NOT NULL DEFAULT 0;

ALTER TABLE simulations
    ADD COLUMN cleanup_code text;

ALTER TABLE simulations
    ADD CONSTRAINT simulations_reserved_actions_check CHECK (reserved_actions >= 0);
