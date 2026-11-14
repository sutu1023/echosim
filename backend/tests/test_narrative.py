import copy
import json

import pytest

from echosim.domain.model_connection import ModelFailure
from echosim.domain.narrative import event_valid, readable, visible_events


def event(actor="1", text="推开柜门，发现一条新线索。"):
    return {
        "actor_id": actor,
        "segments": [
            {"kind": "narration", "text": text, "visibility": ["1", "2", "3"]},
            {
                "kind": "dialogue",
                "text": "请看这里。",
                "speaker_id": actor,
                "visibility": ["1", "2", "3"],
            },
            {"kind": "narration", "text": "门后的线路露了出来。", "visibility": ["1", "2", "3"]},
        ],
        "references": [],
        "progress": "发现新线索",
    }


def test_event_contract_and_rejected_shapes():
    value = event_valid(event(), "1", [])
    assert value["schema_version"] == 2
    assert [s["segment_id"] for s in value["segments"]] == ["1", "2", "3"]
    for mutate in [
        lambda v: v.update(actor_id="2"),
        lambda v: v.update(segments=v["segments"][:2]),
        lambda v: v.update(references=[99]),
        lambda v: v["segments"][1].update(speaker_id="2"),
        lambda v: v["segments"][0].update(visibility=["4"]),
        lambda v: v["segments"][0].update(text="x" * 601),
        lambda v: v["segments"][0].update(kind="tool"),
    ]:
        bad = event()
        mutate(bad)
        with pytest.raises(ModelFailure):
            event_valid(bad, "1", [])


def test_segment_filter_removes_private_text_and_progress():
    data = event()
    data["segments"][0].update(text="secret-marker", visibility=["1"])
    data["progress"] = "secret-summary"
    row = {"sequence": 1, "content": event_valid(data, "1", [])}
    public = json.dumps(visible_events([row], "2"))
    assert "secret" not in public
    assert "请看这里" in json.dumps(visible_events([row], "2"), ensure_ascii=False)
    assert "secret-marker" in json.dumps(visible_events([row], "1"))
    assert "secret" not in json.dumps(visible_events([row], None))


def test_legacy_conversion_preserves_source_and_identity():
    row = {
        "id": "old",
        "sequence": 1,
        "content": {
            "actor_id": "1",
            "action": "开门",
            "dialogue": "来了",
            "result": "门开了",
            "visibility": ["1"],
            "round": 1,
        },
    }
    before = copy.deepcopy(row)
    result = readable(row)
    assert row == before and result["id"] == "old"
    assert [s["text"] for s in result["content"]["segments"]] == ["开门", "来了", "门开了"]
    assert not visible_events([row], "2")


@pytest.mark.parametrize(
    "stop,finish,expected,ending",
    [
        (False, True, 3, "resolved"),
        (False, False, 24, "event_limit"),
        (True, False, 1, "user_stopped"),
    ],
)
def test_dynamic_exchange_and_bounded_endings(stop, finish, expected, ending):
    import asyncio
    from contextlib import asynccontextmanager

    from test_story_generation import story

    from echosim.services.simulation import Simulations

    class Store:
        def __init__(self):
            self.events, self.state, self.ending = [], "running", None

        @asynccontextmanager
        async def transaction(self):
            yield self

        async def task_guard(self, task):
            return {"state": self.state}

        async def simulation_progress(self, task, stage, ending=None):
            self.ending = ending

        async def simulation_append(self, task, sequence, content):
            assert sequence == len(self.events) + 1
            self.events.append(content)

        async def task_finish(self, task, state):
            self.state = state

    class Stories:
        store = Store()

    class Run(Simulations):
        async def ask(self, task, config, prompt, context, validate, name, *args):
            if name == "architect-v8":
                count = len(self.store.events)
                assert context["opening_scene"] == ("灯室0" if count else None)
                return validate(
                    dict(
                        scene=f"灯室{count}",
                        next_actor="2" if len(self.store.events) == 1 else "1",
                        finished=finish and len(self.store.events) == 3,
                        reason="局势变化",
                    )
                )
            assert context["allowed_event_references"] == [e["sequence"] for e in context["events"]]
            assert context["opportunity"] == "局势变化"
            if stop:
                self.store.state = "stopping"
            return validate(event(context["actor_id"]))

        async def check(self, *args):
            return True

    run = Run(Stories(), None)
    asyncio.run(run.run({"input_data": {"story": story()}}, {}))
    assert len(run.store.events) == expected and run.store.ending == ending
    assert run.store.state == ("stopped" if stop else "succeeded")
    if expected >= 3:
        assert [e["actor_id"] for e in run.store.events[:3]] == ["1", "2", "1"]


def test_author_outline_and_other_background_never_enter_role_context():
    from test_story_generation import story as fixture

    from echosim.services.simulation import public_story, role_context

    story = fixture()
    story["outline"] = "SECRET_FUTURE"
    story["synopsis"] = "SECRET_SYNOPSIS"
    story["characters"][1]["background"] = "SECRET_BACKGROUND"
    value = str(role_context(story, "1", [], {"scene": "灯室"}))
    assert "SECRET_" not in value
    assert "SECRET_" not in str(public_story(story))


def test_validator_feedback_uses_only_fixed_safe_category():
    import asyncio

    from echosim.services.simulation import Simulations

    class Provider:
        async def generate(self, *args, **kwargs):
            return {
                "text": json.dumps(
                    {"accept": False, "category": "agency", "reasons": "REJECTED_SECRET_MARKER"}
                )
            }

    service = Simulations(type("Stories", (), {"store": None})(), Provider())
    assert not asyncio.run(
        service.check(
            {"configuration_id": "config"}, {"key": "synthetic", "model": "model"}, {}, {}
        )
    )
    assert "REJECTED_SECRET_MARKER" not in service.rejection_hint
    assert "不写对方已经照做" in service.rejection_hint
