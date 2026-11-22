import sqlite3

from warehouse.district.balance import apply_district_changes
from warehouse.district.inbox import load_pending_edition, remove_pending_edition
from warehouse.district.postings import read_frontier, record_posting
from warehouse.district.receipts import Receipt, record_receipt
from warehouse.intake.provenance import MAX_INTEGER


def advance_ready_editions(db: sqlite3.Connection, day: str, district: str) -> tuple[Receipt, ...]:
    receipts = []
    frontier = read_frontier(db, day, district)
    while frontier < MAX_INTEGER:
        number = frontier + 1
        pending = load_pending_edition(db, day, district, number)
        if pending is None:
            break
        measure = apply_district_changes(db, day, district, pending.material.changes)
        record_posting(db, pending.material, pending.payload, measure)
        receipts.append(record_receipt(db, day, district, number, pending.payload))
        remove_pending_edition(db, day, district, number)
        frontier = number
    return tuple(receipts)
