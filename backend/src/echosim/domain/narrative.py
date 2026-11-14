from echosim.domain.model_connection import ModelFailure

ACTORS = {"1", "2", "3"}


def event_valid(value, actor, visible):
    def require(ok):
        if not ok:
            raise ModelFailure("MODEL_INVALID_RESPONSE")

    require(
        isinstance(value, dict) and set(value) == {"actor_id", "segments", "references", "progress"}
    )
    require(value["actor_id"] == actor)
    segments, refs = value["segments"], value["references"]
    require(isinstance(segments, list) and 3 <= len(segments) <= 6)
    require(
        isinstance(refs, list)
        and all(type(r) is int and r in {e["sequence"] for e in visible} for r in refs)
    )
    require(isinstance(value["progress"], str) and 0 < len(value["progress"].strip()) <= 600)
    result = []
    for i, part in enumerate(segments):
        require(isinstance(part, dict))
        kind = part.get("kind")
        require(isinstance(kind, str) and kind in {"narration", "dialogue"})
        require(
            set(part)
            == {"kind", "text", "visibility"} | ({"speaker_id"} if kind == "dialogue" else set())
        )
        require(isinstance(part["text"], str) and 0 < len(part["text"].strip()) <= 600)
        vis = part["visibility"]
        require(isinstance(vis, list) and all(isinstance(v, str) and v in ACTORS for v in vis))
        require(actor in vis and len(set(vis)) == len(vis))
        require(kind != "dialogue" or part["speaker_id"] == actor)
        result.append({**part, "segment_id": str(i + 1)})
    require(sum(len(s["text"]) for s in result) <= 1200)
    return {**value, "segments": result, "schema_version": 2}


def readable(row):
    content = row["content"]
    if "segments" in content:
        return row
    parts = []
    for key in ("action", "dialogue", "result"):
        if content.get(key):
            part = {
                "segment_id": str(len(parts) + 1),
                "kind": "dialogue" if key == "dialogue" else "narration",
                "text": content[key],
                "visibility": content.get("visibility", []),
            }
            if key == "dialogue":
                part["speaker_id"] = content["actor_id"]
            parts.append(part)
    return {**row, "content": {**content, "schema_version": 1, "segments": parts}}


def visible_events(events, actor):
    result = []
    for row in events:
        content = readable(row)["content"]
        parts = [
            p
            for p in content["segments"]
            if (actor in p["visibility"] if actor else set(p["visibility"]) == ACTORS)
        ]
        if parts:
            result.append(
                {
                    "sequence": row["sequence"],
                    "content": {"actor_id": content["actor_id"], "segments": parts},
                }
            )
    return result
