---
name: echosim-test
description: 按 EchoSim 场景补测试并验证。用于行为变更、缺陷修复和验收，不用于生成无行为依据的测试。
---

# echosim-test

输入：仓库、变更名、受影响任务。
1. 读 AGENTS.md、testing.md 和对应 specs；将正常/失败/边界场景映射到测试编号。
2. 选择最低有效层次；缺陷先复现再修。外部模型新闻与时钟使用替身，真实付费检查不混入标准测试。
3. make prepare 后 make test；合并前 make check。首个失败就定位，不能放宽门禁或跳钩子。
4. 核对 artifacts/traceability.json 与实际断言；关键规则用隔离副本抽样变异，确保变红并恢复。
5. 归档命令、环境、结果和未执行项，不能将M1–M4计划当成已实现。
输出：测试差异、对应关系、验证摘要及真实待办。
