from dataclasses import dataclass
from datetime import date
from typing import Iterable

from warehouse.intake.provenance import MAX_INTEGER, SourcePosition, require_label


@dataclass(frozen=True)
class ObservationInput:
    observation: str
    station: str
    litres: int
    position: SourcePosition


@dataclass(frozen=True)
class MembershipInput:
    station: str
    district: str
    position: SourcePosition


@dataclass(frozen=True)
class ValueCorrection:
    observation: str
    litres: int
    position: SourcePosition


def validate_day(day: str) -> None:
    if not isinstance(day, str):
        raise ValueError('day must use YYYY-MM-DD')
    try:
        parsed = date.fromisoformat(day)
    except ValueError as error:
        raise ValueError('day must use YYYY-MM-DD') from error
    if parsed.isoformat() != day:
        raise ValueError('day must use YYYY-MM-DD')


def validate_litres(litres: object) -> None:
    if type(litres) is not int or not 0 <= litres <= MAX_INTEGER:
        raise ValueError('litres must be a nonnegative integer within the single-reading range')


def validate_request_scope(day: str, request_id: str) -> None:
    validate_day(day)
    require_label(request_id, 'request id')


def validate_source_positions(positions: Iterable[SourcePosition]) -> None:
    seen = set()
    for position in positions:
        if not isinstance(position, SourcePosition):
            raise ValueError('each reported row requires a source position')
        if position in seen:
            raise ValueError(f'duplicate source position: {position.document}/{position.row}')
        seen.add(position)


def _checked_rows(rows: tuple, row_type: type, field: str) -> None:
    if not isinstance(rows, tuple) or any(not isinstance(row, row_type) for row in rows):
        raise ValueError(f'{field} must be a tuple of {row_type.__name__} records')


def _new_key(seen: set[str], key: str, field: str) -> None:
    require_label(key, field)
    if key in seen:
        raise ValueError(f'duplicate {field}: {key}')
    seen.add(key)


def _check_memberships(rows: tuple, districts: set[str], known_stations: set[str] | None) -> set[str]:
    _checked_rows(rows, MembershipInput, 'memberships')
    stations: set[str] = set()
    for row in rows:
        _new_key(stations, row.station, 'station')
        require_label(row.district, 'district')
        if row.district not in districts:
            raise ValueError(f'unknown district: {row.district}')
        if known_stations is not None and row.station not in known_stations:
            raise ValueError(f'unknown station: {row.station}')
    return stations


def validate_opening(batch) -> None:
    validate_request_scope(batch.day, batch.request_id)
    if not isinstance(batch.districts, tuple) or not batch.districts:
        raise ValueError('opening requires a nonempty district tuple')
    districts: set[str] = set()
    for district in batch.districts:
        _new_key(districts, district, 'district')
    stations = _check_memberships(batch.memberships, districts, None)
    _checked_rows(batch.observations, ObservationInput, 'observations')
    observations: set[str] = set()
    for row in batch.observations:
        _new_key(observations, row.observation, 'observation')
        require_label(row.station, 'station')
        if row.station not in stations:
            raise ValueError(f'missing district for station: {row.station}')
        validate_litres(row.litres)
    validate_source_positions(row.position for row in (*batch.observations, *batch.memberships))


def validate_correction(
    batch, known_observations: set[str], known_stations: set[str], districts: set[str]
) -> None:
    validate_request_scope(batch.day, batch.request_id)
    _check_memberships(batch.memberships, districts, known_stations)
    _checked_rows(batch.observations, ValueCorrection, 'observations')
    observations: set[str] = set()
    for row in batch.observations:
        _new_key(observations, row.observation, 'observation')
        if row.observation not in known_observations:
            raise ValueError(f'unknown observation: {row.observation}')
        validate_litres(row.litres)
    validate_source_positions(row.position for row in (*batch.observations, *batch.memberships))
