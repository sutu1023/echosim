# Design

## Context

现有 model_connection 仅支持固定提示的临时诊断；PublicBackend 已实现公网 DNS 检查与连接地址固定。需求见 proposal.md，保留该诊断原合同，业务配置另建认证入口。

## Goals / Non-Goals

Goals: 服务层负责授权、验证及版本切换，适配器负责加密、存储和网络；任务绑定接口可由后续两个 change 复用。
Non-Goals: 不构建任意供应商插件平台，不加入 embedding 或用户主密钥管理。

## Decisions

- 供应商固定 OpenCode Go，base URL 只接受 https://opencode.ai/inference/go/openai/v1；模型名可填写，默认 deepseek-v4.1-flash。相较任意 URL，单一精确白名单复用已验证网络路径。新业务调用允许版本化提示与参数，开发诊断仍固定提示/模型。
- 使用成熟加密库的认证加密，主密钥从环境注入；保存 ciphertext、key_id、用户归属和版本元数据，不自行设计算法。新增依赖需锁定并运行 make audit。密钥缺失/非法必须启动失败，不生成临时替代密钥。
- 测试并激活采用事务外网络验证，事务内比较 expected_version 并切换当前指针。并发修改返回 409，失败不覆盖当前配置。测试通过但尚未激活的密钥仅在请求内存存活，避免额外保存无效候选。
- 配置版本不可修改。模型配置服务提供事务内 acquire/release 引用合同；第二个 change 将引用与任务写入同事务。引用不绑定尚不存在的任务外键，保存 owner/work/operation/request 身份供校验，后续任务外键由新增迁移补齐。终态释放引用并清理 retired ciphertext，worker 每 30 秒补偿清理；即使没有 worker，激活时可立即清理无引用旧版。元数据继续用于历史解释。
- GET/POST /api/v1/model-configurations 提供当前配置与测试激活；POST /api/v1/model-configuration-tests 提供不保存的测试。所有写入验证 Origin/CSRF；输入 key 1–4096、model 1–200 个码点，拒绝控制字符与未知字段；响应 no-store。初次配置 expected_version=null。
- 界面展示 URL、模型、固定掩码与状态；更新要求重新输入 key，避免把掩码当凭据。错误复用既有模型分类。没有保存的 key 不进入日志或客户端存储。

## Risks / Trade-offs

- 供应商可用性/模型能力变化 → 实现时真实测试及创作评测，历史成功不视作当前成功。
- 保留旧 key 才能运行旧任务 → 限于非终态引用，完成后 60 秒内清理；供应商提前撤销 key 时任务安全失败，不悄悄换版。
- 引用泄漏 → 事务性绑定/释放、终态补偿及并发测试；本 change 用合同测试模拟任务，后续补真实任务验证。
- 规模超过单 change 500 行业务逻辑 → 实现前检查预计范围，进一步拆分可独立验收子能力，不能放宽门禁。

## Migration Plan

新增编号 SQL 创建用户配置、版本及引用记录；业务实体有 id/created_at/updated_at。先配置主密钥、迁移，再部署 API；回退旧应用保留新增表，修复新增迁移。备份必须与主密钥分别受控保存，公网和备份恢复仍待验证。证据写入 docs/verification/2026-11-13-persistent-model-config.md。
