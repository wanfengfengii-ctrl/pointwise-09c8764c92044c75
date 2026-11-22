from dataclasses import dataclass
from os import PathLike
import sqlite3
from typing import Callable

from warehouse.audit.read import AuditResult, read_edition
from warehouse.district.advance import advance_ready_editions
from warehouse.district.balance import BALANCE_SCHEMA
from warehouse.district.inbox import INBOX_SCHEMA, load_pending_edition, stage_edition
from warehouse.district.postings import POSTING_SCHEMAS, load_posting, read_frontier
from warehouse.district.receipts import RECEIPT_SCHEMA, Receipt
from warehouse.edition.artifacts import decode_edition, same_material
from warehouse.intake.provenance import require_label
from warehouse.ledger.journal import open_store, store_owner


@dataclass(frozen=True)
class ReceiveResult:
    status: str
    frontier: int
    result: AuditResult | None
    completed: tuple[AuditResult, ...]

    @property
    def receipt(self) -> Receipt | None:
        return self.result.receipt if self.result is not None else None


def open_district(path: str | PathLike[str], district: str) -> sqlite3.Connection:
    require_label(district, 'district')
    schemas = (INBOX_SCHEMA, BALANCE_SCHEMA, *POSTING_SCHEMAS, RECEIPT_SCHEMA)
    return open_store(path, 'district', district, schemas)


def receive_edition(
    db: sqlite3.Connection, payload: bytes, *, before_commit: Callable[[], None] | None = None
) -> ReceiveResult:
    material = decode_edition(payload)
    day, district, number = material.day, material.district, material.number
    if store_owner(db) != ('district', district):
        raise ValueError(f'material is for a different district: {district}')
    with db:
        db.execute('BEGIN IMMEDIATE')
        existing = load_posting(db, day, district, number)
        if existing is not None:
            if not same_material(decode_edition(existing.payload), material):
                raise ValueError(f'conflicting material for posted edition {number}')
            return ReceiveResult('posted', read_frontier(db, day, district),
                                 read_edition(db, day, district, number), ())
        pending = load_pending_edition(db, day, district, number)
        if pending is None:
            stage_edition(db, material)
        elif not same_material(pending.material, material):
            raise ValueError(f'conflicting material for pending edition {number}')
        receipts = advance_ready_editions(db, day, district)
        completed = tuple(read_edition(db, day, district, receipt.edition) for receipt in receipts)
        result = next((item for item in completed if item.edition == number), None)
        frontier = read_frontier(db, day, district)
        if completed and before_commit is not None:
            before_commit()
        return ReceiveResult('posted' if result is not None else 'pending', frontier, result, completed)
