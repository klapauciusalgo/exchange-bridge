"""Main application entry point for MEXC ↔ Telegram Trading Bridge."""
import asyncio
import logging
import signal
import sys
from config.settings import get_settings
from services.orchestrator import BridgeOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


async def main():
    settings = get_settings()
    orchestrator = BridgeOrchestrator(settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received.")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows fallback if needed

    try:
        await orchestrator.start()
        await stop_event.wait()
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application exited.")
