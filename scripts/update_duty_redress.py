from __future__ import annotations

import argparse
import csv
from pathlib import Path


DEFAULT_YEAR = 2026
DEFAULT_NOTE = "Dommertjans"
DEFAULT_REGISTRY_PATH = Path("participant_registry_2026.csv")
DEFAULT_DUTY_PATH = Path("redress_duty_assignments.csv")
SERIES_TO_GROUP = {
    "Stor bane 1": "Stor Bane",
    "Stor bane 2": "Stor Bane",
    "Lille bane 1": "Lille Bane",
    "Lille bane 2": "Lille Bane",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Update redress_duty_assignments.csv from race label and participant number.",
    )
    parser.add_argument("--race", required=True, help="Race label, for example R3.")
    parser.add_argument("--participant-number", required=True, type=int, help="Participant number from participant_registry_2026.csv.")
    parser.add_argument("--year", default=DEFAULT_YEAR, type=int, help="Target year. Defaults to 2026.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY_PATH), help="Participant registry CSV path.")
    parser.add_argument("--duty-file", default=str(DEFAULT_DUTY_PATH), help="Duty redress CSV path.")
    parser.add_argument("--note", default=DEFAULT_NOTE, help="Optional note text.")
    parser.add_argument("--github-output", default="", help="Optional GITHUB_OUTPUT path for workflow outputs.")
    return parser


def _normalize_race_label(value: str) -> str:
    text = str(value or "").strip().upper()
    if not text.startswith("R"):
        text = f"R{text}"
    if len(text) < 2 or not text[1:].isdigit():
        raise ValueError(f"Invalid race label: {value}")
    return text


def _load_registry(path: Path) -> dict[int, dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Participant registry not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    required_columns = {"participant_number", "series", "competitor"}
    if not rows and required_columns:
        header = set()
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            header_row = next(reader, [])
            header = set(header_row)
        missing = sorted(required_columns.difference(header))
        if missing:
            raise ValueError(f"Participant registry is missing columns: {', '.join(missing)}")

    registry: dict[int, dict[str, str]] = {}
    for row in rows:
        number_text = str(row.get("participant_number") or "").strip()
        if not number_text:
            continue
        try:
            number = int(number_text)
        except ValueError as exc:
            raise ValueError(f"Invalid participant_number in registry: {number_text}") from exc

        if number in registry:
            raise ValueError(f"Duplicate participant_number in registry: {number}")

        series = str(row.get("series") or "").strip()
        competitor = str(row.get("competitor") or "").strip()
        if not series or not competitor:
            raise ValueError(f"Registry row for participant {number} is incomplete.")

        group = SERIES_TO_GROUP.get(series)
        if not group:
            raise ValueError(f"Unsupported series in registry: {series}")

        registry[number] = {
            "series": series,
            "group": group,
            "competitor": competitor,
        }

    return registry


def _load_duty_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Duty redress CSV not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [
            {
                "year": str(row.get("year") or "").strip(),
                "race_local": str(row.get("race_local") or "").strip(),
                "group": str(row.get("group") or "").strip(),
                "series": str(row.get("series") or "").strip(),
                "competitor": str(row.get("competitor") or "").strip(),
                "note": str(row.get("note") or "").strip(),
            }
            for row in reader
        ]
    return rows


def _write_duty_rows(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["year", "race_local", "group", "series", "competitor", "note"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sort_key(row: dict[str, str]) -> tuple[int, int, str, str, str]:
    year = int(str(row.get("year") or "0"))
    race_local = str(row.get("race_local") or "").strip().upper()
    race_num = int(race_local[1:]) if race_local.startswith("R") and race_local[1:].isdigit() else 0
    return (
        year,
        race_num,
        str(row.get("group") or ""),
        str(row.get("series") or ""),
        str(row.get("competitor") or ""),
    )


def run(args: argparse.Namespace) -> int:
    race_label = _normalize_race_label(args.race)
    registry_path = Path(args.registry)
    duty_path = Path(args.duty_file)
    rows = _load_duty_rows(duty_path)
    target_year = str(int(args.year))
    participant_number = int(args.participant_number)
    github_output = Path(args.github_output) if str(args.github_output or "").strip() else None

    if participant_number == 0:
        remaining_rows = [
            row
            for row in rows
            if not (
                str(row.get("year") or "") == target_year
                and str(row.get("race_local") or "").strip().upper() == race_label
            )
        ]
        removed_count = len(rows) - len(remaining_rows)
        remaining_rows = sorted(remaining_rows, key=_sort_key)
        _write_duty_rows(duty_path, remaining_rows)

        action = "removed" if removed_count > 0 else "noop"
        if github_output is not None:
            with github_output.open("a", encoding="utf-8") as handle:
                handle.write(f"action={action}\n")
                handle.write(f"year={target_year}\n")
                handle.write(f"race={race_label}\n")
                handle.write("participant_number=0\n")
                handle.write("competitor=\n")
                handle.write("series=\n")
                handle.write("group=\n")
                handle.write(f"removed_count={removed_count}\n")

        print(f"{action}: year={target_year} race={race_label} removed_count={removed_count}")
        return 0

    registry = _load_registry(registry_path)
    participant = registry.get(participant_number)
    if participant is None:
        raise ValueError(f"Participant number {participant_number} was not found in {registry_path}.")

    target_row = {
        "year": target_year,
        "race_local": race_label,
        "group": participant["group"],
        "series": participant["series"],
        "competitor": participant["competitor"],
        "note": str(args.note or DEFAULT_NOTE).strip() or DEFAULT_NOTE,
    }

    deduped = [
        row
        for row in rows
        if not (
            str(row.get("year") or "") == target_year
            and str(row.get("race_local") or "").strip().upper() == race_label
        )
    ]
    removed_count = len(rows) - len(deduped)
    deduped.append(target_row)

    deduped = sorted(deduped, key=_sort_key)
    _write_duty_rows(duty_path, deduped)

    action = "updated" if removed_count > 0 else "added"
    if github_output is not None:
        with github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"action={action}\n")
            handle.write(f"year={target_row['year']}\n")
            handle.write(f"race={target_row['race_local']}\n")
            handle.write(f"participant_number={participant_number}\n")
            handle.write(f"competitor={target_row['competitor']}\n")
            handle.write(f"series={target_row['series']}\n")
            handle.write(f"group={target_row['group']}\n")
            handle.write(f"removed_count={removed_count}\n")

    print(
        f"{action}: year={target_row['year']} race={target_row['race_local']} participant={participant_number} "
        f"name={target_row['competitor']} series={target_row['series']} group={target_row['group']}"
    )
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()