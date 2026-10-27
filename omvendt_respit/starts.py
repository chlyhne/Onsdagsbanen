"""Modul 2: GPH og starttider som funktion af banelængden i sømil."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


@dataclass(frozen=True)
class Boat:
    identity: str
    competitor: str
    name: str
    sail_number: str
    series: str
    rating: Decimal
    components: tuple[Decimal, Decimal, Decimal] | None = None
    boat_type: str = ""


@dataclass(frozen=True)
class Start:
    boat: Boat
    delay: int
    time: datetime


def positive_number(value: object) -> Decimal:
    try:
        number = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise ValueError(f"Ugyldigt tal: {value}") from None
    if not number.is_finite() or number <= 0:
        raise ValueError(f"Tallet skal være positivt og endeligt: {value}")
    return number


def calculate_gph(tacil: object, tacim: object, tacih: object) -> Decimal:
    """DS §1.4.1: 3 parts light, 12 medium and 5 heavy wind.

    Preserve the exact weighted mean until rounding the final start offset.
    """
    light, medium, heavy = map(positive_number, (tacil, tacim, tacih))
    return (3 * light + 12 * medium + 5 * heavy) / 20


def read_participants(path: Path, excluded: list[str] | None = None) -> list[Boat]:
    """Read the authoritative local CSV; no network lookup or fallback."""
    excluded = set(excluded or [])
    boats = []
    names = set()
    required = {
        "competitor",
        "boat_name",
        "sail_number",
        "series",
        "TACIL",
        "TACIM",
        "TACIH",
    }
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not required <= set(reader.fieldnames or []):
            raise ValueError(
                "Deltager-CSV mangler kolonner: "
                + ", ".join(sorted(required - set(reader.fieldnames or [])))
            )
        for row in reader:
            if None in row or any(row[key] is None for key in required):
                raise ValueError(
                    f"Forkert antal felter på CSV-linje {reader.line_num}."
                )
            name = row["competitor"].strip()
            if not name or name in names:
                raise ValueError(
                    f"Tomt eller gentaget deltagernavn på linje {reader.line_num}: {name}"
                )
            names.add(name)
            if name in excluded:
                continue
            try:
                components = tuple(
                    positive_number(row[key]) for key in ("TACIL", "TACIM", "TACIH")
                )
            except ValueError as exc:
                raise ValueError(f"{name}, CSV-linje {reader.line_num}: {exc}") from exc
            boats.append(
                Boat(
                    name,
                    name,
                    row["boat_name"].strip(),
                    row["sail_number"].strip(),
                    row["series"].strip(),
                    calculate_gph(*components),
                    components,
                    boat_type=(row.get("boat_type") or "").strip(),
                )
            )
    if excluded - names:
        raise ValueError(
            "Ukendte deltagernavne: " + ", ".join(sorted(excluded - names))
        )
    if not boats:
        raise ValueError("Ingen deltagere valgt.")
    return boats


def calculate_starts(
    boats: list[Boat], distance: Decimal, first_start: datetime
) -> list[Start]:
    distance = positive_number(distance)
    if not boats:
        raise ValueError("Vælg mindst én båd.")
    slowest = max(positive_number(b.rating) for b in boats)
    starts = []
    for boat in boats:
        delay = int(
            ((slowest - boat.rating) * distance).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        starts.append(Start(boat, delay, first_start + timedelta(seconds=delay)))
    return sorted(
        starts, key=lambda s: (s.delay, s.boat.sail_number, s.boat.competitor)
    )
