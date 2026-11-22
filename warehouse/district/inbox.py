from dataclasses import dataclass
import sqlite3

from warehouse.edition.artifacts import FrozenEdition, decode_edition, encode_edition


INBOX_SCHEMA = '''
CREATE TABLE IF NOT EXISTS pending_editions (
    day TEXT NOT NULL,
    district TEXT NOT NULL,
    number INTEGER NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (day, district, number)
)
'''


@dataclass(frozen=True)
class PendingEdition:
    material: FrozenEdition
    payload: bytes


def stage_edition(db: sqlite3.Connection, material: FrozenEdition) -> None:
    db.execute(
        'INSERT INTO pending_editions (day, district, number, payload) VALUES (?, ?, ?, ?)',
        (material.day, material.district, material.number, encode_edition(material)),
    )


def load_pending_edition(
    db: sqlite3.Connection, day: str, district: str, number: int
) -> PendingEdition | None:
    row = db.execute(
        'SELECT payload FROM pending_editions WHERE day = ? AND district = ? AND number = ?',
        (day, district, number),
    ).fetchone()
    if row is None:
        return None
    payload = row[0]
    material = decode_edition(payload)
    if (material.day, material.district, material.number) != (day, district, number):
        raise RuntimeError('pending edition is stored under a different key')
    return PendingEdition(material, payload)


def remove_pending_edition(db: sqlite3.Connection, day: str, district: str, number: int) -> None:
    result = db.execute(
        'DELETE FROM pending_editions WHERE day = ? AND district = ? AND number = ?',
        (day, district, number),
    )
    if result.rowcount != 1:
        raise RuntimeError('the edition being posted is missing from the inbox')
