"""Real provider narrative evaluations; outputs synthetic transcripts, never credentials."""

import asyncio
import copy
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from echosim.adapters.credentials import Credentials
from echosim.adapters.model_connection import OpenCodeProvider
from echosim.adapters.store import PostgresStore
from echosim.config import load_settings
from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.domain.narrative import event_valid
from echosim.logging import JsonFormatter
from echosim.services.model_config import ModelConfigurations
from echosim.services.simulation import (
    ACTOR,
    VALIDATOR,
    Simulations,
    role_context,
    verdict,
)
from evaluate_simulation import CASES, STORY


class Memory:
    def __init__(self):
        self.events, self.state, self.ending = [], "running", None
        self.start = time.monotonic()

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def task_guard(self, task):
        return {"state": self.state}

    async def simulation_progress(self, task, stage, ending=None):
        self.ending = ending

    async def simulation_append(self, task, sequence, content):
        print(
            f"{self.case} event={sequence} actor={content['actor_id']} elapsed={time.monotonic() - self.start:.1f}s",
            file=sys.stderr,
            flush=True,
        )
        self.events.append(
            {
                "sequence": sequence,
                "content": content,
                "seconds": round(time.monotonic() - self.start, 2),
            }
        )

    async def task_finish(self, task, state):
        self.state = state


class Stories:
    def __init__(self):
        self.store = Memory()


async def main():
    logger = logging.getLogger("echosim")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    settings = load_settings()
    store = PostgresStore(settings.database_url.get_secret_value())
    provider = OpenCodeProvider()
    configs = ModelConfigurations(
        store, Credentials(settings.model_master_key.get_secret_value()), provider
    )
    async with store.transaction() as db:
        owner = (await db.user("experience-a"))["id"]
    version = (await configs.current(owner))["id"]
    config = await configs.bound(owner, version)
    sem = asyncio.Semaphore(int(os.environ.get("EVAL_CONCURRENCY", "1")))

    async def evaluate(case, full=False):
        async with sem:
            name, actor, action, expected = case
            stories = Stories()
            stories.store.case = name
            checks = []

            class ObservedSimulation(Simulations):
                async def ask(
                    self,
                    task,
                    config,
                    prompt,
                    context,
                    validate,
                    name,
                    temperature=0.7,
                    tokens=4096,
                ):
                    def observed(value):
                        try:
                            return validate(value)
                        except ModelFailure:
                            checks.append({"stage": name, "invalid_structure": value})
                            print(
                                f"{case[0]} rejected structure in {name}",
                                file=sys.stderr,
                                flush=True,
                            )
                            raise

                    return await super().ask(
                        task,
                        config,
                        prompt,
                        context,
                        observed,
                        name,
                        temperature,
                        tokens,
                    )

                async def check(self, task, config, context, proposed):
                    result = await self.ask(
                        task,
                        config,
                        VALIDATOR,
                        {"context": context, "proposed": proposed},
                        verdict,
                        "simulation-validation-v5",
                        0,
                        4096,
                    )
                    from echosim.services.simulation import RETRY_HINTS

                    self.rejection_hint = RETRY_HINTS.get(
                        result.get("category"), "请重新构思有效行动。"
                    )
                    if not result["accept"]:
                        checks.append({"proposed": proposed, "verdict": result})
                        print(
                            f"{case[0]} rejected: {result.get('reasons')}",
                            file=sys.stderr,
                            flush=True,
                        )
                    return result["accept"]

            simulation = ObservedSimulation(stories, provider)
            story = copy.deepcopy(STORY)
            if full:
                story["world_rules"] = (
                    "现实世界没有魔法，三人只能在灯室内行动。灯室内桌上有螺丝刀、备用灯泡、记录本和可用的手摇无线电；保险箱里有保险丝，林岫知道开箱密码。需要先检查线路再修灯，不可凭空变出关键物品。"
                )
                story["characters"][0]["public_information"] += (
                    "；主灯起初熄灭；必须先检查线路；陆舟想先求救，林岫希望先修灯，唐棠负责技术判断。"
                )
                if name == "full-confrontation":
                    story["characters"][2]["goal"] = (
                        "说服他人先用手摇无线电求救，避免修灯耽误救援"
                    )
                    story["characters"][0]["goal"] = (
                        "说服他人先修主灯，保障附近船只安全"
                    )
                elif name == "full-disclosure":
                    story["characters"][0]["goal"] = (
                        "希望修灯，但担心公开私人保险箱密码；决定是否亲自开箱或坦白"
                    )
                    story["characters"][1]["goal"] = (
                        "说服林岫提供保险丝，完成修复，不猜测他未公开的密码"
                    )
                story["outline"] += {
                    "full-confrontation": "船长要求立即发出求救信号，守塔人坚持先修灯。",
                    "full-repair": "三人必须先找到断路位置再共同修复。",
                    "full-disclosure": "修灯需要守塔人决定是否公开自己保险箱的密码。",
                }[name]
            task = {
                "id": str(uuid4()),
                "configuration_id": version,
                "input_data": {"story": story},
            }
            start = time.monotonic()
            try:
                if full:
                    async with asyncio.timeout(2700):
                        await simulation.run(task, config)
                    events = stories.store.events
                    lengths = [
                        sum(len(p["text"]) for p in e["content"]["segments"])
                        for e in events
                    ]
                    return {
                        "case": name,
                        "state": stories.store.state,
                        "ending": stories.store.ending,
                        "seconds": round(time.monotonic() - start, 2),
                        "lengths": lengths,
                        "events": events,
                        "human_review": "pending",
                        "story": story,
                        "rejected_candidates": checks,
                    }
                context = role_context(story, actor, [], {"scene": "灯室"})
                if action is None:
                    proposed = await simulation.ask(
                        task,
                        config,
                        ACTOR,
                        context,
                        lambda v: event_valid(v, actor, []),
                        "role-action-v8",
                    )
                else:
                    proposed = {
                        "actor_id": actor,
                        "segments": [
                            {
                                "kind": "narration",
                                "text": action,
                                "visibility": ["1", "2", "3"],
                            },
                            {
                                "kind": "narration",
                                "text": "他在灯室中站定，环顾周围，试图辨认灯光熄灭的原因。",
                                "visibility": ["1", "2", "3"],
                            },
                            {
                                "kind": "dialogue",
                                "speaker_id": actor,
                                "text": "先把当前情况说明白，再决定接下来怎么处理。",
                                "visibility": ["1", "2", "3"],
                            },
                        ],
                        "progress": action,
                        "references": [],
                    }
                accepted = await simulation.check(task, config, context, proposed)
                return {
                    "case": name,
                    "passed": accepted == expected,
                    "accepted": accepted,
                    "seconds": round(time.monotonic() - start, 2),
                }
            except (Fault, ModelFailure, TimeoutError, ValueError) as exc:
                return {
                    "case": name,
                    "passed": False,
                    "error": getattr(exc, "code", type(exc).__name__),
                    "story": story,
                    "events": stories.store.events,
                    "rejected_candidates": checks,
                }

    bad = [
        c
        for c in CASES
        if c[2] is not None and c[0] not in {"injection-2", "injection-3", "conflict-3"}
    ]
    cases = [(c, False) for c in CASES[:3] + bad] + [
        ((name, "1", None, True), True)
        for name in ("full-confrontation", "full-repair", "full-disclosure")
    ]
    if os.environ.get("EVAL_CASE"):
        cases = [(c, full) for c, full in cases if c[0] == os.environ["EVAL_CASE"]]
    if os.environ.get("EVAL_SHORT_ONLY"):
        cases = [(c, full) for c, full in cases if not full]
    if os.environ.get("EVAL_FULL_ONLY"):
        cases = [(c, full) for c, full in cases if full]

    async def record(case, full):
        result = await evaluate(case, full)
        if full:
            print(
                json.dumps(
                    {"completed_case": case[0], "result": result}, ensure_ascii=False
                ),
                file=sys.stderr,
                flush=True,
            )
        return result

    async with provider:
        results = await asyncio.gather(*(record(c, full) for c, full in cases))
    print(
        json.dumps(
            {
                "model": config["model"],
                "reasoning_effort": "low"
                if config["model"] == "deepseek-v4.1-flash"
                else "provider-default",
                "configuration_version": version,
                "prompt_version": "simulation-validation-v5",
                "actor_prompt": "role-action-v8",
                "architect_prompt": "architect-v8",
                "results": results,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
