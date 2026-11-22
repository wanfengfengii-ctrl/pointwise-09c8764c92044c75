from dataclasses import asdict, dataclass
import json
import sqlite3

from warehouse.intake.provenance import SourcePosition, require_positive_integer
from warehouse.intake.validate import validate_day


SOURCE_ROW_SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_rows (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    request_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    document TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    raw TEXT NOT NULL,
    PRIMARY KEY (day, edition, ordinal)
)
'''


@dataclass(frozen=True)
class SourceRow:
    day: str
    edition: int
    request_id: str
    ordinal: int
    kind: str
    position: SourcePosition
    raw: str


def source_rows_for_batch(batch, edition: int) -> tuple[SourceRow, ...]:
    rows = []
    groups = (('observation', batch.observations), ('membership', batch.memberships))
    for kind, inputs in groups:
        for item in inputs:
            raw = json.dumps(asdict(item), sort_keys=True, separators=(',', ':'))
            rows.append(SourceRow(
                batch.day, edition, batch.request_id, len(rows) + 1, kind, item.position, raw
            ))
    return tuple(rows)


def store_source_rows(db: sqlite3.Connection, rows: tuple[SourceRow, ...]) -> None:
    db.executemany(
        '''INSERT INTO source_rows
           (day, edition, ordinal, request_id, kind, document, source_row, raw)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        ((row.day, row.edition, row.ordinal, row.request_id, row.kind,
          row.position.document, row.position.row, row.raw) for row in rows),
    )


def list_expired_rows(db: sqlite3.Connection, day: str, through: int) -> tuple[SourceRow, ...]:
    validate_day(day)
    require_positive_integer(through, 'expiry cutoff')
    rows = db.execute(
        '''SELECT edition, request_id, ordinal, kind, document, source_row, raw
           FROM source_rows WHERE day = ? AND edition <= ? ORDER BY edition, ordinal''',
        (day, through),
    )
    return tuple(
        SourceRow(day, edition, request, ordinal, kind, SourcePosition(document, row), raw)
        for edition, request, ordinal, kind, document, row, raw in rows
    )


def remove_source_rows(db: sqlite3.Connection, day: str, through: int) -> int:
    validate_day(day)
    require_positive_integer(through, 'expiry cutoff')
    return db.execute(
        'DELETE FROM source_rows WHERE day = ? AND edition <= ?', (day, through)
    ).rowcount
