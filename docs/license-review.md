# 已批准：开发／构建依赖许可例外

状态：维护者于 2026-10-28 在本会话明确批准以下4项限定例外（EX-004）。仅适用于表内包及用途；不扩充全局白名单。基础镜像保留发行方第三方许可，应用包清单另行生成。此评估不将依赖许可改成项目 MIT。

| 许可 | 实际依赖与用途 | 拟准入条件与替代 |
|---|---|---|
| [MPL-2.0](https://spdx.org/licenses/MPL-2.0.html) | lightningcss 及同版本平台包；Vite 8 构建 CSS | 仅未修改的开发构建工具，不进入前端应用入口；保留其许可，未来修改或分发库本身重新检查源码提供等义务。替代为切换构建工具版本，须重新验证锁文件与安全性。 |
| [CC0-1.0](https://spdx.org/licenses/CC0-1.0.html) | mdn-data；CSS 工具的规范数据 | 限该数据包、保留来源；不是把用户新闻按 CC0 处理。替代为移除该测试／CSS工具链。 |
| [BlueOak-1.0.0](https://spdx.org/licenses/BlueOak-1.0.0.html) | lru-cache；开发工具缓存 | 限该包，保留许可全文或链接，依赖源码不修改。替代为调整工具链。 |
| [MIT-0](https://spdx.org/licenses/MIT-0.html) | @csstools/css-syntax-patches-for-csstree、@csstools/color-helpers；DOM/CSS测试工具 | 限两包，保留原许可与来源；不扩大所有未知许可准入。替代为更换 DOM 测试工具链。 |

以上包均为开发／构建依赖。生产入口仅导入 React、React DOM、自有代码与CSS，最终Caddy镜像只复制dist，不复制node_modules。完整版本以 pnpm-lock.yaml 和 artifacts/licenses.json 为准。用途或许可变化时重新评估。

已将上述包范围从 config/license-policy.json 的 pending_review 移到 reviewed_exceptions，决定记录见本段及 exceptions.md；用途或许可变化时重新审查，不跳过许可门禁。
