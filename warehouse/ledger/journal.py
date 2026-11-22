from dataclasses import dataclass
from os import PathLike
import sqlite3

from warehouse.intake.provenance import require_positive_integer
from warehouse.intake.validate import validate_day


_OWNER_SCHEMA = '''
CREATE TABLE IF NOT EXISTS warehouse_owner (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    kind TEXT NOT NULL,
    district TEXT NOT NULL
)
'''

JOURNAL_SCHEMAS = (
    '''CREATE TABLE IF NOT EXISTS formal_districts (
           day TEXT NOT NULL,
           district TEXT NOT NULL,
           PRIMARY KEY (day, district)
       )''',
    '''CREATE TABLE IF NOT EXISTS formal_editions (
           day TEXT NOT NULL,
           number INTEGER NOT NULL,
           request_id TEXT NOT NULL,
           fact_count INTEGER NOT NULL,
           membership_count INTEGER NOT NULL,
           PRIMARY KEY (day, number)
       )''',
)


@dataclass(frozen=True)
class FormalEdition:
    day: str
    number: int
    request_id: str
    fact_count: int
    membership_count: int

    @property
    def previous(self) -> int:
        return self.number - 1


def open_store(
    path: str | PathLike[str], kind: str, district: str, schemas: tuple[str, ...]
) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute(_OWNER_SCHEMA)
            db.execute(
                '''INSERT INTO warehouse_owner (singleton, kind, district) VALUES (1, ?, ?)
                   ON CONFLICT(singleton) DO NOTHING''',
                (kind, district),
            )
            if store_owner(db) != (kind, district):
                raise ValueError('warehouse file is already bound to a different owner')
            for schema in schemas:
                db.execute(schema)
    except BaseException:
        db.close()
        raise
    return db


def store_owner(db: sqlite3.Connection) -> tuple[str, str]:
    row = db.execute('SELECT kind, district FROM warehouse_owner WHERE singleton = 1').fetchone()
    if row is None:
        raise RuntimeError('warehouse owner is missing')
    return row


def register_districts(db: sqlite3.Connection, day: str, districts: tuple[str, ...]) -> None:
    db.executemany('INSERT INTO formal_districts (day, district) VALUES (?, ?)',
                   ((day, district) for district in districts))


def districts_for_day(db: sqlite3.Connection, day: str) -> tuple[str, ...]:
    validate_day(day)
    rows = db.execute('SELECT district FROM formal_districts WHERE day = ? ORDER BY district', (day,))
    return tuple(district for (district,) in rows)


def record_formal_edition(db: sqlite3.Connection, edition: FormalEdition) -> None:
    require_positive_integer(edition.number, 'formal edition')
    db.execute(
        '''INSERT INTO formal_editions (day, number, request_id, fact_count, membership_count)
           VALUES (?, ?, ?, ?, ?)''',
        (edition.day, edition.number, edition.request_id, edition.fact_count, edition.membership_count),
    )


def require_formal_edition(db: sqlite3.Connection, day: str, number: int) -> FormalEdition:
    validate_day(day)
    require_positive_integer(number, 'formal edition')
    row = db.execute(
        '''SELECT day, number, request_id, fact_count, membership_count
           FROM formal_editions WHERE day = ? AND number = ?''', (day, number),
    ).fetchone()
    if row is None:
        raise LookupError(f'no formal edition {number} for {day}')
    return FormalEdition(*row)


def list_formal_editions(db: sqlite3.Connection, day: str) -> tuple[FormalEdition, ...]:
    validate_day(day)
    rows = db.execute(
        '''SELECT day, number, request_id, fact_count, membership_count
           FROM formal_editions WHERE day = ? ORDER BY number''', (day,),
    )
    return tuple(FormalEdition(*row) for row in rows)
