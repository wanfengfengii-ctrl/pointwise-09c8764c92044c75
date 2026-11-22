from dataclasses import dataclass
import sqlite3

from warehouse.edition.artifacts import load_frozen_edition
from warehouse.intake.source_rows import remove_source_rows
from warehouse.ledger.journal import districts_for_day, list_formal_editions, require_formal_edition


@dataclass(frozen=True)
class ExpiryResult:
    removed: int
    blocked: tuple[tuple[int, str], ...]


def missing_frozen_editions(db: sqlite3.Connection, day: str) -> tuple[tuple[int, str], ...]:
    editions = list_formal_editions(db, day)
    districts = districts_for_day(db, day)
    if not editions:
        raise LookupError(f'no formal day for {day}')
    if not districts or any(edition.number != number for number, edition in enumerate(editions, 1)):
        raise RuntimeError('formal scope is incomplete')
    missing = []
    for edition in editions:
        for district in districts:
            try:
                material = load_frozen_edition(db, day, district, edition.number)
            except ValueError:
                material = None
            if material is None:
                missing.append((edition.number, district))
    return tuple(missing)


def expire_input(db: sqlite3.Connection, day: str, through: int) -> ExpiryResult:
    with db:
        db.execute('BEGIN IMMEDIATE')
        require_formal_edition(db, day, through)
        missing = missing_frozen_editions(db, day)
        if missing:
            return ExpiryResult(0, missing)
        return ExpiryResult(remove_source_rows(db, day, through), ())
