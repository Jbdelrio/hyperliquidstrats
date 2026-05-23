"""Tiny logging helper — single logger shared across the project."""
from __future__ import annotations

import logging
import sys

from config.settings import LOG_LEVEL

_LOGGER: logging.Logger | None = None


def get_logger(name: str = "funding_rate_tool") -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    _LOGGER = logger
    return logger
