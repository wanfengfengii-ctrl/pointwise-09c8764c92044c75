from dataclasses import dataclass
import sqlite3

from warehouse.audit.entries import AuditEntry, read_audit_entries
from warehouse.audit.reconcile import reconcile_posted_edition
from warehouse.audit.totals import EntryTotals
from warehouse.district.balance import require_local_district
from warehouse.district.postings import load_posting
from warehouse.district.receipts import Receipt, load_receipt, receipt_token
from warehouse.edition.artifacts import decode_edition
from warehouse.edition.changes import DistrictChange
from warehouse.intake.provenance import require_positive_integer
from warehouse.intake.validate import validate_day


@dataclass(frozen=True)
class AuditResult:
    day: str
    district: str
    edition: int
    entries: tuple[AuditEntry, ...]
    row_count: int
    total_litres: int
    totals: EntryTotals
    receipt: Receipt


def read_edition(db: sqlite3.Connection, day: str, district: str, number: int) -> AuditResult:
    validate_day(day)
    require_positive_integer(number, 'edition')
    require_local_district(db, district)
    posting = load_posting(db, day, district, number)
    if posting is None:
        raise LookupError(f'edition has not been posted: {day}/{district}/{number}')
    receipt = load_receipt(db, day, district, number)
    if receipt is None or receipt.token != receipt_token(posting.payload):
        raise RuntimeError('posted edition is missing its bound receipt')
    entries = read_audit_entries(db, day, district, number)
    material = decode_edition(posting.payload)
    recorded = tuple(DistrictChange(row.action, row.observation, row.station, row.litres, row.source)
                     for row in entries)
    if (material.day, material.district, material.number, material.changes) != (day, district, number, recorded):
        raise RuntimeError('posted entries disagree with the original edition material')
    measure = reconcile_posted_edition(db, posting, entries)
    return AuditResult(day, district, number, entries, measure.row_count,
                       measure.total_litres, measure.totals, receipt)
