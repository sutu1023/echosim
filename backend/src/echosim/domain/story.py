from echosim.domain.model_connection import ModelFailure


def candidate(data):
    fields = {"synopsis", "world_rules", "outline", "characters"}
    character_fields = {
        "id",
        "name",
        "identity",
        "goal",
        "background",
        "public_information",
        "private_knowledge",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    if not all(
        isinstance(data[k], str) and 0 < len(data[k].strip()) <= 12000
        for k in fields - {"characters"}
    ):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    rows = data["characters"]
    if not isinstance(rows, list) or len(rows) != 3:
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    for row in rows:
        if not isinstance(row, dict) or set(row) != character_fields:
            raise ModelFailure("MODEL_INVALID_RESPONSE")
        if not all(isinstance(v, str) and 0 < len(v.strip()) <= 4000 for v in row.values()):
            raise ModelFailure("MODEL_INVALID_RESPONSE")
    if {r["id"] for r in rows} != {"1", "2", "3"}:
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    return data
