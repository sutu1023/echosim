---
name: echosim-baseline
description: 从零复现 EchoSim 基线。用于干净克隆、配置启动、探针、日志、隔离测试和本地版本切换验收。
---

# echosim-baseline

输入：仓库与待验证版本。
1. 只按 README 在临时克隆目录操作。确认 Docker、Python3、make、Git，不复用他人的配置或数据卷。
2. make init 生成本地随机配置；make prepare 准备镜像依赖；make up 启动，make status 核对版本/UTC/数据库。
3. make test 与 make check，检查两引擎和375/768/1280截图；故障场景不得只看HTTP成功。
4. 本地版本切换和SIGTERM由隔离测试执行，记录时间。清理仅本次测试资源，不删除开发卷。
5. 未执行真实模型、公网发布、同学/教师独立复现时分别列未执行。AI复现不能替代真人交叉复现。
输出：精确步骤、结果、报告位置和外部待验。
