# EchoSim 仓库工作约定

修改前阅读 README.md、docs/product.md、docs/standards/README.md，并读取涉及的规范全文。数值源 config/policy.json；规范冲突不能静默忽略，按 docs/standards/exceptions.md 处理。

1. 先在 OpenSpec 写 proposal/design/specs/tasks 并严格校验，再实现。当前仅 M0，禁止顺手实现 M1–M4。
2. 后端 api → services → repositories；外部能力 adapters，SQL不得出仓储。统一响应与集中权限，未登记默认拒绝。
3. 修改行为补场景测试；按 echosim-test执行，运行 make check。纯说明文字精简条件见 R05，不按.md后缀猜测。
4. 合并前使用 echosim-review：用户已授权临时独立审查子代理，提供仓库与变更名，不提供预设结论；修复证实问题并重验。不新建侧栏聊天。
5. 常规范围门禁、独立审查通过后可Squash合并；预算增额、安全新例外、破坏数据和正式发布由维护者决定。M0本地标签与仓库交付已授权。
6. 禁止真实Key、选题PDF、私有正文进入提交与日志；提交前密钥钩子必须运行，不使用--no-verify绕过。无真实模型调用属于M0约束。
7. 检查报告区分通过/失败/未执行。外部同学复现与公网资源未获得时写待办，不冒报完成。
8. 代码与文档同PR，常规15文件/800有效行，首次骨架仅EX-003。门禁结果需对应最终版本，已共享迁移不可改。

后端改动读 backend/security/data/testing；前端读 product/security/testing；构建部署读 ops/security；模型相关还读 ai；所有变更读 review。运行流程技能见 .agents/skills/。

启动M1或改变部署暴露范围时必须先更新config/project.json；这会使仅限本地M0的EX-005失效，不允许保留旧阶段绕过。
