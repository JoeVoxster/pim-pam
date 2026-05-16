from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(log_dir: str | Path, level: str = "INFO") -> Path:
    target = Path(log_dir)
    target.mkdir(parents=True, exist_ok=True)
    log_file = target / "run.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    return log_file
