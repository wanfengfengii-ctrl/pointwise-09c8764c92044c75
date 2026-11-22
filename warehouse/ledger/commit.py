from dataclasses import dataclass
from os import PathLike
import sqlite3
from typing import Callable, TYPE_CHECKING

from warehouse.edition.artifacts import FROZEN_EDITION_SCHEMA
from warehouse.intake.provenance import make_source_reference
from warehouse.intake.requests import REQUEST_SCHEMA, RequestRecord, lookup_request, record_request, request_parameters, verify_request_parameters
from warehouse.intake.source_rows import SOURCE_ROW_SCHEMA, source_rows_for_batch, store_source_rows
from warehouse.intake.validate import MembershipInput, ValueCorrection, validate_correction, validate_request_scope
from warehouse.ledger.evidence import REVISION_EVIDENCE_SCHEMA, record_ordinary_evidence
from warehouse.ledger.replacement import REPLACEMENT_ALLOCATION_SCHEMA
from warehouse.ledger.facts import FACT_SCHEMA, ObservationRevision, RETIREMENT_SCHEMA, observations_at, record_observation_revisions
from warehouse.ledger.journal import JOURNAL_SCHEMAS, FormalEdition, districts_for_day, list_formal_editions, open_store, record_formal_edition, register_districts
from warehouse.ledger.membership import MEMBERSHIP_SCHEMA, MembershipRevision, memberships_at, record_membership_revisions
from warehouse.ledger.projection import DistrictProjection, project_day

if TYPE_CHECKING:
    from warehouse.intake.batch import OpeningBatch


@dataclass(frozen=True)
class CorrectionBatch:
    day: str
    request_id: str
    observations: tuple[ValueCorrection, ...] = ()
    memberships: tuple[MembershipInput, ...] = ()


@dataclass(frozen=True)
class CommitResult:
    day: str
    edition: int
    projections: tuple[DistrictProjection, ...]


def open_source(path: str | PathLike[str]) -> sqlite3.Connection:
    schemas = (*JOURNAL_SCHEMAS, REQUEST_SCHEMA, SOURCE_ROW_SCHEMA,
               FACT_SCHEMA, RETIREMENT_SCHEMA, REVISION_EVIDENCE_SCHEMA,
               REPLACEMENT_ALLOCATION_SCHEMA, MEMBERSHIP_SCHEMA, FROZEN_EDITION_SCHEMA)
    return open_store(path, 'source', '', schemas)


def _result(db: sqlite3.Connection, day: str, number: int) -> CommitResult:
    return CommitResult(day, number, project_day(db, day, number))


def _finish(
    db: sqlite3.Connection, batch, operation: str, parameters: str, edition: int,
    stations_by_observation: dict[str, str], before_commit: Callable[[], None] | None,
) -> CommitResult:
    def source_at(position):
        return make_source_reference(batch.day, edition, batch.request_id, position)

    facts = tuple(ObservationRevision(
        row.observation, stations_by_observation[row.observation], row.litres, source_at(row.position)
    ) for row in batch.observations)
    memberships = tuple(MembershipRevision(row.station, row.district, source_at(row.position))
                        for row in batch.memberships)
    store_source_rows(db, source_rows_for_batch(batch, edition))
    record_observation_revisions(db, facts)
    for row in facts:
        record_ordinary_evidence(db, batch.day, edition, row.observation, row.source, row.litres)
    record_membership_revisions(db, memberships)
    record_formal_edition(db, FormalEdition(
        batch.day, edition, batch.request_id, len(facts), len(memberships)
    ))
    record_request(db, RequestRecord(batch.day, batch.request_id, operation, parameters, edition))
    result = _result(db, batch.day, edition)
    if before_commit is not None:
        before_commit()
    return result


def commit_opening(
    db: sqlite3.Connection, batch: 'OpeningBatch', *,
    before_commit: Callable[[], None] | None = None,
) -> CommitResult:
    """Commit a batch that has passed the opening intake checks."""
    parameters = request_parameters(batch)
    with db:
        db.execute('BEGIN IMMEDIATE')
        record = lookup_request(db, batch.day, batch.request_id)
        if record is not None:
            verify_request_parameters(record, 'opening', parameters)
            return _result(db, record.day, record.edition)
        if list_formal_editions(db, batch.day):
            raise ValueError(f'day is already open: {batch.day}')
        register_districts(db, batch.day, batch.districts)
        return _finish(db, batch, 'opening', parameters, 1,
                       {row.observation: row.station for row in batch.observations}, before_commit)


def apply_batch(
    db: sqlite3.Connection, batch: CorrectionBatch, *,
    before_commit: Callable[[], None] | None = None,
) -> CommitResult:
    if not isinstance(batch, CorrectionBatch):
        raise ValueError('correction requires a CorrectionBatch')
    validate_request_scope(batch.day, batch.request_id)
    with db:
        db.execute('BEGIN IMMEDIATE')
        editions = list_formal_editions(db, batch.day)
        if not editions:
            raise LookupError(f'day must be open before correction: {batch.day}')
        parameters = request_parameters(batch)
        record = lookup_request(db, batch.day, batch.request_id)
        if record is not None:
            verify_request_parameters(record, 'correction', parameters)
            return _result(db, record.day, record.edition)
        current = editions[-1].number
        facts = observations_at(db, batch.day, current)
        memberships = memberships_at(db, batch.day, current)
        validate_correction(batch, set(facts), set(memberships), set(districts_for_day(db, batch.day)))
        stations = {key: fact.station for key, fact in facts.items()}
        return _finish(db, batch, 'correction', parameters, current + 1, stations, before_commit)
