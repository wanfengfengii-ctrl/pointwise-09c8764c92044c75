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

RETIREMENT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS observation_retirements (
    day TEXT NOT NULL,
    edition INTEGER NOT NULL,
    observation TEXT NOT NULL,
    station TEXT NOT NULL,
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


@dataclass(frozen=True)
class ObservationRetirement:
    observation: str
    station: str
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


def record_observation_retirements(
    db: sqlite3.Connection, edition: int, retirements: Iterable[ObservationRetirement]
) -> None:
    rows = tuple(retirements)
    db.executemany(
        '''INSERT INTO observation_retirements (day, edition, observation, station, source)
           VALUES (?, ?, ?, ?, ?)''',
        ((row.source.day, edition, row.observation, row.station,
          reference_to_text(row.source)) for row in rows),
    )


def _last_retired_editions(db: sqlite3.Connection, day: str, number: int) -> dict[str, int]:
    result: dict[str, int] = {}
    rows = db.execute(
        '''SELECT observation, max(edition) FROM observation_retirements
           WHERE day = ? AND edition <= ? GROUP BY observation''',
        (day, number),
    )
    for observation, edition in rows:
        result[observation] = edition
    return result


def observations_at(db: sqlite3.Connection, day: str, number: int) -> dict[str, ObservationRevision]:
    revisions: dict[str, ObservationRevision] = {}
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
    for observation, retired_edition in _last_retired_editions(db, day, number).items():
        revision = revisions.get(observation)
        if revision is not None and retired_edition >= revision.source.edition:
            del revisions[observation]
    return revisions


def retirement_counts(db: sqlite3.Connection, day: str, through: int) -> dict[int, int]:
    rows = db.execute(
        '''SELECT edition, count(*) FROM observation_retirements
           WHERE day = ? AND edition <= ? GROUP BY edition''',
        (day, through),
    )
    return {edition: count for edition, count in rows}
