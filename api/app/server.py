"""Bounded graceful shutdown: mark readiness false before Uvicorn drains requests."""

import uvicorn

from app.main import create_app


class DrainingServer(uvicorn.Server):
    def __init__(self, application, config):
        self.application = application
        super().__init__(config)

    def handle_exit(self, sig, frame):
        self.application.state.health.draining = True
        return super().handle_exit(sig, frame)


def main():
    app = create_app()

    settings = app.state.settings
    DrainingServer(
        app,
        uvicorn.Config(
            app,
            host=settings.api_host,
            port=settings.api_port,
            access_log=False,
            log_config=None,
            timeout_graceful_shutdown=30,
        ),
    ).run()


if __name__ == "__main__":
    main()
