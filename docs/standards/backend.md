# 后端开发规范

- api 只做 HTTP 边界，services 管用例，domain 定义契约，adapters 对接外部系统。组装位于 main/worker。检查：make check 的 AST 依赖检查。
- 业务 API 使用 /api/v1 与小写复数资源；健康路径 /health。错误结构 error.code、error.message、request_id，HTTP 码与错误类别一致。骨架码：NOT_FOUND/404、METHOD_NOT_ALLOWED/405、INVALID_REQUEST/422、INTERNAL_ERROR/500。检查：接口用例。
- 请求入口校验类型、范围与未知筛选项；非法筛选报错，不能忽略后返回全量。分页有上限和唯一排序兜底 id。检查：后续接口边界用例。
- 数据访问通过适配器；写事务只覆盖数据库操作，不夹带模型调用；业务任务提交与状态变更同事务。幂等键按用户、作品、操作作用域唯一。检查：事务和重放测试。
- 必需配置缺失或非法即启动失败；不得在日志输出 Pydantic 原始输入或数据库 URL。检查：R1 进程测试。
- 模型、数据库访问有超时；失败转为明确错误，不无限重试、不静默返回成功。检查：R2 超时与恢复测试。
