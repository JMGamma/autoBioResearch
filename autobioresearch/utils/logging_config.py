"""Structured logging setup."""
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(numeric_level)
    handlers: list[logging.Handler] = [console]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        # RotatingFileHandler flushes after every record and avoids Windows
        # block-buffering issues that plain FileHandler can have.
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=20 * 1024 * 1024,  # 20 MB
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(fmt)
        fh.setLevel(numeric_level)
        handlers.append(fh)

    logging.basicConfig(level=numeric_level, handlers=handlers, force=True)

    # Quiet noisy third-party loggers
    for noisy in ["httpx", "httpcore", "urllib3", "requests"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
