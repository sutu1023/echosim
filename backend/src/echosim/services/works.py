import base64
import json
import re
from datetime import datetime
from uuid import UUID

from echosim.domain.accounts import Fault, Store


def identifier(value, code="INVALID_REQUEST", status=422):
    try:
        return str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise Fault(code, status) from None


class Works:
    def __init__(self, store: Store):
        self.store = store

    async def create(self, owner, title, key):
        if isinstance(title, str):
            title = re.sub(r"^[\s\ufeff]+|[\s\ufeff]+$", "", title)
        if not isinstance(title, str) or not 1 <= len(title) <= 100:
            raise Fault("INVALID_REQUEST")
        title, key = title.strip(), identifier(key)
        async with self.store.transaction() as db:
            await db.lock("work:" + owner + ":" + key)
            previous = await db.created_work(owner, key)
            if previous:
                if previous["title"] != title:
                    raise Fault("IDEMPOTENCY_CONFLICT", 409)
                return previous
            return await db.create_work(owner, title, key)

    async def get(self, owner, work_id):
        work_id = identifier(work_id, "NOT_FOUND", 404)
        async with self.store.transaction() as db:
            row = await db.work(owner, work_id)
        if not row:
            raise Fault("NOT_FOUND", 404)
        return row

    async def list(self, owner, limit=20, cursor=None):
        if not 1 <= limit <= 100:
            raise Fault("INVALID_REQUEST")
        position = None
        if cursor:
            try:
                if len(cursor) > 256:
                    raise ValueError
                stamp, key = json.loads(base64.urlsafe_b64decode(cursor))
                stamp = datetime.fromisoformat(stamp)
                if stamp.tzinfo is None:
                    raise ValueError
                position = (stamp, identifier(key))
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise Fault("INVALID_REQUEST") from None
        async with self.store.transaction() as db:
            rows = await db.list_works(owner, limit + 1, position)
        more, rows = len(rows) > limit, rows[:limit]
        cursor = None
        if more:
            cursor = base64.urlsafe_b64encode(
                json.dumps([rows[-1]["created_at"].isoformat(), rows[-1]["id"]]).encode()
            ).decode()
        return {"items": rows, "next_cursor": cursor}
