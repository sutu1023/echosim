import asyncio
import signal

from echosim.adapters.postgres import PostgresProbe
from echosim.config import load_settings
from echosim.logging import configure_logging, emit
from echosim.services.health import HealthService


async def run_worker(health: HealthService, interval: float, stop: asyncio.Event) -> None:
    emit("worker_started", service="worker")
    try:
        while not stop.is_set():
            probe = asyncio.create_task(health.ready())
            stopping = asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait({probe, stopping}, return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    break
                emit("worker_idle", service="worker", dependency="up" if probe.result() else "down")
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
    await run_worker(health, settings.worker_poll_seconds, stop)


def main() -> None:
    configure_logging()
    asyncio.run(serve())


if __name__ == "__main__":
    main()
