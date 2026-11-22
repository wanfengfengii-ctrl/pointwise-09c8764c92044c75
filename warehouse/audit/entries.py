from dataclasses import dataclass
import sqlite3

from warehouse.district.postings import load_posted_entries
from warehouse.intake.provenance import SourceReference


@dataclass(frozen=True)
class AuditEntry:
    position: int
    action: str
    observation: str
    station: str
    litres: int
    source: SourceReference


def read_audit_entries(
    db: sqlite3.Connection, day: str, district: str, number: int
) -> tuple[AuditEntry, ...]:
    return tuple(AuditEntry(position, entry.action, entry.observation, entry.station, entry.litres, entry.source)
                 for position, entry in enumerate(load_posted_entries(db, day, district, number), 1))
