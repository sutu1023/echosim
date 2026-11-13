# 持久生成模型配置验证

执行日期：2026-11-14，Asia/Shanghai。本地 macOS、Docker Compose、OpenCode Go / deepseek-v4.1-flash。代码基于 22271047eeaef248abca6fb826d4112ea0240aa9 的本轮工作区改动，最终文件摘要见 results/creative-source-manifest.json。

- 失败先行：新增 test_model_config.py 时因缺失适配器/服务导入失败；实现后轮换失败保留、旧引用、双用户404、密文/掩码、并发冲突、缺主密钥进程拒绝均通过。
- make check：后端与前端全部检查通过；最终统计见联合推演验证记录。
- scripts/private_integration.py：隔离 PostgreSQL，八路并发激活仅一个成功，其余409；重建存储连接仍能读取配置；旧引用释放立即清除旧密文，迁移重复执行与回滚通过。
- make audit：最初 PyPI TLS 失败，重试发现 cryptography 47.0.0 已知漏洞；升级并锁定 50.0.2 后 Python/Node 均零已知漏洞。未忽略漏洞或放宽审计门禁。
- 浏览器：用户在体验账号 A 验证并保存密钥；代理看到 A 的固定掩码和默认模型，B 显示未配置。API 容器重建、随后数据库/API显式重启后刷新仍显示已保存配置；密钥未回显。
- 真实模型：已保存配置用于故事准备及推演。运行任务绑定 df76a1e0-abe1-45c6-a5b7-861f4c51960a；运行中经真实连接验证激活 093690bb-657e-4792-9b3f-211228ee59d5，任务仍使用原版，旧密文当时保留。任务终态后已确认旧密文清除、新版仍可用。
- make smoke：独立 Compose 的 readiness、停库/恢复、非 root、SIGTERM 与认证边界通过；不操作或删除用户开发卷。

主密钥受控保存在未跟踪 .env；提供配置给本次开发环境不构成公网发布。embedding、公网及备份恢复未实现/未验证。清理在释放引用事务立即执行，因此测试直接检查提交后的密文为空，无须等待60秒或模拟计时器。
