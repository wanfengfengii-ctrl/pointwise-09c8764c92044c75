from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from warehouse import CorrectionBatch, MembershipInput, ObservationInput, OpeningBatch, SourcePosition, ValueCorrection, encode_edition, open_district, open_source, receive_edition, release_edition


DAY = '2026-02-03'
EAST = 'east'
WEST = 'west'
STATION = 'bend'


def opening(*, litres: int = 7, second: int | None = None, day: str = DAY) -> OpeningBatch:
    observations = [ObservationInput('a', STATION, litres, SourcePosition('morning.csv', 1))]
    if second is not None:
        observations.append(ObservationInput('b', STATION, second, SourcePosition('morning.csv', 2)))
    return OpeningBatch(day, 'morning', (EAST, WEST), tuple(observations),
                        (MembershipInput(STATION, EAST, SourcePosition('stations.csv', 1)),))


def correction(
    request: str = 'fix', *, litres: int | None = 11, district: str | None = WEST, day: str = DAY
) -> CorrectionBatch:
    observations = () if litres is None else (
        ValueCorrection('a', litres, SourcePosition(f'{request}-observations.csv', 4)),
    )
    memberships = () if district is None else (
        MembershipInput(STATION, district, SourcePosition(f'{request}-stations.csv', 3)),
    )
    return CorrectionBatch(day, request, observations, memberships)


class Interrupted(RuntimeError):
    pass


def interrupt() -> None:
    raise Interrupted('operator stopped the transaction before commit')


class WarehouseCase(unittest.TestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.open_source()

    def open_source(self):
        db = open_source(self.root / 'source.sqlite')
        self.addCleanup(db.close)
        return db

    def open_district(self, district):
        db = open_district(self.root / f'{district}.sqlite', district)
        self.addCleanup(db.close)
        return db

    def material(self, district, edition, day=DAY):
        return release_edition(self.source, day, district, edition)

    def send(self, db, district, edition, day=DAY, **kwargs):
        return receive_edition(db, encode_edition(self.material(district, edition, day)), **kwargs)

    def freeze(self, last):
        return {(district, number): encode_edition(self.material(district, number))
                for number in range(1, last + 1) for district in (EAST, WEST)}
