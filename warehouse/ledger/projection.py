from dataclasses import dataclass
import sqlite3

from warehouse.intake.provenance import SourceReference
from warehouse.intake.validate import validate_litres
from warehouse.ledger.facts import observations_at, retirement_counts
from warehouse.ledger.journal import FormalEdition, districts_for_day, list_formal_editions, require_formal_edition
from warehouse.ledger.membership import memberships_at


@dataclass(frozen=True)
class ProjectedObservation:
    observation: str
    station: str
    district: str
    litres: int
    source: SourceReference


@dataclass(frozen=True)
class DistrictProjection:
    day: str
    district: str
    edition: int
    rows: tuple[ProjectedObservation, ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def total_litres(self) -> int:
        return sum(row.litres for row in self.rows)


def _verify_complete_prefix(
    db: sqlite3.Connection, edition: FormalEdition, editions: tuple[FormalEdition, ...]
) -> None:
    prefix = tuple(item for item in editions if item.number <= edition.number)
    if len(prefix) != edition.number or any(
        item.number != expected for expected, item in enumerate(prefix, 1)
    ):
        raise RuntimeError('formal edition prefix is incomplete')
    for table, count_field in (('observation_revisions', 'fact_count'),
                               ('membership_revisions', 'membership_count')):
        expected_counts = {item.number: getattr(item, count_field) for item in prefix
                           if getattr(item, count_field)}
        actual_counts = dict(db.execute(
            f'SELECT edition, count(*) FROM {table} WHERE day = ? AND edition <= ? GROUP BY edition',
            (edition.day, edition.number),
        ))
        if actual_counts != expected_counts:
            raise RuntimeError(f'incomplete {table} for {edition.day} edition {edition.number}')
    expected_retirements = {item.number: item.retired_count for item in prefix if item.retired_count}
    if retirement_counts(db, edition.day, edition.number) != expected_retirements:
        raise RuntimeError(
            f'incomplete observation_retirements for {edition.day} edition {edition.number}')


def project_day(
    db: sqlite3.Connection, day: str, number: int | None = None
) -> tuple[DistrictProjection, ...]:
    if number is not None:
        edition = require_formal_edition(db, day, number)
    editions = list_formal_editions(db, day)
    if not editions:
        raise LookupError(f'no formal day for {day}')
    if number is None:
        edition = editions[-1]
    _verify_complete_prefix(db, edition, editions)
    districts = districts_for_day(db, day)
    if not districts:
        raise RuntimeError('formal day is missing its district scope')
    groups: dict[str, list[ProjectedObservation]] = {district: [] for district in districts}
    memberships = memberships_at(db, day, edition.number)
    if any(row.district not in groups for row in memberships.values()):
        raise RuntimeError('formal membership is outside the day district scope')
    observations = observations_at(db, day, edition.number)
    for key in sorted(observations):
        fact = observations[key]
        membership = memberships.get(fact.station)
        if membership is None:
            raise RuntimeError(f'formal projection has no membership for {fact.station}')
        validate_litres(fact.litres)
        groups[membership.district].append(ProjectedObservation(
            key, fact.station, membership.district, fact.litres, fact.source
        ))
    return tuple(DistrictProjection(day, district, edition.number, tuple(rows))
                 for district, rows in groups.items())


def project_district(
    db: sqlite3.Connection, day: str, district: str, number: int | None = None
) -> DistrictProjection:
    for projection in project_day(db, day, number):
        if projection.district == district:
            return projection
    raise LookupError(f'unknown district {district} for {day}')
