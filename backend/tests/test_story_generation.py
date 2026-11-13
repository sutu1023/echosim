import asyncio
import json
from copy import deepcopy

import pytest

from echosim.domain.model_connection import ModelFailure
from echosim.domain.story import candidate
from echosim.services.story_generation import generate_candidate


def story():
    return dict(
        synopsis="海边灯塔失去灯光",
        world_rules="没有魔法",
        outline="三人寻找停电原因",
        characters=[
            dict(
                id=str(i),
                name="角色" + str(i),
                identity="守塔人",
                goal="恢复灯光",
                background="住在海边",
                public_information="熟悉灯塔",
                private_knowledge="藏有旧钥匙",
            )
            for i in range(1, 4)
        ],
    )


def test_candidate_rejects_missing_fields_and_roles():
    assert candidate(story()) == story()
    for field in story():
        data = deepcopy(story())
        del data[field]
        with pytest.raises(ModelFailure):
            candidate(data)
    data = story()
    data["characters"][1]["id"] = "1"
    with pytest.raises(ModelFailure):
        candidate(data)


def test_generation_bounded_retry_and_no_partial_publish():
    class Provider:
        calls = 0

        async def generate(self, *args, **kwargs):
            self.calls += 1
            return {"text": "{}"}

    async def scenario():
        p = Provider()

        async def no_sleep(_):
            pass

        with pytest.raises(ModelFailure):
            await generate_candidate(
                p, dict(key="key", model="model"), {"idea": "灯塔"}, "v1", sleep=no_sleep
            )
        assert p.calls == 3

        async def valid(*args, **kwargs):
            return {"text": json.dumps(story())}

        p.generate = valid
        assert (
            await generate_candidate(p, dict(key="key", model="model"), {"idea": "灯塔"}, "v1")
            == story()
        )

    asyncio.run(scenario())
