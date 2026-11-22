from dataclasses import dataclass
from hashlib import sha256
import sqlite3


RECEIPT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS district_receipts (
    day TEXT NOT NULL,
    district TEXT NOT NULL,
    number INTEGER NOT NULL,
    token TEXT NOT NULL,
    PRIMARY KEY (day, district, number)
)
'''


@dataclass(frozen=True)
class Receipt:
    day: str
    district: str
    edition: int
    token: str


def receipt_token(payload: bytes) -> str:
    return 'water-' + sha256(payload).hexdigest()


def record_receipt(
    db: sqlite3.Connection, day: str, district: str, number: int, payload: bytes
) -> Receipt:
    token = receipt_token(payload)
    db.execute(
        'INSERT INTO district_receipts (day, district, number, token) VALUES (?, ?, ?, ?)',
        (day, district, number, token),
    )
    return Receipt(day, district, number, token)


def load_receipt(db: sqlite3.Connection, day: str, district: str, number: int) -> Receipt | None:
    row = db.execute(
        'SELECT token FROM district_receipts WHERE day = ? AND district = ? AND number = ?',
        (day, district, number),
    ).fetchone()
    return Receipt(day, district, number, row[0]) if row is not None else None
