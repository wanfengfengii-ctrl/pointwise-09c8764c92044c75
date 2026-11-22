from dataclasses import dataclass
import sqlite3

from warehouse.district.balance import BalanceMeasure
from warehouse.edition.artifacts import FrozenEdition
from warehouse.edition.changes import DistrictChange
from warehouse.intake.provenance import reference_from_text, reference_to_text


POSTING_SCHEMAS = (
    '''CREATE TABLE IF NOT EXISTS posted_editions (
           day TEXT NOT NULL,
           district TEXT NOT NULL,
           number INTEGER NOT NULL,
           payload BLOB NOT NULL,
           row_count INTEGER NOT NULL,
           total_litres TEXT NOT NULL,
           PRIMARY KEY (day, district, number)
       )''',
    '''CREATE TABLE IF NOT EXISTS posted_entries (
           day TEXT NOT NULL,
           district TEXT NOT NULL,
           number INTEGER NOT NULL,
           position INTEGER NOT NULL,
           action TEXT NOT NULL,
           observation TEXT NOT NULL,
           station TEXT NOT NULL,
           litres INTEGER NOT NULL,
           source TEXT NOT NULL,
           PRIMARY KEY (day, district, number, position)
       )''',
)


@dataclass(frozen=True)
class PostedEdition:
    day: str
    district: str
    number: int
    payload: bytes
    measure: BalanceMeasure


def record_posting(
    db: sqlite3.Connection, material: FrozenEdition, payload: bytes, measure: BalanceMeasure
) -> PostedEdition:
    key = (material.day, material.district, material.number)
    db.execute(
        '''INSERT INTO posted_editions (day, district, number, payload, row_count, total_litres)
           VALUES (?, ?, ?, ?, ?, ?)''', (*key, payload, measure.row_count, str(measure.total_litres)),
    )
    db.executemany(
        '''INSERT INTO posted_entries
           (day, district, number, position, action, observation, station, litres, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        ((*key, position, entry.action, entry.observation, entry.station,
          entry.litres, reference_to_text(entry.source))
         for position, entry in enumerate(material.changes, 1)),
    )
    return PostedEdition(*key, payload, measure)


def load_posting(db: sqlite3.Connection, day: str, district: str, number: int) -> PostedEdition | None:
    row = db.execute(
        '''SELECT payload, row_count, total_litres FROM posted_editions
           WHERE day = ? AND district = ? AND number = ?''', (day, district, number),
    ).fetchone()
    if row is None:
        return None
    payload, row_count, total = row
    return PostedEdition(day, district, number, payload, BalanceMeasure(row_count, int(total)))


def read_frontier(db: sqlite3.Connection, day: str, district: str) -> int:
    (number,) = db.execute(
        'SELECT max(number) FROM posted_editions WHERE day = ? AND district = ?', (day, district),
    ).fetchone()
    return number if number is not None else 0


def load_posted_entries(
    db: sqlite3.Connection, day: str, district: str, number: int
) -> tuple[DistrictChange, ...]:
    entries = []
    rows = db.execute(
        '''SELECT position, action, observation, station, litres, source FROM posted_entries
           WHERE day = ? AND district = ? AND number = ? ORDER BY position''', (day, district, number),
    )
    for position, action, observation, station, litres, source in rows:
        if position != len(entries) + 1:
            raise RuntimeError('posting positions are not contiguous')
        entries.append(DistrictChange(action, observation, station, litres, reference_from_text(source)))
    return tuple(entries)
