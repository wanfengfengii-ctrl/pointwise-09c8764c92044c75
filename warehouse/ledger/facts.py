from dataclasses import dataclass
import sqlite3
from typing import Iterable

from warehouse.intake.provenance import SourceReference, reference_from_text, reference_to_text


FACT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS observation_revisions (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    observation TEXT NOT NULL,
    station TEXT NOT NULL,
    litres INTEGER NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (day, edition, observation)
)
'''


@dataclass(frozen=True)
class ObservationRevision:
    observation: str
    station: str
    litres: int
    source: SourceReference


def record_observation_revisions(
    db: sqlite3.Connection, revisions: Iterable[ObservationRevision]
) -> None:
    db.executemany(
        '''INSERT INTO observation_revisions (day, edition, observation, station, litres, source)
           VALUES (?, ?, ?, ?, ?, ?)''',
        ((row.source.day, row.source.edition, row.observation, row.station,
          row.litres, reference_to_text(row.source)) for row in revisions),
    )


def observations_at(db: sqlite3.Connection, day: str, number: int) -> dict[str, ObservationRevision]:
    revisions = {}
    rows = db.execute(
        '''SELECT edition, observation, station, litres, source FROM observation_revisions
           WHERE day = ? AND edition <= ? ORDER BY edition, observation''',
        (day, number),
    )
    for edition, observation, station, litres, text in rows:
        source = reference_from_text(text)
        if (source.day, source.edition) != (day, edition):
            raise RuntimeError('observation revision has mismatched provenance')
        revisions[observation] = ObservationRevision(observation, station, litres, source)
    return revisions
