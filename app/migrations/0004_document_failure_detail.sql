-- 0004：材料失败的结构化详情（解析时生效的页数与页数上限）。
-- 仅前向迁移：新增列并回填空对象，旧记录保持“无上限数字”的展示语义。

ALTER TABLE documents
    ADD COLUMN failure_detail jsonb NOT NULL DEFAULT '{}'::jsonb;
