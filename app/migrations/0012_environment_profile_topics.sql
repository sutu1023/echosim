-- 0012：保存分批画像返回的初始话题，避免只出现在批次 initialTopics 的话题丢失。
-- 只做前向迁移；已有准备记录默认为空数组。

ALTER TABLE environment_preparations
    ADD COLUMN profile_topics jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE environment_preparations
    ADD CONSTRAINT environment_preparations_profile_topics_check CHECK (
        jsonb_typeof(profile_topics) = 'array'
    );
