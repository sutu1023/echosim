from echosim.domain.accounts import Fault


def editable(simulation):
    if simulation["state"] not in ("succeeded", "stopped") or simulation["decision"] not in (
        "pending",
        "adopted",
    ):
        raise Fault("RESULT_INELIGIBLE", 409)


def check_version(draft, expected):
    if (draft["version"] if draft else 0) != expected:
        raise Fault("CHAPTER_STALE", 409)


def evidence(rows, body):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 100:
        raise ValueError
    previous = 0
    for row in rows:
        text, visibility = row["text"], row["visibility"]
        if not isinstance(text, str) or not text.strip() or body.find(text, previous) < 0:
            raise ValueError
        previous = body.index(text, previous) + len(text)
        if not isinstance(visibility, list) or not set(visibility) <= {"1", "2", "3"}:
            raise ValueError
    return [{"text": row["text"], "visibility": row["visibility"]} for row in rows]
