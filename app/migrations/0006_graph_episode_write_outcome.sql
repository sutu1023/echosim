-- 0006：区分 episode 写入尚未尝试、可能已受理、明确拒绝可重试与已受理。
-- 仅前向迁移：新增独立于检索状态的写结果列，旧记录按 not_attempted 回填。

ALTER TABLE graph_episodes
    ADD COLUMN write_outcome text NOT NULL DEFAULT 'not_attempted';

ALTER TABLE graph_episodes
    ADD CONSTRAINT graph_episodes_write_outcome_check CHECK (write_outcome IN (
        'not_attempted',
        'in_flight',
        'rejected_retryable',
        'accepted'
    ));
