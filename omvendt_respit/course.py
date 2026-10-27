"""Modul 1: Banelængde fra mærkernes WGS84-positioner og rækkefølge."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal
import math
from pathlib import Path
from collections.abc import Mapping, Sequence

from geographiclib.geodesic import Geodesic


@dataclass(frozen=True)
class Mark:
    name: str
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Mærket skal have et navn.")
        if not math.isfinite(self.latitude) or not -90 <= self.latitude <= 90:
            raise ValueError(f"{self.name}: breddegrad skal være mellem -90 og 90.")
        if not math.isfinite(self.longitude) or not -180 <= self.longitude <= 180:
            raise ValueError(f"{self.name}: længdegrad skal være mellem -180 og 180.")


@dataclass(frozen=True)
class Leg:
    start: Mark
    end: Mark
    distance_nm: Decimal


@dataclass(frozen=True)
class Course:
    marks: tuple[Mark, ...]
    legs: tuple[Leg, ...]
    source: str = ""
    rounding_marks: tuple[Mark, ...] = ()
    start_line: tuple[Mark, Mark] | None = None

    @property
    def distance_nm(self) -> Decimal:
        return sum((leg.distance_nm for leg in self.legs), Decimal(0))


def read_marks(path: Path) -> dict[str, Mark]:
    """CSV: name,latitude,longitude; positioner i decimalgrader, nord/øst positive."""
    marks = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"name", "latitude", "longitude"} <= set(reader.fieldnames or []):
            raise ValueError("Mærke-CSV skal have kolonnerne name,latitude,longitude.")
        for row in reader:
            try:
                name = row["name"].strip()
                mark = Mark(
                    name,
                    float(row["latitude"].replace(",", ".")),
                    float(row["longitude"].replace(",", ".")),
                )
            except (AttributeError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"Ugyldigt mærke på CSV-linje {reader.line_num}: {exc}"
                ) from exc
            if name in marks:
                raise ValueError(f"Mærket {name} findes flere gange.")
            marks[name] = mark
    if not marks:
        raise ValueError("Mærkelisten er tom.")
    return marks


def calculate_legs(
    marks: Mapping[str, Mark], route: Sequence[str], *, source: str = ""
) -> Course:
    """Sum of WGS84 geodesic legs / 1852; no tacking or rounding allowance.

    First and last entries are the start and finish positions. Repeated marks
    allow laps. The route is never implicitly closed or reordered.
    """
    if len(route) < 2:
        raise ValueError("Banen skal indeholde mindst et start- og et målpunkt.")
    unknown = set(route) - marks.keys()
    if unknown:
        raise ValueError("Ukendte mærker: " + ", ".join(sorted(unknown)))
    selected = tuple(marks[name] for name in route)
    legs = []
    for start, end in zip(selected, selected[1:]):
        meters = Geodesic.WGS84.Inverse(
            start.latitude, start.longitude, end.latitude, end.longitude
        )["s12"]
        if meters <= 0:
            raise ValueError(f"Strækningen {start.name} → {end.name} har længde 0.")
        legs.append(Leg(start, end, Decimal(str(meters)) / Decimal(1852)))
    return Course(selected, tuple(legs), source)


def project_onto_start_line(j: Mark, x: Mark, target: Mark) -> Mark:
    """Closest WGS84 point on finite J–X, clamped to either endpoint.

    Golden-section minimization reduces the interval along the geodesic
    segment to below 0.1 mm. Endpoints are compared explicitly.
    """
    inverse = Geodesic.WGS84.Inverse(j.latitude, j.longitude, x.latitude, x.longitude)
    length = inverse["s12"]
    if length <= 0:
        raise ValueError("Startlinjens mærker J og X skal have forskellige positioner.")
    line = Geodesic.WGS84.Line(j.latitude, j.longitude, inverse["azi1"])

    def position(distance: float) -> Mark:
        point = line.Position(distance)
        return Mark("Start (projektion på J–X)", point["lat2"], point["lon2"])

    def distance_to_target(point: Mark) -> float:
        return Geodesic.WGS84.Inverse(
            point.latitude, point.longitude, target.latitude, target.longitude
        )["s12"]

    ratio = (math.sqrt(5) - 1) / 2
    left, right = 0.0, length
    lower = right - ratio * (right - left)
    upper = left + ratio * (right - left)
    lower_distance = distance_to_target(position(lower))
    upper_distance = distance_to_target(position(upper))
    for _ in range(100):
        if right - left <= 0.0001:
            break
        if lower_distance <= upper_distance:
            right, upper, upper_distance = upper, lower, lower_distance
            lower = right - ratio * (right - left)
            lower_distance = distance_to_target(position(lower))
        else:
            left, lower, lower_distance = lower, upper, upper_distance
            upper = left + ratio * (right - left)
            upper_distance = distance_to_target(position(upper))
    projected = position((left + right) / 2)
    return min((j, x, projected), key=distance_to_target)


def calculate_course(
    marks: Mapping[str, Mark], route: Sequence[str], *, source: str = ""
) -> Course:
    """Rounding marks only; shortest first leg from segment J–X, finish at X."""
    if not route:
        raise ValueError("Angiv mindst ét rundingsmærke.")
    missing = {"J", "X"} - marks.keys()
    if missing:
        raise ValueError(
            "Startlinjen J–X kræver koordinater til: " + ", ".join(sorted(missing))
        )
    unknown = set(route) - marks.keys()
    if unknown:
        raise ValueError("Ukendte mærker: " + ", ".join(sorted(unknown)))
    j, x = marks["J"], marks["X"]
    start = project_onto_start_line(j, x, marks[route[0]])
    selected = (start, *(marks[name] for name in route), x)
    # Use unique temporary keys so the same rounding mark can occur repeatedly.
    points = {str(i): mark for i, mark in enumerate(selected)}
    geometry = calculate_legs(points, list(points), source=source)
    return Course(
        geometry.marks,
        geometry.legs,
        source,
        tuple(marks[name] for name in route),
        (j, x),
    )


def rounding_sides(course: Course, initial_side: str = "port") -> tuple[str, ...]:
    """Port/red for left turns, starboard/green for right turns under 180°.

    At 180° (or straight ahead), preserve the previous rounding direction.
    If the first turn is ambiguous, default to port (red).
    Incoming azi2 is the forward bearing at the mark, not the reverse bearing.
    """
    if initial_side not in ("port", "starboard"):
        raise ValueError("Rundingsretning skal være port eller starboard.")
    previous = initial_side
    sides = []
    for before, mark, after in zip(course.marks, course.marks[1:], course.marks[2:]):
        incoming = Geodesic.WGS84.Inverse(
            before.latitude, before.longitude, mark.latitude, mark.longitude
        )
        outgoing = Geodesic.WGS84.Inverse(
            mark.latitude, mark.longitude, after.latitude, after.longitude
        )
        turn = (outgoing["azi1"] - incoming["azi2"] + 180) % 360 - 180
        ambiguous = math.isclose(
            abs(turn), 180, abs_tol=1e-7, rel_tol=0
        ) or math.isclose(turn, 0, abs_tol=1e-7, rel_tol=0)
        if ambiguous:
            side = previous
        else:
            side = "starboard" if turn > 0 else "port"
        sides.append(side)
        previous = side
    return tuple(sides)
