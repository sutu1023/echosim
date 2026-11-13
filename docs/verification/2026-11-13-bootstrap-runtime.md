# Bootstrap runtime 验证记录

日期：2026-11-13。环境：macOS arm64、Python 3.12.16、Node 22.23.2、Docker 29.9.2、Compose 5.6.1。

## 已验证

- OpenSpec 四份产物齐全，`openspec validate 2026-11-13-bootstrap-runtime --strict` 通过。
- `make check`：29 项后端测试、5 项前端测试通过，类型与生产构建、Python 静态检查、分层检查通过。
- `make audit`：Python 和 npm 均未发现已知漏洞。
- `make smoke`：独立 Compose 项目/随机端口/独立卷，真实数据库正常返回 200；停机返回 503；恢复后无需重启 API 即返回 200；根页面存在，业务路径 404；容器 UID 10001；worker 在 5 秒内停止。临时数据卷已清理。
- Render Blueprint 使用官方 JSON Schema 校验通过；自动部署关闭。
- GitHub 主干保护读回：check 必跑、strict=true、管理员同样受限、禁止强推与删除。

## 修复记录

首次前端构建发现 CSS 导入缺少 Vite 类型声明，添加 vite-env.d.ts 后通过。Render YAML 的 off 和数字值改为显式字符串，避免 YAML 隐式类型破坏平台 schema。

本机存在旧版同名 Compose 服务且占用 8080；新骨架采用独立 echosim-fiction 项目与 8081 端口。首次启动曾重建旧项目的同名 API、worker、数据库容器，随后停止这些新建容器；旧数据卷未删除。后续运行仅操作独立项目。

## 验证限制

未创建 Render 资源，未验证公网访问、SSE 或真实模型调用；尚无登录、业务任务、MCP、对象存储、图谱与章节能力。备份恢复目标未经演练。M0 尚未整体完成。

后端测试依赖给出 httpx TestClient 弃用提示，测试仍通过；后续依赖维护时迁移对应测试客户端，不屏蔽告警。

## 持续集成

实现提交 `4c62702` 已推送至 `codex/2026-11-13-bootstrap-runtime`。
运行地址：https://github.com/sutu1023/echosim/actions/runs/43423049671 。
GitHub check 已成功完成：setup、make check、make audit、make smoke 全部通过。期间曾发生短暂 TLS 断连，恢复后已读取最终 success 结论。

本地浏览器已验证 http://localhost:8081 显示小说创作状态页及“服务运行正常”。全部 Git 历史 blob 已通过密钥模式扫描，确认本机生成的数据库密码未进入历史。
