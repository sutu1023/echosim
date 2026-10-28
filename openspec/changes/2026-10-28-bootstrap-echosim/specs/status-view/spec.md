## ADDED Requirements

### Requirement: 状态页面
系统 MUST 满足以下可判定场景；失败路径不以口头说明豁免。

#### Scenario: SC-UI-01 正常
- **GIVEN** API就绪
- **WHEN** 打开状态页并重试
- **THEN** 显示服务就绪、版本及数据库状态

#### Scenario: SC-UI-02 失败
- **GIVEN** API不可达
- **WHEN** 刷新状态
- **THEN** 显示错误及重试入口，无无限加载

#### Scenario: SC-UI-03 失败
- **GIVEN** API返回503
- **WHEN** 打开页面
- **THEN** 显示未就绪和下一步，不当成功

#### Scenario: SC-UI-04 失败
- **GIVEN** API返回非法JSON或契约
- **WHEN** 打开页面
- **THEN** 错误态，不使用伪造默认健康值

#### Scenario: SC-UI-05 失败
- **GIVEN** 用户打开未知页面
- **WHEN** 直接访问未知URL
- **THEN** 显示页面不存在并能返回

#### Scenario: SC-UI-06 边界
- **GIVEN** 375/768/1280宽度与键盘
- **WHEN** 查看页面和触发重试
- **THEN** 无全页横向溢出、焦点可见、44px控件

#### Scenario: SC-UI-07 正常
- **GIVEN** 获取尚未完成或尚无有效状态
- **WHEN** 渲染加载与空状态
- **THEN** 说明正在获取或无记录，提供下一步
