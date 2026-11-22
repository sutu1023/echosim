-- 0007：0005 升级前已尝试写入但没有 UUID 的旧 episode 必须先检索确认。
-- 仅前向迁移：把可能与服务端已受理对应的旧记录标记为 in_flight，避免重复 add。

UPDATE graph_episodes
   SET write_outcome = 'in_flight',
       updated_at = now()
 WHERE episode_uuid IS NULL
   AND attempts > 0
   AND write_outcome = 'not_attempted';
