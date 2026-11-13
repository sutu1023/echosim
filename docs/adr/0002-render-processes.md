# Render 分离 Web 与后台进程

线上选择 Render Web Service 同域提供前端、API 与 SSE，Background Worker 执行长任务，使用托管 PostgreSQL；材料和导出文件未来放 S3 兼容对象存储。相较单机共享卷，该选择减少主机运维，但要求进程之间通过持久状态通信，不能依赖共享本地目录；本地仍用 Compose。只读 MCP 随后作为 worker 内部子进程加入。
