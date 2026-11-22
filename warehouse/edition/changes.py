from dataclasses import dataclass

from warehouse.intake.provenance import SourceReference, require_label
from warehouse.intake.validate import validate_litres
from warehouse.ledger.projection import ProjectedObservation


@dataclass(frozen=True)
class DistrictChange:
    action: str
    observation: str
    station: str
    litres: int
    source: SourceReference

    def __post_init__(self) -> None:
        if self.action not in ('exit', 'enter'):
            raise ValueError('entry direction must be exit or enter')
        require_label(self.observation, 'observation')
        require_label(self.station, 'station')
        validate_litres(self.litres)
        if not isinstance(self.source, SourceReference):
            raise ValueError('entry requires a source reference')


def _entry(action: str, row: ProjectedObservation) -> DistrictChange:
    return DistrictChange(action, row.observation, row.station, row.litres, row.source)


def build_district_changes(
    before: tuple[ProjectedObservation, ...], after: tuple[ProjectedObservation, ...]
) -> tuple[DistrictChange, ...]:
    old = {row.observation: row for row in before}
    new = {row.observation: row for row in after}
    changes = []
    for observation in sorted(old.keys() | new.keys()):
        previous = old.get(observation)
        current = new.get(observation)
        if previous == current:
            continue
        if previous is not None:
            changes.append(_entry('exit', previous))
        if current is not None:
            changes.append(_entry('enter', current))
    return tuple(changes)
