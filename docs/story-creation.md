# 一句话故事准备与角色推演

模型设置完成后，在作品详情进入故事准备。输入 1–2000 字创意，后台生成概要、世界规则、大纲和三名角色；通过自然语言意见整版重写，失败保留旧候选。内容标明为模型创作新增。确认具体当前版本后才允许推演；重复确认幂等，过时版本确认 409。候选与确认是两个独立指针，重新生成不会自动覆盖确认，旧推演保留原确认快照。

生成 API：POST `/api/v1/works/{id}/preparation-tasks`，提交 idea、可选 base_version_id 及 UUID Idempotency-Key，返回 202。GET `/preparations` 读取候选、确认与任务历史，POST `/preparation-confirmations` 提交 version_id/expected_candidate_id。GET `/tasks/{task_id}` 恢复任务状态。所有端点均核验作品及资源归属，写操作校验 Origin/CSRF。

推演 API：POST `/api/v1/works/{id}/simulations`，空对象请求体和幂等键；GET `/{simulation_id}`、`/events?after=N`、`/stream`；POST `/stop`。三轮每轮三名角色各一次，共九事件。SSE ID 对应事件序号，支持 Last-Event-ID；前端有轮询补取与去重。会话失效后流关闭。每个角色的上下文只含公开设定、自己的秘密与可见事件；任务内已接受事件构成记忆，不写正式剧情。

同一作品最多一个 queued/running/stopping 任务。提交与配置引用同事务保存；模型调用在事务外。任务每五秒续租，租约三十秒，存活的 worker 在租约到期后十秒内回收失败任务；全部 worker 停止时将在恢复运行后回收。fencing 禁止旧 worker 写结果。失败任务不续跑，重试是新任务。停止 queued 不调用模型；停止 running 完成至多当前事件后 stopped，终态不可恢复。

每次模型调用 60 秒、最多两次重试，1/2 秒退避；准备总期限 240 秒，推演事件 240 秒、推演总期限 45 分钟。停止遇到模型失败则不发布未完成事件。记录模型、配置/提示版本、参数、耗时和可用 usage，不记录完整提示、密钥或私有正文。

迁移 003 新增任务与版本，004 新增事件。回退前停止领取，保留历史表，采用新增迁移修复。自动测试与真实模型评测独立记录；章节生成、采纳、公网与规模目标尚未交付。
