from dataclasses import asdict, dataclass
import json
import sqlite3


REQUEST_SCHEMA = '''
CREATE TABLE IF NOT EXISTS source_requests (
    day TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    parameters TEXT NOT NULL,
    edition INTEGER NOT NULL,
    PRIMARY KEY (day, request_id)
)
'''


@dataclass(frozen=True)
class RequestRecord:
    day: str
    request_id: str
    operation: str
    parameters: str
    edition: int


def request_parameters(batch) -> str:
    return json.dumps(asdict(batch), sort_keys=True, separators=(',', ':'))


def lookup_request(db: sqlite3.Connection, day: str, request_id: str) -> RequestRecord | None:
    row = db.execute(
        '''SELECT day, request_id, operation, parameters, edition
           FROM source_requests WHERE day = ? AND request_id = ?''',
        (day, request_id),
    ).fetchone()
    return RequestRecord(*row) if row is not None else None


def verify_request_parameters(record: RequestRecord, operation: str, parameters: str) -> None:
    if (record.operation, record.parameters) != (operation, parameters):
        raise ValueError(f'conflicting parameters for request {record.request_id}')


def record_request(db: sqlite3.Connection, record: RequestRecord) -> None:
    db.execute(
        '''INSERT INTO source_requests (day, request_id, operation, parameters, edition)
           VALUES (?, ?, ?, ?, ?)''',
        (record.day, record.request_id, record.operation, record.parameters, record.edition),
    )
