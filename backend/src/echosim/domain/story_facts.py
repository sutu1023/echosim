from echosim.domain.model_connection import ModelFailure


def fact_delta(value, sources, base):
    def require(ok):
        if not ok:
            raise ModelFailure("STORY_CONFLICT")

    require(
        isinstance(value, dict) and set(value) == {"entities", "relations", "retire", "conflict"}
    )
    require(value["conflict"] is False)
    require(all(isinstance(value[k], list) for k in ("entities", "relations", "retire")))
    require(len(value["entities"]) <= 100 and len(value["relations"]) <= 200)
    known = {e["id"]: e for e in base["entities"]}
    result = {"entities": [], "relations": [], "retire": value["retire"]}
    for kind in ("entities", "relations"):
        ids = set()
        fields = {"id", "source", "quote"} | (
            {"kind", "name"} if kind == "entities" else {"subject", "predicate", "object"}
        )
        for row in value[kind]:
            require(isinstance(row, dict) and set(row) == fields)
            require(all(isinstance(v, str) and 0 < len(v.strip()) <= 600 for v in row.values()))
            require(row["id"] not in ids and row["source"] in sources)
            ids.add(row["id"])
            source = sources[row["source"]]
            require(row["quote"] in source["text"])
            if kind == "entities":
                require(row["kind"] in ("character", "place", "object"))
                require(row["kind"] != "character" or row["id"] in ("1", "2", "3"))
                require(row["name"] in source["text"])
                require(
                    row["id"] not in known
                    or all(known[row["id"]][k] == row[k] for k in ("name", "kind"))
                )
                require(
                    row["id"] in known
                    or not any(
                        e["kind"] == row["kind"] and e["name"] == row["name"]
                        for e in known.values()
                    )
                )
                known[row["id"]] = row
            else:
                require(row["subject"] in known and row["object"] in known)
                require(
                    row["predicate"]
                    not in {"同一实体", "同一物品", "同一人物", "别名", "又称", "也叫"}
                )
                previous = next((r for r in base["relations"] if r["id"] == row["id"]), None)
                require(
                    previous is None
                    or all(previous[k] == row[k] for k in ("subject", "predicate", "object"))
                )
            result[kind].append({**row, "visibility": source["visibility"], "active": True})
    old = {r["id"] for r in base["relations"] if r.get("active", True)}
    require(all(isinstance(r, str) and r in old for r in value["retire"]))
    return result


def merge_facts(base, delta):
    entities = {e["id"]: e for e in base["entities"]}
    entities.update({e["id"]: e for e in delta["entities"]})
    relations = {
        r["id"]: {**r, "active": False if r["id"] in delta["retire"] else r.get("active", True)}
        for r in base["relations"]
    }
    relations.update({r["id"]: r for r in delta["relations"]})
    current = [
        r for r in relations.values() if r["active"] and r["predicate"] in ("持有者", "位于")
    ]
    if len({(r["subject"], r["predicate"]) for r in current}) != len(current):
        raise ModelFailure("STORY_CONFLICT")
    return {"entities": list(entities.values()), "relations": list(relations.values())}


def setting_sources(story, version):
    result = {}
    for key, text in story.items():
        if key == "world_rules":
            result[f"setting:{version}:{key}"] = {"text": text, "visibility": ["1", "2", "3"]}
    for c in story["characters"]:
        for key, text in c.items():
            if key in ("name", "identity", "public_information", "private_knowledge"):
                result[f"setting:{version}:{c['id']}:{key}"] = {
                    "text": text,
                    "visibility": [c["id"]] if key == "private_knowledge" else ["1", "2", "3"],
                }
    return result
