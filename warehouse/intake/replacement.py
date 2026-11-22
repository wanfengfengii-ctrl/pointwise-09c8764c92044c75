from dataclasses import dataclass

from warehouse.intake.provenance import MAX_INTEGER, SourceReference, require_label
from warehouse.intake.validate import ObservationInput, validate_litres, validate_source_positions


# An allocation item is ``(retired key, new key, origin source reference, litres)``.
Allocation = tuple[str, str, SourceReference, int]


@dataclass(frozen=True)
class ReplacementBatch:
    day: str
    request_id: str
    station: str
    retired: tuple[tuple[str, SourceReference], ...]
    new: tuple[ObservationInput, ...]
    allocations: tuple[Allocation, ...]


def _checked(rows, row_type, field: str) -> None:
    if not isinstance(rows, tuple) or any(not isinstance(row, row_type) for row in rows):
        raise ValueError(f'{field} must be a tuple of {row_type.__name__} records')


def _check_allocation(item) -> Allocation:
    if not isinstance(item, tuple) or len(item) != 4:
        raise ValueError('each allocation requires (retired, new, origin, litres)')
    retired, new, origin, litres = item
    require_label(retired, 'allocation retired key')
    require_label(new, 'allocation new key')
    if not isinstance(origin, SourceReference):
        raise ValueError('allocation requires a source reference')
    if type(litres) is not int or not 1 <= litres <= MAX_INTEGER:
        raise ValueError('allocation litres must be a positive single-reading integer')
    return retired, new, origin, litres


def validate_replacement_scope(batch) -> None:
    require_label(batch.station, 'replacement station')
    if not isinstance(batch.retired, tuple) or not batch.retired:
        raise ValueError('replacement requires a nonempty retired tuple')
    if not isinstance(batch.new, tuple) or not batch.new:
        raise ValueError('replacement requires a nonempty new tuple')
    _checked(batch.new, ObservationInput, 'new observations')
    if not isinstance(batch.allocations, tuple):
        raise ValueError('allocations must be a tuple')
    retired_keys: set[str] = set()
    for item in batch.retired:
        if not isinstance(item, tuple) or len(item) != 2:
            raise ValueError('each retired row requires a (key, source reference) pair')
        key, reference = item
        require_label(key, 'retired observation')
        if not isinstance(reference, SourceReference):
            raise ValueError('retired row requires a source reference')
        if key in retired_keys:
            raise ValueError(f'duplicate retired observation: {key}')
        retired_keys.add(key)
    new_keys: set[str] = set()
    for row in batch.new:
        require_label(row.observation, 'new observation')
        require_label(row.station, 'new observation station')
        if row.observation in new_keys:
            raise ValueError(f'duplicate new observation: {row.observation}')
        if row.observation in retired_keys:
            raise ValueError(f'new observation repeats a retired key: {row.observation}')
        new_keys.add(row.observation)
        if row.station != batch.station:
            raise ValueError(f'new observation {row.observation} belongs to another station')
        validate_litres(row.litres)
    validate_source_positions(row.position for row in batch.new)
    checked = tuple(_check_allocation(item) for item in batch.allocations)
    for retired, new, _, _ in checked:
        if retired not in retired_keys:
            raise ValueError(f'allocation retires a key outside the batch: {retired}')
        if new not in new_keys:
            raise ValueError(f'allocation creates a key outside the batch: {new}')
    received: dict[str, int] = {}
    for _, new, _, litres in checked:
        received[new] = received.get(new, 0) + litres
    for row in batch.new:
        if row.litres != received.get(row.observation, 0):
            raise ValueError(f'new row {row.observation} does not receive its full share')


def _origin_key(origin: SourceReference) -> tuple:
    return (origin.day, origin.edition, origin.request_id,
            origin.position.document, origin.position.row)


def allocation_shares(batch) -> dict[str, tuple[tuple[SourceReference, int], ...]]:
    """Declared origin shares given up by each retired key."""
    shares: dict[str, dict[SourceReference, int]] = {}
    for retired, _, origin, litres in batch.allocations:
        bucket = shares.setdefault(retired, {})
        bucket[origin] = bucket.get(origin, 0) + litres
    return {key: tuple(sorted(items.items(), key=lambda pair: _origin_key(pair[0])))
            for key, items in shares.items()}


def received_shares(batch) -> dict[str, tuple[tuple[SourceReference, int], ...]]:
    """Declared origin shares received by each new key."""
    shares: dict[str, dict[SourceReference, int]] = {}
    for _, new, origin, litres in batch.allocations:
        bucket = shares.setdefault(new, {})
        bucket[origin] = bucket.get(origin, 0) + litres
    return {key: tuple(sorted(items.items(), key=lambda pair: _origin_key(pair[0])))
            for key, items in shares.items()}
