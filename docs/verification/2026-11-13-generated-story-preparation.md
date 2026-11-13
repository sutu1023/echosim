# 一句话故事准备验证

执行日期：2026-11-14，Asia/Shanghai。基线 22271047eeaef248abca6fb826d4112ea0240aa9 加本轮工作区；源文件摘要见 results/creative-source-manifest.json。

实现以已保存的用户配置创建持久任务，事务只写输入快照/引用/状态，模型调用在事务外。生成完整概要、世界规则、大纲及三名角色；整版候选确认前不会替换确认指针。

- 自动合同：test_story_generation.py 检查完整 schema、三角色唯一 ID、无部分发布、最多三次调用；test_zz_story.py 在隔离 PostgreSQL 检查六路重复提交、作品占用、跨用户404、旧版确认409、重复确认、失败重写保留候选与租约 fencing；StoryPreparation.test.tsx 覆盖 pending、确认及模型脚本按文本显示。
- 实际 worker 故障：后台恢复测试作品 f79aa515-0ce3-4df6-8566-7c5d99d0ccbf，任务 ef8d0cf8-2a61-450f-ad19-db7a3cab6245 在 running 时重启 worker，租约回收后明确 failed / WORKER_LOST；未发布残缺候选。全部 worker 停止期间无法回收，重启后执行回收。
- 真实生成：浏览器 A 创建“雾港灯塔 · 真实推演验收”，只输入一句暴风雨灯塔创意；真实 OpenCode Go 生成林岫、唐棠、陆行舟及故事信息，用户界面整版确认，无手动逐字段填写。
- 固定真实评测：scripts/evaluate_preparation.py，15 条合成案例。preparation-eval-v1.json 为13/15，失败为超时和脚本形式角色名；preparation-v2 收紧长度与名称后 preparation-eval-v2.json 为15/15。保留原始失败结果，未以 mock 替代供应商调用。

浏览器自然语言整版改写已完成：保留三角色与秘密，把风暴改为台风、恢复主灯期限改为十分钟；出现新候选后单独确认。旧确认版本仍保留在历史中。随后重启数据库/API，已保存配置与旧推演事件仍可见。

最终门禁及联合真实链路结果见 2026-11-13-minimal-role-simulation.md。自动模型评审仅为固定样例证据，不是所有创作输入的质量或安全保证。材料导入、来源抽取和章节不在本次实现范围。
