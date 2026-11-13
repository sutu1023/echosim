# Proposal

## Why

私有作品闭环需要可撤销、可持久化的用户身份。按 delivery.md 的 500 行业务逻辑边界，从已确认的 private-works 范围中拆出认证前置能力；验收目标未减少。

## What Changes

- 实现预置账号命令、七天会话、密码重置、登录限速和 CSRF。
- 提供正常登录页及仅本地开发显式开启的 A/B 快捷登录。
- 引入账号和会话数据迁移；不提供注册或账号管理后台。
- 与 2026-11-13-private-works 连续实施、联合验收。当前工作分支保留 codex/2026-11-13-private-works，后续独立提交/合入须保持认证在前的依赖顺序。

## Capabilities

### New Capabilities

- `user-auth`: 账号、会话与开发快捷登录。

### Modified Capabilities

无。作品入口开放与首页变化由依赖本变更的 private-works 负责。

## Impact

domain/accounts、services/accounts、密码及账号存储适配器、账号命令、认证 HTTP 与登录界面。共享组装文件与适配器按功能分别追溯；测试、基础 HTTP/SQL 包装不冒充业务复杂度。依赖 Argon2id 库，锁定版本并审计。
