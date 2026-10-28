# 数据字典

M0 不创建业务表。PostgreSQL 只用于就绪连通探测。Alembic 保留空基线，不将未来模型误记为已实现。

M1 起由模型 Column.info 的 description/sensitivity/retention 字段生成字典，检查生成结果与仓库一致；模型字段未定义含义时不得合并。此文件当前是明确的空基线，非业务字典验收通过。
