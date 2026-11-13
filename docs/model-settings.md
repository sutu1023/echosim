# 生成模型配置

登录后访问 `/settings/models`，首版仅支持 OpenCode Go 的 `https://opencode.ai/inference/go/openai/v1`，默认模型 `deepseek-v4.1-flash`。测试成功且并发版本检查通过后才保存；替换失败保持旧配置。模型名可改，地址严格固定。每次保存需重新输入密钥，页面仅显示固定掩码，不把密钥存入浏览器存储。

`MODEL_MASTER_KEY` 是 Fernet 主密钥，由部署环境注入；本地 `make configure` 为新环境生成，已有环境需受控补充，不得提交或输出取值。API 缺少或非法主密钥会安全拒绝启动。cryptography 提供认证加密，避免自行实现密码算法；已锁定 50.0.2，使用 make audit 检查。丢失主密钥会导致既有凭据无法解密，恢复时必须使用受控备份中的相同主密钥；主密钥轮换迁移不属于用户配置更新。

配置保存为不可变版本，更新需 `expected_version`；新任务原子绑定当前版本。旧版本仅为在途任务保留密文，终态释放引用后立即清理，worker 另外补偿清理无引用退休版本。历史版本保留模型元数据。供应商撤销旧密钥会使旧任务失败，不会偷偷切到新配置。数据库与主密钥备份分开受控保存，公网备份恢复尚未验证。

API：GET/POST `/api/v1/model-configurations`，GET `/api/v1/model-configurations/{version}`，POST `/api/v1/model-configuration-tests`。测试与保存受 Origin/CSRF 保护，测试 60 秒无自动重试。跨用户版本与不存在版本同 404；调用网络时数据库事务已结束。

迁移 002 新增模型配置及引用表；回退保留表，向前修复使用新迁移。后续 003 将引用关联到持久故事任务，不能将尚未入库的引用单独提交。
