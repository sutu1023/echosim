# Design

## Context

本变更从 2026-11-13-private-works 按实现规模拆出。两份变更承接同一次已确认访谈；认证先于作品验收，整体范围不变。

## Goals / Non-Goals

目标为正常身份、会话撤销与本地体验登录。作品创建及授权用例由下游 private-works 承接，不实现公开注册。

## Decisions

- 沿用 private-works/design.md 第 1–4 节已确定的技术设计：api→services→domain、PostgreSQL 事务会话、Argon2id、同源 cookie/CSRF、固定窗口限速、开发开关与固定 A/B 映射。
- 密码重置与签发按账号锁串行化；错误登录计数在错误返回前提交。签发令牌只以摘要保存，CSRF 值不充当登录凭据。
- 认证与作品共享 HTTP/存储组装，逻辑归属以用例为界：本变更负责 Accounts、账号迁移、账号 SQL 方法、登录/退出/快捷 API 和登录页面；private-works 负责 Works、作品 SQL、作品 API 和列表/详情。
- 先运行新增账号迁移，再启动业务进程；初始化体验账号显式执行且不重置原数据。实现和验证始终覆盖完整双账号闭环。

## Risks / Trade-offs

- 认证与作品共享组装需要联合回归 → 两个变更均跑门禁和联合浏览器证据，归档顺序先认证后作品。
- 开发快捷入口可能被误配置 → production/test 强制 404，页面能力由后端返回。
- 本地 HTTP cookie 不使用 Secure → 在安全规范限定环境并测试 production Secure。

## Migration Plan

账号迁移只新增并记录版本；回退旧应用保留表，修复新增迁移。用户和会话存 PostgreSQL，重启保留；公网及备份恢复仍未验证。
