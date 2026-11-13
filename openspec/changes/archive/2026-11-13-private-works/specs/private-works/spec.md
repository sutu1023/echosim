# Private Works Delta

## Purpose

让已登录创作者创建并持续保存自己的作品，通过仅属于当前账号的列表和详情进入后续创作流程；以明确的授权、标题校验和持久幂等规则，避免跨账号泄露和网络重试造成重复作品。

## ADDED Requirements

### Requirement: W1 Create a titled work
系统 SHALL 允许已登录用户创建只有标题的作品，归属由服务端当前身份确定。标题去除首尾空白后为 1–100 个 Unicode 码点，允许重名，保留内部空白；空白、超长、非字符串或未知字段返回 422/INVALID_REQUEST 且无写入。

#### Scenario: Valid and duplicate titles
- **WHEN** 用户以两个独立请求标识创建同名作品
- **THEN** 每次返回 201，产生两个不同作品；标题按规则去除首尾空白

#### Scenario: Title boundaries
- **WHEN** 分别提交空白、101 个码点、1 个码点和 100 个码点标题
- **THEN** 前两项返回 422 且无新增作品，后两项成功；前后端使用同一长度规则

#### Scenario: Forged ownership
- **WHEN** 用户在创建请求中额外传入 user_id 或其他未知字段
- **THEN** 返回 422，不创建属于自己或他人的作品

### Requirement: W2 Private list and detail
系统 SHALL 逐资源校验归属，只返回当前账号的作品。已登录访问他人作品与不存在作品 SHALL 同为 404/NOT_FOUND 且提示及响应结构相同；未登录访问这些业务 API SHALL 返回 401。列表按 created_at DESC、id DESC 分页，默认 20 条、最多 100 条；非法分页及未知筛选项返回 422。

#### Scenario: Two-user isolation
- **GIVEN** A 和 B 分别拥有作品
- **WHEN** A 查询列表并分别请求自己、B 及不存在的作品
- **THEN** 列表只有 A 的作品，自有详情返回 200，后两项返回相同 404 契约

#### Scenario: Unauthenticated access
- **WHEN** 无有效会话请求列表、创建或任意作品详情
- **THEN** 返回 401，不返回作品数据或改变任何作品

#### Scenario: Pagination and invalid filters
- **GIVEN** 用户有超过一页作品且部分创建时间相同
- **WHEN** 在无新增写入期间连续翻页，或提交越界页大小、无效游标、未知筛选项
- **THEN** 合法翻页不重复或遗漏且顺序确定，非法请求返回 422 而非全量结果

### Requirement: W3 Durable creation idempotency
创建 SHALL 要求有效请求标识，按账号及创建操作隔离。相同标识和规范化标题重放返回原作品及 201；不同标题返回 409/IDEMPOTENCY_CONFLICT。标识随作品保留，重启、并发和响应丢失 SHALL 不产生重复作品；失败事务不得留下部分记录。

#### Scenario: Lost response and concurrent replay
- **WHEN** 同账号同标识同标题并发提交，或首次成功响应丢失后重启服务并重试
- **THEN** 都返回同一个作品，数据库中只有一份作品及对应幂等记录

#### Scenario: Changed payload
- **WHEN** 同标识已创建“春山”，随后用它创建“秋水”
- **THEN** 返回 409，原作品不变，也不新增作品

#### Scenario: Independent accounts
- **WHEN** A 和 B 使用相同请求标识创建作品
- **THEN** 分别创建各自作品，不能互相获得对方结果

#### Scenario: Transaction failure
- **WHEN** 首次创建在提交前失败，然后以相同标识重试
- **THEN** 失败无部分写入，重试可以成功且只创建一个作品

#### Scenario: Invalid request identifier
- **WHEN** 创建缺少请求标识或标识格式非法
- **THEN** 返回 422，未写入作品或幂等记录

### Requirement: W4 Work interface and persistence
系统 SHALL 提供中文作品列表和详情，支持加载、空、错误、重试、创建中状态及键盘操作。创建成功进入详情；详情明确故事准备尚未提供，不显示可用的改名、删除或创作控件。服务重启 SHALL 保留作品。

#### Scenario: Empty list and create
- **WHEN** 新账号进入列表并创建第一部作品
- **THEN** 先显示真实空状态，提交期间不能重复点击，成功后进入真实详情

#### Scenario: Uncertain submission
- **WHEN** 创建请求网络超时，用户未修改标题并重试
- **THEN** 重用原请求标识；修改标题后明确发起新建才使用新标识，不静默把冲突请求当成功

#### Scenario: Reload and restart
- **WHEN** 创建后刷新详情、直接打开详情链接或重启服务
- **THEN** 有效会话仍能读取原作品，失败时显示可重试错误而非伪造数据

#### Scenario: Safe text rendering
- **WHEN** 作品标题包含 HTML 或脚本字符
- **THEN** 列表与详情按文本显示，不能执行脚本
