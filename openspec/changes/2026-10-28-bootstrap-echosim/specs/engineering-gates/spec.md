## ADDED Requirements

### Requirement: 工程交付
系统 MUST 满足以下可判定场景；失败路径不以口头说明豁免。

#### Scenario: SC-GATE-01 正常
- **GIVEN** 新克隆、Docker及配置齐备
- **WHEN** make up与make test
- **THEN** 隔离服务启动、测试通过、无真实模型调用

#### Scenario: SC-GATE-02 失败
- **GIVEN** 放入测试机密标记
- **WHEN** 运行机密检查
- **THEN** 非零失败，输出不含机密明文

#### Scenario: SC-GATE-03 失败
- **GIVEN** 测试改坏就绪判断
- **WHEN** 在隔离副本运行对应测试
- **THEN** 有效断言失败，不提交变异

#### Scenario: SC-GATE-04 失败
- **GIVEN** 规范缺失编号或场景无测试
- **WHEN** 运行规范追踪检查
- **THEN** 非零并报告缺失项

#### Scenario: SC-GATE-05 失败
- **GIVEN** 漏洞源/检查工具不可用或存在高危
- **WHEN** 运行安全检查
- **THEN** 非零，不冒报通过

#### Scenario: SC-GATE-06 边界
- **GIVEN** 关闭测试栈后再次测试
- **WHEN** make test
- **THEN** 数据与端口隔离、无遗留容器；结果相同

#### Scenario: SC-GATE-07 正常
- **GIVEN** 本地两个可运行构建版本
- **WHEN** 切换并请求探针
- **THEN** 版本正确可恢复，记录耗时

#### Scenario: SC-GATE-08 正常
- **GIVEN** API已有请求
- **WHEN** 发送SIGTERM
- **THEN** 30秒内有界退出，不接受新推演（M0无推演路由）

#### Scenario: SC-GATE-09 正常与失败恢复演练
- **GIVEN** 隔离测试库含合成数据，临时加密密钥仅在内存
- **WHEN** 导出、认证加密、篡改校验并恢复到另一数据库
- **THEN** 篡改失败、正常恢复核对成功、无密钥和明文备份入报告；明确非生产容灾
