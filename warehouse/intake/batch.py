from dataclasses import dataclass
import sqlite3
from typing import Callable

from warehouse.intake.validate import MembershipInput, ObservationInput, validate_opening
from warehouse.ledger.commit import CommitResult, commit_opening


@dataclass(frozen=True)
class OpeningBatch:
    day: str
    request_id: str
    districts: tuple[str, ...]
    observations: tuple[ObservationInput, ...]
    memberships: tuple[MembershipInput, ...]


def open_day(
    db: sqlite3.Connection,
    batch: OpeningBatch,
    *,
    before_commit: Callable[[], None] | None = None,
) -> CommitResult:
    if not isinstance(batch, OpeningBatch):
        raise ValueError('opening requires an OpeningBatch')
    validate_opening(batch)
    return commit_opening(db, batch, before_commit=before_commit)
