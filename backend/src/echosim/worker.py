import asyncio
import signal

from echosim.adapters.credentials import Credentials
from echosim.adapters.model_connection import OpenCodeProvider
from echosim.adapters.postgres import PostgresProbe
from echosim.adapters.store import PostgresStore
from echosim.config import load_settings
from echosim.domain.accounts import Fault
from echosim.logging import configure_logging, emit
from echosim.services.health import HealthService
from echosim.services.model_config import ModelConfigurations
from echosim.services.simulation import Simulations
from echosim.services.story import Stories
from echosim.services.story_worker import StoryWorker


async def run_worker(
    health: HealthService, interval: float, stop: asyncio.Event, jobs=None
) -> None:
    emit("worker_started", service="worker")
    try:
        while not stop.is_set():
            probe = asyncio.create_task(jobs.tick() if jobs else health.ready())
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait({probe, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    break
                try:
                    ready = probe.result()
                except Fault:
                    ready = False
                if jobs:
                    emit("worker_idle", service="worker", task_processed=ready)
                else:
                    emit("worker_idle", service="worker", dependency="up" if ready else "down")
            finally:
                for task in (probe, stopping):
                    task.cancel()
                await asyncio.gather(probe, stopping, return_exceptions=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass
    finally:
        emit("worker_stopped", service="worker")


async def serve() -> None:
    settings = load_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    health = HealthService(
        PostgresProbe(settings.database_url.get_secret_value()), settings.health_timeout_seconds
    )
    jobs = None
    if settings.model_master_key.get_secret_value():
        store = PostgresStore(settings.database_url.get_secret_value())
        provider = OpenCodeProvider()
        configs = ModelConfigurations(
            store, Credentials(settings.model_master_key.get_secret_value()), provider
        )
        simulation = Simulations(Stories(store, configs), provider)
        jobs = StoryWorker(store, configs, provider, simulation.run)
    await run_worker(
        health,
        min(settings.worker_poll_seconds, 5) if jobs else settings.worker_poll_seconds,
        stop,
        jobs,
    )


def main() -> None:
    configure_logging()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
