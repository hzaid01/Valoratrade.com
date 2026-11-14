"""
Scheduled Retraining Job (Cloud Run Job entry point: python -m jobs.retrain)

Runs the SAME pipeline as POST /api/training/trigger
(app.api.training.run_training_job): job lock, system state machine,
windowed baselines, champion/challenger gates, FIRST_MODEL_GATE.

Previously this module had its own copy of the pipeline that skipped the
job lock and the state machine, so a Cloud Run Job could train and promote
concurrently with the API's scheduled training for the same symbol.

Env:
  SYMBOLS   comma-separated, default BTCUSDT,ETHUSDT,SOLUSDT
"""
import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def symbols_from_env() -> List[str]:
    raw = os.getenv("SYMBOLS", "")
    symbols = [s.strip().upper() for s in raw.split(",") if s.strip()]
    return symbols or list(DEFAULT_SYMBOLS)


class RetrainJob:
    """Thin wrapper kept for compatibility; delegates to run_training_job."""

    async def run(self, symbols: List[str], force: bool = False) -> Dict:
        from app.api.training import run_training_job
        return await run_training_job(symbols, force=force)


async def main():
    """Entry point for scheduled job."""
    symbols = symbols_from_env()
    logger.info(f"Starting retraining job for {symbols}")
    results = await RetrainJob().run(symbols)
    logger.info(f"Retraining complete: {results}")
    return results


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
