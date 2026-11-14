import copy

import pytest

from echosim.domain.model_connection import ModelFailure
from echosim.domain.story_facts import fact_delta, merge_facts


def fixture():
    sources = {"event:1:1": {"text": "林岫把钥匙交给唐棠", "visibility": ["1"]}}
    delta = {
        "entities": [
            {"id": "key", "kind": "object", "name": "钥匙", "source": "event:1:1", "quote": "钥匙"}
        ],
        "relations": [
            {
                "id": "holder",
                "subject": "key",
                "predicate": "持有者",
                "object": "2",
                "source": "event:1:1",
                "quote": "交给唐棠",
            }
        ],
        "retire": [],
        "conflict": False,
    }
    base = {
        "entities": [
            {"id": "2", "kind": "character", "name": "唐棠", "visibility": ["1", "2", "3"]}
        ],
        "relations": [],
    }
    return delta, sources, base


def test_facts_require_source_and_derive_visibility():
    delta, sources, base = fixture()
    out = fact_delta(delta, sources, base)
    assert out["entities"][0]["visibility"] == ["1"]
    assert out["relations"][0]["visibility"] == ["1"]
    for mutation in [
        lambda d: d["entities"][0].update(source="missing"),
        lambda d: d["relations"][0].update(object="other-work"),
        lambda d: d["entities"][0].update(quote="invented"),
        lambda d: d["relations"][0].update(predicate="同一物品"),
        lambda d: d.update(conflict=True),
        lambda d: d.update(retire=["absent"]),
    ]:
        invalid = copy.deepcopy(delta)
        mutation(invalid)
        with pytest.raises(ModelFailure):
            fact_delta(invalid, sources, base)


def test_transfer_retires_relation_and_preserves_entity_identity():
    delta, sources, base = fixture()
    base["entities"].append({"id": "key", "kind": "object", "name": "钥匙", "visibility": ["1"]})
    base["relations"] = [
        {"id": "old-holder", "subject": "key", "object": "1", "predicate": "持有者", "active": True}
    ]
    delta["retire"] = ["old-holder"]
    result = merge_facts(base, fact_delta(delta, sources, base))
    assert len([e for e in result["entities"] if e["id"] == "key"]) == 1
    assert not result["relations"][0]["active"]
    assert result["relations"][1]["active"]


def test_relationship_id_cannot_silently_overwrite_history():
    delta, sources, base = fixture()
    base["relations"] = [
        {"id": "holder", "subject": "key", "object": "1", "predicate": "持有者", "active": True}
    ]
    with pytest.raises(ModelFailure):
        fact_delta(delta, sources, base)


def test_same_named_existing_entity_requires_identity_reuse():
    delta, sources, base = fixture()
    base["entities"].append({"id": "stable-key", "kind": "object", "name": "钥匙"})
    with pytest.raises(ModelFailure):
        fact_delta(delta, sources, base)


@pytest.mark.parametrize("field,count", [("entities", 101), ("relations", 201)])
def test_fact_capacity_rejects_without_truncation(field, count):
    delta, sources, base = fixture()
    delta[field] = [copy.deepcopy(delta[field][0]) for _ in range(count)]
    with pytest.raises(ModelFailure):
        fact_delta(delta, sources, base)


def test_creator_sources_do_not_become_role_knowledge():
    from echosim.services.simulation import role_canon

    canon = {
        "entities": [{"id": "secret", "name": "SECRET_OBJECT", "visibility": ["1"]}],
        "relations": [
            {
                "id": "secret-edge",
                "subject": "secret",
                "object": "secret",
                "visibility": ["1", "2", "3"],
            }
        ],
        "memories": [{"text": "SECRET_MEMORY", "visibility": ["1"]}],
        "sources": {"s": {"text": "SECRET_SOURCE", "visibility": ["1"]}},
    }
    assert "SECRET" in str(canon)
    assert "SECRET" not in str(role_canon(canon, "2"))
    assert "SECRET" not in str(role_canon(canon, None))
    assert "SECRET_OBJECT" in str(role_canon(canon, "1"))
    assert "SECRET_SOURCE" not in str(role_canon(canon, "1"))
