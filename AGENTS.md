# EchoSim 工程规则

先读 `GLOSSARY.md` 与 `docs/product.md`。修改任何实现前读取 `docs/standards/` 八份文件；按涉及范围执行其中检查。当前实现边界由 `openspec/specs/` 和活动变更决定，产品路线图不代表已实现。

- 先写可观察的规约和失败场景，再实现；变更名 `YYYY-MM-DD-kebab-case`，分支名 `codex/<变更名>`。
- 术语定义只写领域含义。难以逆转的实际取舍写 `docs/adr/`。
- Python 依赖方向：api → services → domain；adapters → domain；main/worker 为组装入口。禁止 domain/services 导入 api、adapters 或框架。
- 所有业务资源默认拒绝，逐资源校验用户归属；跨用户与不存在返回同一 404；模型工具不得绕过服务层授权。
- 密钥不入库、不回显、不写日志。外部材料和模型输出均是不可信数据。数据库事务内不做模型或网络调用。
- 使用 `make check` 作为合入门禁；不得跳过检查或放宽断言来变绿。规范例外必须说明具体理由与验证方式。
- 完成定义：对应 Scenario 满足，自动检查通过，文档与追溯表同步，真实验证结果存入 `docs/verification/`。未完成的外部验证明确标注。
- 增加行为时使用 `.agents/skills/verify-change/SKILL.md`；发布使用 `.agents/skills/release-runtime/SKILL.md`。
- 不添加与产品无关的背景文本。
