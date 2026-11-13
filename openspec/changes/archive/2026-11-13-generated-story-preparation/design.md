# Design

## Context

依赖 persistent-model-config。当前 worker 仅轮询健康，数据库尚无任务与故事版本。此 change 实现准备任务所需的最小持久执行，不引入独立消息队列；推演随后复用。

## Goals / Non-Goals

Goals: 一次生成完整候选、整版确认、可追溯版本，以及可跨浏览器连接存活的持久任务。
Non-Goals: 不把候选视作正式剧情；不提供手动字段表单、材料提取或通用工作流编辑器。

## Decisions

- 新增 story_preparation_versions、work_story_heads 与 story_tasks；完整候选按不可变文档保存并有结构化角色 ID/可见性。公开/私有内容分离，私有知识显式列出允许角色；全文只向创作者展示，后续角色上下文须服务端裁剪。
- 本轮用户输入即创意或整版修改意见，均 1–2000 个 Unicode 码点；仅生成且校验通过的完整输出可成为当前候选。概要/规则/大纲、三名角色及必填字段由确定性 schema 校验；总响应上限 1 MiB。所有内容标记为创作新增，不声称材料来源。
- POST /api/v1/works/{id}/preparation-tasks 创建或重写，GET /api/v1/works/{id}/preparations 读版本，POST /api/v1/works/{id}/preparation-confirmations 确认 version_id/expected_candidate_id。读任务使用 /api/v1/works/{id}/tasks/{task_id}。服务统一检查作品归属及 task/version 从属，分页 limit 上限 100。
- 提交事务同时写任务、按用户/作品/操作的幂等记录、配置引用与作品占用。作品部分唯一约束覆盖 queued/running/stopping；确认也锁作品行并检查无活跃任务。确认是无模型调用的短事务，不作为后台生成任务。
- worker 使用 PostgreSQL 领取 queued，短事务领取后再调用模型。running 租约 30 秒，5 秒心跳；10 秒扫描一次超时租约，40 秒内标记 failed。token fencing 保证过期 worker 无法提交；running 不接续，queued 可重新领取。先结束任务再释放作品占用/密钥引用，同一事务提交。
- 每次调用 60 秒、最多两次重试、1/2 秒退避；任务总时限 240 秒，认证/权限拒绝不重试。初始输出限 8192 tokens，temperature=0.7，提示版本入库，记录元数据不记录完整提示。截断/缺字段触发校验失败，禁止部分入库。
- 当前候选与当前确认是两个指针；重写输入绑定显式 base_version_id。确认旧版本若已被新候选替换返回 409；重复当前确认幂等。新候选不清除旧确认，已有推演引用不可变版本。
- 页面 /works/:id/prepare 只提供创意、自然语言修改、整版确认及历史状态；任务轮询满足本阶段恢复查看。首次模型评测建立 15 条固定案例（完整性3、可见性3、注入3、冲突2、来源2、越权2）。

## Risks / Trade-offs

- 模型结构成功不等于故事质量 → 结构合同自动验证，15 条评测记录真实输出判定与限制。
- worker 崩溃可能已产生供应商费用 → 不保证外部调用 exactly-once，保证内部结果不重复；不自动恢复 running。
- 整版重写可能偏离用户意图 → 旧版保留，必须重新确认；历史版本不自动作为最新推演依据。
- 任务和生成逻辑规模 → 实现前评估 500 行业务逻辑限制，必要时细拆关联 change，不扩大既有能力声明。

## Migration Plan

在模型配置迁移后新增任务/版本/指针迁移及引用关联；API/worker 同批部署，保留现有账号和作品。回退前停止领取并处理在途任务，旧应用不删新表。证据写入 docs/verification/2026-11-13-generated-story-preparation.md；准备成功不等于推演或 M1 完成。
