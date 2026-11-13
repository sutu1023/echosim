"""Explicit schema and account administration; never accept passwords in argv."""

import argparse
import asyncio
import getpass
import secrets
import sys

from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.config import load_settings
from echosim.domain.accounts import Fault
from echosim.services.accounts import Accounts


async def execute(args):
    settings = load_settings()
    store = PostgresStore(settings.database_url.get_secret_value())
    if args.action == "migrate":
        await store.migrate()
        return
    await store.check_schema()
    auth = Accounts(store, Passwords())
    if args.action == "demo":
        if settings.app_env != "development":
            raise Fault("DEVELOPMENT_ONLY", 403)
        for target in ["a", "b"]:
            await auth.provision("experience-" + target, secrets.token_urlsafe(24), preserve=True)
    else:
        password = (
            getpass.getpass("密码：") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
        )
        await auth.provision(args.username, password, reset=args.action == "reset")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["migrate", "create", "reset", "demo"])
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    if args.action in {"create", "reset"} and not args.username:
        parser.error("username required")
    try:
        asyncio.run(execute(args))
    except Fault as exc:
        raise SystemExit(exc.code) from None
    except Exception:
        raise SystemExit("Administration failed; check configuration and database") from None
    print("操作完成")


if __name__ == "__main__":
    main()
