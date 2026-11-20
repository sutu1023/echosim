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
