from __future__ import annotations

from enum import StrEnum


class JobType(StrEnum):
    LIVE = "LIVE"
    BACKTEST = "BACKTEST"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    STOP_REQUESTED = "STOP_REQUESTED"
    SUCCEEDED = "SUCCEEDED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class EventKind(StrEnum):
    LOG = "LOG"
    ORDER = "ORDER"
    TRADE = "TRADE"
    PROGRESS = "PROGRESS"
    STATUS = "STATUS"
    RISK = "RISK"
