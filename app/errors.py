"""领域异常：由服务层抛出，路由层与全局处理器统一映射为对外响应。"""

from __future__ import annotations


class Unauthenticated(Exception):
    """请求未携带会话 Cookie、会话无效或已过期。"""

    def __init__(self, *, clear_cookie: bool) -> None:
        super().__init__("未登录")
        self.clear_cookie = clear_cookie


class ServiceUnavailable(Exception):
    """数据库不可达等暂时性故障，对外统一 503。"""


class InvalidCredentials(Exception):
    """邮箱不存在或密码错误，对外只返回同一条文案。"""


class AccountExists(Exception):
    """邮箱已存在且未要求更新密码。"""


class ProjectNotFound(Exception):
    """项目不存在、属于他人或 ID 非法，对外统一 404。"""


class JobNotFound(Exception):
    """任务不存在或不属于已解析项目，对外统一 404。"""


class DocumentNotFound(Exception):
    """材料不存在、属于其他项目或 ID 非法，对外统一 404。"""


class DocumentNotText(Exception):
    """对 PDF 材料请求原文视图，对外统一 409。"""


class InvalidCursor(Exception):
    """事件流游标非法，对外统一 400。"""


class LeaseLost(Exception):
    """本次领取的租约令牌已失效或任务已进入终态，调用方必须停止写入。"""


class GraphBuildNotFound(Exception):
    """建图任务不存在、属于其他项目或 ID 非法，对外统一 404。"""


class GraphSnapshotNotFound(Exception):
    """指定建图没有已发布快照，或版本不存在或已被清理。"""


class GraphSnapshotInconsistent(Exception):
    """快照头计数与节点/边明细数量不一致，禁止返回半成品视图。"""


class InvalidSnapshotRevision(Exception):
    """快照版本非法或超出当前 build 的版本范围，对外统一 400。"""


class GraphEpisodeConflict(Exception):
    """同一 episode 已绑定不同 UUID，拒绝覆盖并保留原记录。"""

    def __init__(self, code: str = "episode_uuid_conflict") -> None:
        super().__init__(code)
        self.code = code


class GraphEpisodeNotFound(Exception):
    """建图 episode 记录不存在。"""


class GraphBindingNotFound(Exception):
    """项目尚无图谱绑定记录。"""


class GraphBuildConflict(Exception):
    """同一项目的活动建图冲突、记录唯一性或材料状态冲突。"""

    def __init__(self, code: str = "build_conflict") -> None:
        super().__init__(code)
        self.code = code


class GraphBindingConflict(Exception):
    """图谱创建结果为空或与已保存绑定不一致。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GraphLimitExceeded(Exception):
    """节点、边或单项来源超过首版上限，明确失败而不是截断。"""

    def __init__(
        self,
        *,
        code: str,
        limit: int,
        actual: int,
        target: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.limit = limit
        self.actual = actual
        self.target = target
