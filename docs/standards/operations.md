# 部署运维规范

- Python 与 Node 使用提交的锁文件；容器多阶段构建并非 root 运行。检查：make check、make smoke 和容器 id。
- 环境分 development/test/production，环境相关配置外置；配置缺失失败，日志不打印取值。检查：R1。
- /health 免认证但只返回版本、时间、依赖 up/down，不返回网络地址。readiness 为 200 才接流量；此端点不是 worker 或模型可用性证明。检查：R2 数据库停止/恢复。
- 日志单行 JSON，字段 timestamp、level、service、event、request_id；请求增加 method、status、duration_ms。检查：结构化日志测试。
- API graceful shutdown 上限 10 秒；空闲 worker 收 SIGTERM 后 5 秒内退出，Compose 留 15 秒停止窗口。检查：进程测试与容器停止记录。
- 发布顺序：make check→构建固定提交→兼容迁移（如有）→readiness→切换→版本标签；失败回到上一镜像或提交。schema 先加后删；不得通过 down -v 停止正常开发实例。
- 正式持久数据上线前配置每日备份，目标 RPO 24 小时/RTO 4 小时，恢复到隔离数据库验证；这些是目标，未经演练不得标记已达成。对象存储备份与留存随导入能力加入。
- Render 配置入库，不代表已购买或部署；上线需核实账号、区域、预算、对象存储和模型访问。运行证据记录环境、命令、结果与限制。
