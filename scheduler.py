from __future__ import annotations

import logging
import time

import schedule

from main import run_pipeline

logger = logging.getLogger(__name__)


RUN_TIMES = ["09:00", "15:00", "21:00", "03:00"]


def start_local_scheduler(videos_per_run: int = 2) -> None:
    for run_time in RUN_TIMES:
        schedule.every().day.at(run_time).do(run_pipeline, videos=videos_per_run)
    logger.info("Scheduler local iniciado nos horarios: %s", ", ".join(RUN_TIMES))
    while True:
        schedule.run_pending()
        time.sleep(20)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start_local_scheduler()
