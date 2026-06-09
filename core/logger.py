"""Rotating file logger + console handler for Picurate."""
import logging
from logging.handlers import RotatingFileHandler
from core.paths import log_dir

_configured = False


def get_logger(name: str = "picurate") -> logging.Logger:
    global _configured
    logger = logging.getLogger(name)
    if not _configured:
        logger.setLevel(logging.DEBUG)
        fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

        fh = RotatingFileHandler(
            log_dir() / "picurate.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)

        logger.addHandler(fh)
        logger.addHandler(ch)
        _configured = True
    return logger
