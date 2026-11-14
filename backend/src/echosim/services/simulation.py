import asyncio
import json

from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.domain.narrative import event_valid, readable, visible_events
from echosim.services.narrative_prompts import ACTOR, ARCHITECT, VALIDATOR
from echosim.services.story import task_public
from echosim.services.story_generation import structured
from echosim.services.works import identifier


def public_story(story):
    # Author-facing synopsis/outline can disclose secrets or future events.
    return {
        "world_rules": story["world_rules"],
        "characters": [
            {k: c[k] for k in ("id", "name", "identity", "public_information")}
            for c in story["characters"]
        ],
    }


def role_context(story, actor, events, plan, canon=None):
    character = next(c for c in story["characters"] if c["id"] == actor)
    visible = visible_events(events, actor)
    return dict(
        story=public_story(story),
        actor_id=actor,
        own_private_knowledge=character["private_knowledge"],
        own_character=character,
        events=visible,
        allowed_event_references=[row["sequence"] for row in visible],
        scene=plan["scene"],
        opportunity=plan.get("reason", ""),
        canon=role_canon(canon or {}, actor),
    )


def role_canon(canon, actor):
    def allowed(row):
        return (
            actor in row.get("visibility", [])
            if actor
            else set(row.get("visibility", [])) == {"1", "2", "3"}
        )

    entities = [e for e in canon.get("entities", []) if allowed(e)]
    ids = {e["id"] for e in entities}
    return {
        "entities": entities,
        "relations": [
            r
            for r in canon.get("relations", [])
            if r.get("active", True) and allowed(r) and r["subject"] in ids and r["object"] in ids
        ],
        "memories": [m for m in canon.get("memories", []) if allowed(m)],
    }


def action_valid(value, actor, visible):
    fields = {"actor_id", "action", "dialogue", "result", "visibility", "references"}
    if not isinstance(value, dict) or set(value) != fields or value["actor_id"] != actor:
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    if not all(
        isinstance(value[k], str)
        and len(value[k]) <= 8000
        and (k == "dialogue" or value[k].strip())
        for k in ("action", "dialogue", "result")
    ):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    visibility, refs = value["visibility"], value["references"]
    if (
        not isinstance(visibility, list)
        or not visibility
        or any(v not in ["1", "2", "3"] for v in visibility)
        or actor not in visibility
    ):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    if not isinstance(refs, list) or any(
        type(r) is not int or r not in [e["sequence"] for e in visible] for r in refs
    ):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    return value


def plan_valid(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"scene", "next_actor", "finished", "reason"}
        or value["next_actor"] not in ("1", "2", "3")
        or type(value["finished"]) is not bool
        or any(
            not isinstance(value[k], str) or not 0 < len(value[k].strip()) <= 2000
            for k in ("scene", "reason")
        )
    ):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    return value


RETRY_HINTS = {
    "knowledge": "仅使用本人可见事实；私密动作和内心只标本人可见，不猜测他人秘密。",
    "agency": (
        "可以请求或影响他人，不写对方已经照做。请求、呼叫、发信一发出就结束本事件。"
        " 禁止代写回答或暗示对方有动作，含无线电另一端。只写本人选择。"
    ),
    "causality": "承接已接受行动的当前事实，不跳过必要前提，不改写既成后果。",
    "repetition": "产生新行动、信息或有代价的决定，不重复上一段建议和结论。",
    "format": "使用3-6有序段，区分本人对白与动作旁白，遵循输出字段和引用。",
}


def verdict(value):
    if not isinstance(value, dict) or type(value.get("accept")) is not bool:
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    if "category" in value and value["category"] not in (*RETRY_HINTS, "none"):
        raise ModelFailure("MODEL_INVALID_RESPONSE")
    return value


class Simulations:
    def __init__(self, stories, provider):
        self.stories, self.store, self.provider = stories, stories.store, provider

    async def read(self, owner, work, task, after=0):
        if type(after) is not int or not 0 <= after <= 24:
            raise Fault("INVALID_REQUEST")
        async with self.store.transaction() as db:
            await self.stories.owned(db, owner, work)
            row = await db.task_get(owner, work, identifier(task, "NOT_FOUND", 404))
            if not row or row["kind"] != "simulation":
                raise Fault("NOT_FOUND", 404)
            return {
                **task_public(row),
                "has_chapter": bool(await db.chapter(owner, work, task)),
                "version_id": row["input_data"]["version_id"],
                "events": [
                    readable(e) for e in await db.simulation_events(owner, work, task, after)
                ],
                "characters": {
                    c["id"]: c["name"] for c in row["input_data"]["story"]["characters"]
                },
                "progress": row.get("progress", {}),
                "canon_revision": row["input_data"].get("canon_revision"),
            }

    async def stop(self, owner, work, task):
        await self.read(owner, work, task)
        async with self.store.transaction() as db:
            row = await db.task_get(owner, work, task)
            await db.simulation_stop(row)
        return await self.read(owner, work, task)

    async def ask(
        self, task, config, prompt, context, validate, name, temperature=0.7, tokens=4096
    ):
        return await structured(
            self.provider,
            config,
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            validate,
            task["configuration_id"],
            session=task.get("id", task["configuration_id"]),
            tokens=tokens,
            temperature=temperature,
            prompt=name,
        )

    async def check(self, task, config, context, proposed):
        result = await self.ask(
            task,
            config,
            VALIDATOR,
            dict(context=context, proposed=proposed),
            verdict,
            "simulation-validation-v5",
            0,
            4096,
        )
        self.rejection_hint = RETRY_HINTS.get(
            result.get("category"), "请基于原可见事实重新构思有效行动。"
        )
        return result["accept"]

    async def boundary(self, task):
        async with self.store.transaction() as db:
            row = await db.task_guard(task)
            if not row:
                return False
            if row["state"] == "stopping":
                await db.task_finish(task, "stopped")
                return False
            return True

    async def stage(self, task, stage, ending=None):
        async with self.store.transaction() as db:
            if not await db.task_guard(task):
                return False
            await db.simulation_progress(task, stage, ending)
            return True

    async def run(self, task, config):
        story, events, scene = task["input_data"]["story"], [], None
        opening_scene = None
        for sequence in range(1, 25):
            if not await self.boundary(task):
                return
            async with asyncio.timeout(240):
                if not await self.stage(task, "planning"):
                    return
                context = dict(
                    story=public_story(story),
                    opening_scene=opening_scene,
                    scene=scene,
                    events=visible_events(events, None),
                    event_count=len(events),
                    canon=role_canon(task["input_data"].get("canon", {}), None),
                )
                for attempt in range(3):
                    plan = await self.ask(
                        task, config, ARCHITECT, context, plan_valid, "architect-v8", 0.3, 2048
                    )
                    if (events or not plan["finished"]) and await self.check(
                        task, config, context, plan
                    ):
                        break
                    if attempt == 2:
                        raise ModelFailure("STORY_CONFLICT")
                    context["retry_hint"] = "根据已有公开事实重新安排下一次行动机会或阶段性收束。"
                    await asyncio.sleep(attempt + 1)
                if plan["finished"]:
                    async with self.store.transaction() as db:
                        row = await db.task_guard(task)
                        if row:
                            stopped = row["state"] == "stopping"
                            await db.simulation_progress(
                                task, "complete", "user_stopped" if stopped else "resolved"
                            )
                            await db.task_finish(task, "stopped" if stopped else "succeeded")
                    return
                if opening_scene is None:
                    opening_scene = plan["scene"]
                scene, actor = plan["scene"], plan["next_actor"]
                context = role_context(story, actor, events, plan, task["input_data"].get("canon"))
                for attempt in range(3):
                    if not await self.stage(task, "generating"):
                        return
                    action = await self.ask(
                        task,
                        config,
                        ACTOR,
                        context,
                        lambda v, a=actor, c=context: event_valid(v, a, c["events"]),
                        "role-action-v8",
                    )
                    if not await self.stage(task, "validating"):
                        return
                    if await self.check(task, config, context, action):
                        break
                    if attempt == 2:
                        raise ModelFailure("STORY_CONFLICT")
                    context["retry_hint"] = getattr(
                        self, "rejection_hint", "请基于原可见事实重新构思有效行动。"
                    )
                    await asyncio.sleep(attempt + 1)
            async with self.store.transaction() as db:
                row = await db.task_guard(task)
                if not row:
                    return
                await db.simulation_append(task, sequence, action)
                if row["state"] == "stopping" or sequence == 24:
                    stopped = row["state"] == "stopping"
                    await db.simulation_progress(
                        task, "complete", "user_stopped" if stopped else "event_limit"
                    )
                    await db.task_finish(task, "stopped" if stopped else "succeeded")
                    return
            events.append({"sequence": sequence, "content": action})
