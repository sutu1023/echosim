# 自然互动、单线采纳与故事图谱规划验证

日期：2026-11-14，Asia/Shanghai。范围仅为三个OpenSpec change的规划和领域/产品文档同步；没有修改实现、执行迁移、调用真实模型或宣告新行为已通过。

## 规划产物

| Change | 规约文件 | Scenario | 待实施任务 |
| --- | --- | --- | --- |
| 2026-11-14-narrative-simulation | role-simulation | 21 | 13 |
| 2026-11-14-simulation-adoption | simulation-adoption、story-preparation、role-simulation | 31 | 14 |
| 2026-11-14-story-graph | story-graph | 15 | 12 |

每项均包含proposal.md、design.md、完整delta specs和tasks.md；OpenSpec status的四类artifact均done，isPlanningComplete=true。这里的done表示规划文件齐备，不表示39个实施任务完成；所有任务保持未勾选。

实现及归档顺序为narrative-simulation→simulation-adoption→story-graph。前者修改role-simulation.S1/S2/S3/S7并新增S8/S9，采纳change仅追加S10，避免跨change重复覆盖。story-preparation.P2/P3的冻结与互斥限制由采纳change承担。图谱读取采纳提交的同一版事实，不另设采纳后异步写图事务。

## 已实际运行的检查

- openspec validate --all --strict：10项通过、0失败（3个活动change及7份主规约）。
- make check：ruff检查和格式检查通过；后端93项通过；前端25项通过；TypeScript/Vite构建通过；仓库结构检查通过；上述OpenSpec严格校验通过。
- 既有Starlette/httpx弃用警告1项，未跳过或放宽测试。
- git diff --check：通过。
- 规划逐Scenario追溯已写入traceability.md，全部标为待实现/待验证；产品文档区分上一轮3×3已实现能力与本轮待实现能力。

## 尚未验证

本轮动态互动质量、逐字播放、采纳事务、角色跨任务记忆、图谱构建及更新均待实施。未运行本轮make smoke、真实模型评测、浏览器验收、迁移/回退或公网验证；对应任务已列入各change。当前93/25项既有测试通过不能替代新Scenario证据。实施期间还须核对每个change业务逻辑500行门槛，规划阶段不声称已经满足实现规模要求。
