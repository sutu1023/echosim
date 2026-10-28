## ADDED Requirements

### Requirement: 运行基线
系统 MUST 满足以下可判定场景；失败路径不以口头说明豁免。

#### Scenario: SC-RT-01 正常
- **GIVEN** 配置完整数据库可用
- **WHEN** GET /health
- **THEN** 200、OK，含版本UTC时间及postgres就绪

#### Scenario: SC-RT-02 失败
- **GIVEN** 删除必需数据库地址
- **WHEN** 启动应用
- **THEN** 配置校验失败，非零退出且不打印秘密

#### Scenario: SC-RT-03 失败
- **GIVEN** APP_ENV非法或模式不是fake
- **WHEN** 启动M0
- **THEN** 拒绝启动并指明配置名

#### Scenario: SC-RT-04 失败
- **GIVEN** 数据库断开
- **WHEN** GET /health
- **THEN** 503、DEPENDENCY_UNAVAILABLE且不泄露DSN

#### Scenario: SC-RT-05 失败
- **GIVEN** 请求未知或未登记路由
- **WHEN** 发送请求
- **THEN** 404/403统一封装，绝不执行路由副作用

#### Scenario: SC-RT-06 边界
- **GIVEN** 数据库故障或连接耗时达到探测上限
- **WHEN** GET /livez及/health
- **THEN** 存活仍200，就绪有界失败；有request_id

#### Scenario: SC-RT-07 正常
- **GIVEN** 日志收集可用
- **WHEN** 请求探针
- **THEN** JSON日志带关联ID，路由模板，无秘密原文

#### Scenario: SC-RT-08 失败
- **GIVEN** 缺失、超长或含控制字符的外部请求标识
- **WHEN** 请求探针
- **THEN** 服务端生成合法关联ID，不回显恶意值
