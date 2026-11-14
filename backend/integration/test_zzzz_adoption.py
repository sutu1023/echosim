import asyncio
import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from test_zzz_simulation import Provider

from echosim.adapters.credentials import Credentials
from echosim.adapters.passwords import Passwords
from echosim.adapters.store import PostgresStore
from echosim.domain.accounts import Fault
from echosim.domain.model_connection import ModelFailure
from echosim.services.accounts import Accounts
from echosim.services.adoption import Adoptions
from echosim.services.model_config import BASE_URL, ModelConfigurations
from echosim.services.simulation import Simulations, role_canon
from echosim.services.story import Stories
from echosim.services.story_graph import StoryGraph
from echosim.services.story_worker import StoryWorker
from echosim.services.works import Works


class Facts:
    fail = False

    async def build(self, task, config, base, sources):
        assert sources
        if task["kind"] == "adoption":
            assert all(
                k.startswith(f"event:{task['input_data']['simulation_id']}:") for k in sources
            )
        if self.fail:
            raise ModelFailure("STORY_CONFLICT")
        return {"entities": base["entities"], "relations": base["relations"]}


def test_adoption_atomicity_sequential_flow_and_isolation():
    async def scenario():
        store = PostgresStore(os.environ["ECHOSIM_TEST_DATABASE_URL"])
        await store.migrate()
        auth = Accounts(store, Passwords())
        await auth.provision("adoption-owner", "integration-password")
        session = await auth.login("adoption-owner", "integration-password", "adopt")
        owner = (await auth.current(session["token"]))["user_id"]
        provider = Provider()
        configs = ModelConfigurations(store, Credentials(Fernet.generate_key().decode()), provider)
        await configs.save(owner, BASE_URL, "test", "key", None)
        stories = Stories(store, configs)
        simulations = Simulations(stories, provider)
        adoptions = Adoptions(stories, Facts())
        graph_facts = Facts()
        graph = StoryGraph(stories, graph_facts)
        worker = StoryWorker(store, configs, provider, simulations.run, adoptions.run, graph.run)
        work = (await Works(store).create(owner, "采纳验证", str(uuid4())))["id"]
        prep = await stories.submit(owner, work, "preparation", str(uuid4()), {"idea": "灯塔"})
        await worker.tick()
        from echosim.services import story as story_module

        with patch.object(story_module, "enqueue_graph", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError, match="injected"):
                await stories.confirm(owner, work, prep["id"], prep["id"])
        async with store.transaction() as db:
            assert (await db.story_head(owner, work))["confirmed_id"] is None
        await stories.confirm(owner, work, prep["id"], prep["id"])
        await stories.confirm(owner, work, prep["id"], prep["id"])
        assert (
            len([t for t in (await stories.history(owner, work))["tasks"] if t["kind"] == "graph"])
            == 1
        )
        with pytest.raises(Fault, match="WORK_BUSY"):
            await stories.submit(owner, work, "simulation", str(uuid4()), {})
        graph_facts.fail = True
        await worker.tick()
        assert (await graph.read(owner, work))["state"] == "failed"
        with pytest.raises(Fault, match="GRAPH_NOT_READY"):
            await stories.submit(owner, work, "simulation", str(uuid4()), {})
        graph_facts.fail = False
        await graph.build(owner, work)
        async with store.transaction() as db:
            old_graph = await db.task_claim()
        stale_version = {
            **old_graph,
            "input_data": {**old_graph["input_data"], "version_id": str(uuid4())},
        }
        with pytest.raises(Fault, match="CANON_CONFLICT"):
            await graph.run(stale_version, {})
        async with store.transaction() as db:
            await db.execute(
                "UPDATE story_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",
                (old_graph["id"],),
            )
            await db.task_reap()
        await graph.run(old_graph, {})
        assert (await graph.read(owner, work))["state"] == "failed"
        await graph.build(owner, work)
        await worker.tick()
        baseline = await graph.read(owner, work)
        assert baseline["state"] == "ready" and len(baseline["content"]["entities"]) == 3
        assert (await graph.read(owner, work)) == baseline
        with pytest.raises(Fault, match="NOT_FOUND"):
            await graph.read(str(uuid4()), work)
        sim = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        await worker.tick()
        assert (await simulations.read(owner, work, sim["id"]))["decision"] == "pending"
        for kind, data in [("simulation", {}), ("preparation", {"idea": "新设定"})]:
            with pytest.raises(Fault, match="RESULT_PENDING"):
                await stories.submit(owner, work, kind, str(uuid4()), data)
        assert await graph.read(owner, work) == baseline
        payload = {"expected_canon_revision": 0, "acknowledge_irreversible": True}
        with pytest.raises(Fault, match="NOT_FOUND"):
            await adoptions.choose(str(uuid4()), work, sim["id"], str(uuid4()), payload)

        old_config = (await configs.current(owner))["id"]
        new_config = await configs.save(owner, BASE_URL, "current-model", "current-key", old_config)
        async with store.transaction() as db:
            assert (await db.config_get(owner, old_config))["ciphertext"] is None
        lost = await adoptions.choose(owner, work, sim["id"], str(uuid4()), payload)
        async with store.transaction() as db:
            stale = await db.task_claim()
            assert stale["id"] == lost["id"]
            assert stale["configuration_id"] == new_config["id"]
            await db.execute(
                "UPDATE story_tasks SET lease_until=now()-interval '1 second' WHERE id=%s",
                (lost["id"],),
            )
            await db.task_reap()
        assert (await stories.task(owner, work, lost["id"]))["error_code"] == "WORKER_LOST"
        await adoptions.run(stale, {})
        assert (await simulations.read(owner, work, sim["id"]))["decision"] == "pending"
        assert (await graph.read(owner, work))["canon_revision"] == 0
        timed_out = await adoptions.choose(owner, work, sim["id"], str(uuid4()), payload)
        timeout = asyncio.timeout

        async def stalled(*_):
            await asyncio.Event().wait()

        with (
            patch.object(adoptions.facts, "build", stalled),
            patch.object(
                asyncio, "timeout", lambda seconds: timeout(0 if seconds == 600 else seconds)
            ),
        ):
            await worker.tick()
        assert (await stories.task(owner, work, timed_out["id"]))["error_code"] == "TASK_TIMEOUT"
        assert (await simulations.read(owner, work, sim["id"]))["decision"] == "pending"
        assert await graph.read(owner, work) == baseline
        for table, condition in [
            ("story_canons", "TRUE"),
            ("story_tasks", "NEW.decision='adopted'"),
            ("work_story_heads", "NEW.canon_revision>0"),
        ]:
            key = str(uuid4())
            task = await adoptions.choose(owner, work, sim["id"], key, payload)
            assert (await adoptions.choose(owner, work, sim["id"], key, payload))["id"] == task[
                "id"
            ]
            with pytest.raises(Fault, match="IDEMPOTENCY_CONFLICT"):
                await adoptions.choose(
                    owner, work, sim["id"], key, {**payload, "expected_canon_revision": 1}
                )
            for kind, data in [("simulation", {}), ("preparation", {"idea": "新设定"})]:
                with pytest.raises(Fault, match="WORK_BUSY"):
                    await stories.submit(owner, work, kind, str(uuid4()), data)
            with pytest.raises(Fault, match="WORK_BUSY"):
                await adoptions.choose(owner, work, sim["id"], str(uuid4()), {}, discard=True)
            async with store.transaction() as db:
                await db.execute(
                    "CREATE FUNCTION reject_adoption() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    f"BEGIN IF {condition} THEN RAISE EXCEPTION 'injected'; "
                    "END IF; RETURN NEW; END $$"
                )
                await db.execute(
                    f"CREATE TRIGGER reject_write BEFORE INSERT OR UPDATE ON {table} "
                    "FOR EACH ROW EXECUTE FUNCTION reject_adoption()"
                )
            await worker.tick()
            async with store.transaction() as db:
                assert (await db.story_head(owner, work))["canon_revision"] == 0
                assert not (await db.canon(owner, work, 1))["events"]
                assert (await db.task_get(owner, work, sim["id"]))["decision"] == "pending"
                await db.execute(f"DROP TRIGGER reject_write ON {table}")
                await db.execute("DROP FUNCTION reject_adoption()")
        assert await graph.read(owner, work) == baseline
        key = str(uuid4())
        tasks = await asyncio.gather(
            *(adoptions.choose(owner, work, sim["id"], key, payload) for _ in range(5))
        )
        assert len({t["id"] for t in tasks}) == 1

        async def observe_commit():
            return [await graph.read(owner, work) for _ in range(20)]

        _, observed = await asyncio.gather(worker.tick(), observe_commit())
        assert all(
            (g["canon_revision"] == 0 and not g["content"]["events"])
            or (g["canon_revision"] == 1 and len(g["content"]["events"]) == 24)
            for g in observed
        )
        async with store.transaction() as db:
            canon = await db.canon(owner, work, 1)
            assert len(canon["events"]) == 24 and len(canon["memories"]) == 72
            assert (await db.story_head(owner, work))["canon_revision"] == 1
        assert (await adoptions.choose(owner, work, sim["id"], key, payload))["id"] == tasks[0][
            "id"
        ]
        assert (await adoptions.choose(owner, work, sim["id"], str(uuid4()), payload))[
            "id"
        ] == tasks[0]["id"]
        restarted = Adoptions(Stories(store, configs), Facts())
        assert (await restarted.choose(owner, work, sim["id"], key, payload))["id"] == tasks[0][
            "id"
        ]
        with pytest.raises(Fault, match="PREPARATION_FROZEN"):
            await stories.confirm(owner, work, prep["id"], prep["id"])
        with pytest.raises(Fault, match="PREPARATION_FROZEN"):
            await stories.submit(owner, work, "preparation", str(uuid4()), {"idea": "修改"})
        adopted_graph = await graph.read(owner, work)
        assert adopted_graph["canon_revision"] == 1
        assert adopted_graph["content"]["events"] == canon["events"]
        # Existing canon remains readable if the pre-adoption graph baseline is absent.
        async with store.transaction() as db:
            await db.execute("DELETE FROM story_graph_baselines WHERE work_id=%s", (work,))
        assert (await graph.build(owner, work))["state"] == "ready"
        assert await graph.read(owner, work) == adopted_graph
        from psycopg.errors import ForeignKeyViolation, UniqueViolation

        async with store.transaction() as db:
            for statement, params, error in [
                (
                    "UPDATE work_story_heads SET pending_simulation_id=%s WHERE work_id=%s",
                    (str(uuid4()), work),
                    ForeignKeyViolation,
                ),
                (
                    "INSERT INTO story_canons(id,user_id,work_id,revision,"
                    "simulation_id,adoption_task_id,content) SELECT %s,user_id,work_id,"
                    "revision+1,simulation_id,adoption_task_id,content FROM story_canons "
                    "WHERE work_id=%s AND revision=1",
                    (str(uuid4()), work),
                    UniqueViolation,
                ),
            ]:
                with pytest.raises(error):
                    async with db.connection.transaction():
                        await db.execute(statement, params)
        next_task = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        async with store.transaction() as db:
            row = await db.task_get(owner, work, next_task["id"])
            assert row["input_data"]["canon_revision"] == 1
            assert row["input_data"]["canon"]["memories"] == canon["memories"]
        await worker.tick()
        discard_key = str(uuid4())
        await adoptions.choose(owner, work, next_task["id"], discard_key, {}, discard=True)
        await adoptions.choose(owner, work, next_task["id"], discard_key, {}, discard=True)
        with pytest.raises(Fault, match="RESULT_INELIGIBLE"):
            await adoptions.choose(owner, work, next_task["id"], str(uuid4()), payload)
        async with store.transaction() as db:
            assert (await db.story_head(owner, work))["canon_revision"] == 1
        assert await graph.read(owner, work) == adopted_graph
        race = await stories.submit(owner, work, "simulation", str(uuid4()), {})
        await worker.tick()
        count = len(provider.seen)
        with pytest.raises(Fault, match="CANON_CONFLICT"):
            await adoptions.choose(owner, work, race["id"], str(uuid4()), payload)
        assert len(provider.seen) == count
        outcomes = await asyncio.gather(
            adoptions.choose(
                owner, work, race["id"], str(uuid4()), {**payload, "expected_canon_revision": 1}
            ),
            adoptions.choose(owner, work, race["id"], str(uuid4()), {}, discard=True),
            return_exceptions=True,
        )
        assert sum(isinstance(r, Fault) for r in outcomes) == 1
        await worker.tick()
        final = await simulations.read(owner, work, race["id"])
        assert final["decision"] in ("adopted", "discarded")
        assert (await graph.read(owner, work))["canon_revision"] == (
            2 if final["decision"] == "adopted" else 1
        )
        completed, capacity_rejections = 0, 0
        for _ in range(10):
            count = len(provider.seen)
            try:
                continuation = await stories.submit(owner, work, "simulation", str(uuid4()), {})
            except Fault as exc:
                assert exc.code == "STORY_CAPACITY"
                assert len(provider.seen) == count
                capacity_rejections += 1
                continue
            await worker.tick()
            revision = (await graph.read(owner, work))["canon_revision"]
            await adoptions.choose(
                owner,
                work,
                continuation["id"],
                str(uuid4()),
                {**payload, "expected_canon_revision": revision},
            )
            await worker.tick()
            completed += 1
        assert completed >= 1 and capacity_rejections >= 1
        private = {"memories": [{"text": "secret-marker", "visibility": ["1"]}]}
        assert role_canon(private, "2")["memories"] == []

    asyncio.run(scenario())
