from dataclasses import dataclass
import sqlite3
from typing import Iterable

from warehouse.intake.provenance import SourceReference, reference_from_text, reference_to_text


MEMBERSHIP_SCHEMA = '''
CREATE TABLE IF NOT EXISTS membership_revisions (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    station TEXT NOT NULL,
    district TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (day, edition, station)
)
'''


@dataclass(frozen=True)
class MembershipRevision:
    station: str
    district: str
    source: SourceReference


def record_membership_revisions(
    db: sqlite3.Connection, revisions: Iterable[MembershipRevision]
) -> None:
    db.executemany(
        '''INSERT INTO membership_revisions (day, edition, station, district, source)
           VALUES (?, ?, ?, ?, ?)''',
        ((row.source.day, row.source.edition, row.station, row.district,
          reference_to_text(row.source)) for row in revisions),
    )


def memberships_at(db: sqlite3.Connection, day: str, number: int) -> dict[str, MembershipRevision]:
    revisions = {}
    rows = db.execute(
        '''SELECT edition, station, district, source FROM membership_revisions
           WHERE day = ? AND edition <= ? ORDER BY edition, station''',
        (day, number),
    )
    for edition, station, district, text in rows:
        source = reference_from_text(text)
        if (source.day, source.edition) != (day, edition):
            raise RuntimeError('membership revision has mismatched provenance')
        revisions[station] = MembershipRevision(station, district, source)
    return revisions
