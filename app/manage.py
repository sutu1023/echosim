"""本机管理命令：创建或更新本地账户。

用法：
    python -m app.manage create-user --email a@example.com --password-stdin
    python -m app.manage create-user --email a@example.com --password 'dev-password-123' \
        --update-password

密码只从参数或标准输入读取，不写日志；重复邮箱默认失败，`--update-password`
会重设密码并删除该用户已有会话。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy.ext.asyncio import AsyncSession

from .db import SessionLocal
from .errors import AccountExists, ServiceUnavailable
from .security import validate_email, validate_new_password, normalize_email
from .services.accounts import create_account


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.manage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-user", help="创建或更新本地账户")
    create.add_argument("--email", required=True, help="登录邮箱")
    create.add_argument("--display-name", default=None, help="展示名称（可选）")
    password_group = create.add_mutually_exclusive_group(required=True)
    password_group.add_argument(
        "--password", default=None, help="密码（仅本地开发；会出现在进程参数中）"
    )
    password_group.add_argument(
        "--password-stdin",
        action="store_true",
        help="从标准输入读取一行密码，避免出现在 shell 历史与进程列表中",
    )
    create.add_argument(
        "--update-password",
        action="store_true",
        help="邮箱已存在时更新密码，并使该用户已有会话失效",
    )
    return parser


def read_password(args: argparse.Namespace) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    return args.password or ""


async def run_create_user(args: argparse.Namespace, password: str) -> int:
    error = validate_email(args.email)
    if error:
        print(f"邮箱无效：{error}")
        return 1
    password_error = validate_new_password(password)
    if password_error:
        print(f"密码无效：{password_error}")
        return 1

    email = normalize_email(args.email)
    async with SessionLocal() as db:
        try:
            await create_account(
                db,
                email,
                password,
                args.display_name,
                update_password=args.update_password,
            )
        except AccountExists:
            print(f"邮箱已存在：{email}（如需重设密码请加 --update-password）")
            return 1
        except ServiceUnavailable:
            print("数据库不可用，未创建账户")
            return 1
        except Exception as exc:  # noqa: BLE001 - CLI 需要给出可定位失败
            print(f"创建账户失败：{type(exc).__name__}")
            return 1

    action = "已更新" if args.update_password else "已创建"
    print(f"{action}用户：{email}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return asyncio.run(run_create_user(args, read_password(args)))
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
