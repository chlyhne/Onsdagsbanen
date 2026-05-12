from __future__ import annotations

import argparse
from datetime import datetime
from itertools import combinations
import re
from pathlib import Path

import pandas as pd

from .combine import BAYESIAN_POINT_RULE
from .combine import FRACTIONAL_POINT_RULE
from .combine import HIGH_POINT_RULE
from .combine import LOW_POINT_RULE
from .combine import combine_overall_from_races
from .combine import combine_races
from .defaults import DEFAULT_CLASS_GROUPS
from .defaults import DEFAULT_EVENT_URL
from .parser import parse_available_race_labels_from_result_payload
from .parser import parse_completed_race_labels_from_result_payload
from .parser import parse_discard_after_races_from_result_payload
from .parser import parse_race_rows_from_result_payload
from .pdf_report import build_combined_pdf
from .redress_duty import DEFAULT_DUTY_ASSIGNMENTS_PATH
from .redress_duty import DutyApplicationContext
from .redress_duty import apply_group_duty_assignments
from .redress_duty import duty_assignment_years
from .redress_duty import filter_relevant_duty_assignments
from .redress_duty import load_duty_assignments
from .scraper import fetch_class_results_batch


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch Manage2Sail class results via API, combine standings, and generate a PDF report.",
    )
    parser.add_argument("--event-url", default=DEFAULT_EVENT_URL, help="Manage2Sail event root URL.")
    parser.add_argument(
        "--class-names",
        action="append",
        help=(
            "Comma-separated class names for one combined group. "
            "Repeat --class-names for multiple groups."
        ),
    )
    parser.add_argument(
        "--max-race",
        type=int,
        action="append",
        help=(
            "Maximum race number cap. Provide once to apply to all groups, "
            "or provide once per --class-names group in the same order."
        ),
    )
    parser.add_argument(
        "--output-pdf",
        default="Results.pdf",
        help="Output PDF filename.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Folder for output PDF files.",
    )
    parser.add_argument(
        "--scoring-rule",
        default=LOW_POINT_RULE,
        choices=[LOW_POINT_RULE, HIGH_POINT_RULE, FRACTIONAL_POINT_RULE, BAYESIAN_POINT_RULE],
        help=(
            "Scoring rule. "
            f"'{LOW_POINT_RULE}' keeps the current low-point system; "
            f"'{HIGH_POINT_RULE}' gives 1 point for participation plus one per boat left behind; "
            f"'{FRACTIONAL_POINT_RULE}' is high-point normalized by race participants, then multiplied by 100 and rounded; "
            f"'{BAYESIAN_POINT_RULE}' applies order-invariant, time-based hierarchical Bayesian scoring with handicap baseline (no discards)."
        ),
    )
    parser.add_argument(
        "--duty-assignments",
        default="",
        help=(
            "Optional CSV file with judge-duty redress assignments. "
            "If omitted, the CLI automatically uses redress_duty_assignments.csv when present."
        ),
    )
    return parser


def _group_label_from_class(class_name: str) -> str:
    label = re.sub(r"\s+\d+$", "", class_name).strip()
    if not label:
        return class_name.strip()
    return " ".join(part.capitalize() for part in label.split())


def _class_groups_from_args(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    if not args.class_names:
        return DEFAULT_CLASS_GROUPS

    groups: list[tuple[str, list[str]]] = []
    for group_index, spec in enumerate(args.class_names, start=1):
        class_names = [name.strip() for name in str(spec).split(",") if name.strip()]
        if len(class_names) < 2:
            raise ValueError(
                "Each --class-names value must contain at least 2 class names, separated by commas."
            )

        normalized_labels = {_group_label_from_class(name) for name in class_names}
        group_label = normalized_labels.pop() if len(normalized_labels) == 1 else f"Group {group_index}"
        groups.append((group_label, class_names))

    return groups


def _roster_from_payload(payload: dict[str, object]) -> set[str]:
    roster: set[str] = set()
    entries = payload.get("EntryResults")
    if not isinstance(entries, list):
        return roster

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        competitor = str(entry.get("TeamName") or "").strip()
        if competitor:
            roster.add(competitor)
    return roster


def _resolve_group_max_races(max_race_values: list[int] | None, group_count: int) -> list[int | None]:
    values = list(max_race_values or [])
    if not values:
        return [None] * group_count

    for value in values:
        if value < 1:
            raise ValueError("--max-race must be >= 1.")

    if len(values) == 1:
        return [values[0]] * group_count

    if len(values) == group_count:
        return values

    raise ValueError(
        "Provide --max-race either once (all groups) or once per --class-names group in the same order."
    )


def _resolve_duty_assignments_path(args: argparse.Namespace) -> tuple[Path, bool]:
    configured_path = str(getattr(args, "duty_assignments", "") or "").strip()
    if configured_path:
        return Path(configured_path), True
    return DEFAULT_DUTY_ASSIGNMENTS_PATH, False


def _event_year_from_payloads(payload_by_class: dict[str, dict[str, object]], max_race: int | None) -> int:
    years: set[int] = set()
    for payload in payload_by_class.values():
        race_meta = _race_meta_from_payload(payload, max_race=max_race)
        for race_info in race_meta.values():
            race_date_text = _format_race_date_value(race_info.get("start_date"), race_info.get("start_date_with_time"))
            if race_date_text == "ukendt":
                continue
            try:
                years.add(int(race_date_text[-4:]))
            except ValueError:
                continue

    if not years:
        raise ValueError("Could not infer event year from Manage2Sail payload metadata.")
    if len(years) != 1:
        raise ValueError(f"Expected one event year, but found: {sorted(years)}")
    return next(iter(years))


def _race_meta_from_payload(payload: dict[str, object], max_race: int | None) -> dict[int, dict[str, object]]:
    meta_by_race: dict[int, dict[str, object]] = {}
    race_infos = payload.get("RaceInfos")
    if not isinstance(race_infos, list):
        return meta_by_race

    for item in race_infos:
        if not isinstance(item, dict):
            continue

        race_index = item.get("RaceIndex")
        if not isinstance(race_index, int) or race_index < 1 or (max_race is not None and race_index > max_race):
            continue

        length_nm_value = item.get("LengthNM")
        length_nm = float(length_nm_value) if isinstance(length_nm_value, (int, float)) else None

        start_time_value = item.get("StartTime")
        start_seconds = int(start_time_value) if isinstance(start_time_value, (int, float)) else None

        start_text = str(item.get("StartTimeText") or "").strip()
        start_date = str(item.get("StartDate") or "").strip()
        start_date_with_time = str(item.get("StartDateWithTime") or "").strip()
        wind_speed_type_value = item.get("WindSpeedType")
        wind_speed_type = int(wind_speed_type_value) if isinstance(wind_speed_type_value, (int, float)) else None
        meta_by_race[race_index] = {
            "length_nm": length_nm,
            "start_seconds": start_seconds,
            "start_text": start_text,
            "start_date": start_date,
            "start_date_with_time": start_date_with_time,
            "wind_speed_type": wind_speed_type,
        }

    return meta_by_race


def _format_race_labels(race_indices: list[int]) -> str:
    if not race_indices:
        return ""
    labels = [f"R{race_index}" for race_index in race_indices]
    if len(labels) <= 6:
        return ", ".join(labels)
    return f"{', '.join(labels[:6])}, ..."


def _format_length_value(value: object) -> str:
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return f"{text} nm"
    return "ukendt"


def _format_start_value(start_seconds: object, start_text: object) -> str:
    text = str(start_text or "").strip()
    if text:
        return text
    if isinstance(start_seconds, int):
        hours = (start_seconds // 3600) % 24
        minutes = (start_seconds % 3600) // 60
        seconds = start_seconds % 60
        if seconds:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{hours:02d}:{minutes:02d}"
    return "ukendt"


def _format_race_date_value(start_date: object, start_date_with_time: object) -> str:
    start_date_text = str(start_date or "").strip()
    if start_date_text:
        normalized = start_date_text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed.strftime("%d-%m-%Y")
        except ValueError:
            pass

    start_date_with_time_text = str(start_date_with_time or "").strip()
    if start_date_with_time_text:
        for pattern in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                parsed = datetime.strptime(start_date_with_time_text, pattern)
                return parsed.strftime("%d-%m-%Y")
            except ValueError:
                continue

    return "ukendt"


def _format_wind_category_da(wind_speed_type: object) -> str:
    if not isinstance(wind_speed_type, int):
        return "ukendt"

    wind_map = {
        0: "lav",
        1: "mellem",
        2: "høj",
    }
    return wind_map.get(wind_speed_type, f"kategori {wind_speed_type}")


def _single_or_varies(values: list[str], unknown_text: str = "ukendt") -> str:
    normalized = [value.strip() for value in values if value.strip()]
    unique_values = sorted(set(normalized))
    if not unique_values:
        return unknown_text
    if len(unique_values) == 1:
        return unique_values[0]
    return "varierer mellem klasser"


def _race_page_meta_by_label(
    class_names: list[str],
    payload_by_class: dict[str, dict[str, object]],
    race_labels: list[str],
    max_race: int | None,
) -> dict[str, dict[str, str]]:
    meta_by_class = {
        class_name: _race_meta_from_payload(payload_by_class[class_name], max_race=max_race)
        for class_name in class_names
    }

    race_meta: dict[str, dict[str, str]] = {}
    for race_label in race_labels:
        race_index = int(race_label[1:])

        length_values: list[str] = []
        start_values: list[str] = []
        date_values: list[str] = []
        wind_values: list[str] = []

        for class_name in class_names:
            race_info = meta_by_class[class_name].get(race_index, {})
            length_values.append(_format_length_value(race_info.get("length_nm")))
            start_values.append(_format_start_value(race_info.get("start_seconds"), race_info.get("start_text")))
            date_values.append(_format_race_date_value(race_info.get("start_date"), race_info.get("start_date_with_time")))
            wind_values.append(_format_wind_category_da(race_info.get("wind_speed_type")))

        race_meta[race_label] = {
            "wind_category_da": _single_or_varies(wind_values),
            "course_length": _single_or_varies(length_values),
            "start_time": _single_or_varies(start_values),
            "race_date": _single_or_varies(date_values),
        }

    return race_meta


def _warn_if_group_not_meaningfully_combinable(
    group_label: str,
    class_names: list[str],
    payload_by_class: dict[str, dict[str, object]],
    max_race: int | None,
) -> dict[str, list[str]]:
    race_meta_by_class = {
        class_name: _race_meta_from_payload(payload_by_class[class_name], max_race=max_race)
        for class_name in class_names
    }
    race_warnings: dict[str, list[str]] = {}

    race_count_by_class: dict[str, int] = {}
    for class_name in class_names:
        race_meta = race_meta_by_class[class_name]
        if race_meta:
            race_count_by_class[class_name] = len(race_meta)
        else:
            race_count_by_class[class_name] = len(
                parse_available_race_labels_from_result_payload(
                    payload_by_class[class_name],
                    max_race=max_race,
                )
            )

    for left_class, right_class in combinations(class_names, 2):
        left_count = race_count_by_class[left_class]
        right_count = race_count_by_class[right_class]
        if left_count != right_count:
            print(
                f"[{group_label}] WARNING: '{left_class}' and '{right_class}' have different race counts "
                f"({left_count} vs {right_count})."
            )

        left_meta = race_meta_by_class[left_class]
        right_meta = race_meta_by_class[right_class]
        if not left_meta or not right_meta:
            continue

        common_race_indices = sorted(set(left_meta).intersection(right_meta))
        if not common_race_indices:
            print(
                f"[{group_label}] WARNING: '{left_class}' and '{right_class}' have no overlapping race indices."
            )
            continue

        length_mismatches: list[int] = []
        start_mismatches: list[int] = []
        race_issues: dict[int, set[str]] = {}

        for race_index in common_race_indices:
            left_race = left_meta[race_index]
            right_race = right_meta[race_index]

            left_length = left_race.get("length_nm")
            right_length = right_race.get("length_nm")
            if isinstance(left_length, float) and isinstance(right_length, float):
                if abs(left_length - right_length) > 1e-6:
                    length_mismatches.append(race_index)
                    race_issues.setdefault(race_index, set()).add("length")

            left_start_seconds = left_race.get("start_seconds")
            right_start_seconds = right_race.get("start_seconds")
            if isinstance(left_start_seconds, int) and isinstance(right_start_seconds, int):
                if left_start_seconds != right_start_seconds:
                    start_mismatches.append(race_index)
                    race_issues.setdefault(race_index, set()).add("start")
            else:
                left_start_text = str(left_race.get("start_text") or "").strip()
                right_start_text = str(right_race.get("start_text") or "").strip()
                if left_start_text and right_start_text and left_start_text != right_start_text:
                    start_mismatches.append(race_index)
                    race_issues.setdefault(race_index, set()).add("start")

        if length_mismatches:
            print(
                f"[{group_label}] WARNING: '{left_class}' and '{right_class}' have different race lengths for "
                f"{_format_race_labels(length_mismatches)}."
            )
        if start_mismatches:
            print(
                f"[{group_label}] WARNING: '{left_class}' and '{right_class}' have different start times for "
                f"{_format_race_labels(start_mismatches)}."
            )
        for race_index, issues in race_issues.items():
            race_label = f"R{race_index}"

            left_race = left_meta.get(race_index, {})
            right_race = right_meta.get(race_index, {})
            left_length_text = _format_length_value(left_race.get("length_nm"))
            right_length_text = _format_length_value(right_race.get("length_nm"))
            left_start_text = _format_start_value(
                left_race.get("start_seconds"),
                left_race.get("start_text"),
            )
            right_start_text = _format_start_value(
                right_race.get("start_seconds"),
                right_race.get("start_text"),
            )

            if "length" in issues and "start" in issues:
                message = (
                    f"OBS: '{left_class}' og '{right_class}' har forskellig banelængde "
                    f"({left_length_text} vs {right_length_text}) og starttid "
                    f"({left_start_text} vs {right_start_text}) i denne sejlads."
                )
            elif "length" in issues:
                message = (
                    f"OBS: '{left_class}' og '{right_class}' har forskellig banelængde "
                    f"({left_length_text} vs {right_length_text}) i denne sejlads."
                )
            else:
                message = (
                    f"OBS: '{left_class}' og '{right_class}' har forskellig starttid "
                    f"({left_start_text} vs {right_start_text}) i denne sejlads."
                )
            race_warnings.setdefault(race_label, []).append(message)

    deduped_warnings: dict[str, list[str]] = {}
    for race_label, messages in race_warnings.items():
        seen: set[str] = set()
        deduped: list[str] = []
        for message in messages:
            if message not in seen:
                seen.add(message)
                deduped.append(message)
        if deduped:
            deduped_warnings[race_label] = deduped

    return deduped_warnings


def run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    duty_assignments_path, duty_assignments_required = _resolve_duty_assignments_path(args)
    duty_assignments = load_duty_assignments(duty_assignments_path, required=duty_assignments_required)
    redress_lookup: pd.DataFrame | None = None

    pdf_sections: list[dict[str, object]] = []
    source_descriptions: list[str] = []

    class_groups = _class_groups_from_args(args)
    group_max_races = _resolve_group_max_races(args.max_race, len(class_groups))
    all_class_names = [name for _, class_names in class_groups for name in class_names]
    unique_class_names = [name for name in dict.fromkeys(all_class_names) if name]

    class_payloads = fetch_class_results_batch(
        args.event_url,
        class_names=unique_class_names,
        timeout_ms=90000,
        max_workers=max(4, len(unique_class_names)),
    )

    for (group_label, class_names), requested_max_race in zip(class_groups, group_max_races):
        if len(class_names) < 2:
            raise ValueError(f"Group '{group_label}' must contain at least 2 classes.")

        payload_by_class: dict[str, dict[str, object]] = {}
        for class_name in class_names:
            payload = class_payloads.get(class_name)
            if payload is None:
                raise ValueError(f"No payload fetched for class '{class_name}'.")
            payload_by_class[class_name] = payload

        available_races_by_class = {
            class_name: parse_available_race_labels_from_result_payload(payload_by_class[class_name], max_race=None)
            for class_name in class_names
        }

        aligned_races_all = sorted(
            set.intersection(*(set(available_races_by_class[class_name]) for class_name in class_names)),
            key=lambda label: int(label[1:]),
        )

        if not aligned_races_all:
            print(f"[{group_label}] No aligned race labels found; skipping group.")
            continue

        group_max_available = max(int(label[1:]) for label in aligned_races_all)
        if requested_max_race is not None and requested_max_race > group_max_available:
            raise ValueError(
                f"[{group_label}] --max-race {requested_max_race} exceeds this group's maximum available race {group_max_available}."
            )

        effective_max_race = requested_max_race if requested_max_race is not None else group_max_available
        event_year = _event_year_from_payloads(payload_by_class, max_race=effective_max_race)

        race_warnings = _warn_if_group_not_meaningfully_combinable(
            group_label=group_label,
            class_names=class_names,
            payload_by_class=payload_by_class,
            max_race=effective_max_race,
        )

        aligned_races = [label for label in aligned_races_all if int(label[1:]) <= effective_max_race]

        completed_common = set.intersection(
            *(set(parse_completed_race_labels_from_result_payload(payload_by_class[class_name], max_race=effective_max_race)) for class_name in class_names)
        )
        selected_races = [label for label in aligned_races if label in completed_common]

        if not selected_races:
            print(f"[{group_label}] No completed aligned races found; skipping group.")
            continue

        race_page_meta = _race_page_meta_by_label(
            class_names=class_names,
            payload_by_class=payload_by_class,
            race_labels=selected_races,
            max_race=effective_max_race,
        )

        print(f"[{group_label}] Aligned race labels: {', '.join(selected_races)}")

        parsed_races_by_class = {
            class_name: parse_race_rows_from_result_payload(
                payload_by_class[class_name],
                class_name,
                selected_races,
            )
            for class_name in class_names
        }

        if not duty_assignments.empty:
            group_duties = filter_relevant_duty_assignments(
                duty_assignments,
                event_year=event_year,
                group_label=group_label,
                selected_races=selected_races,
            )
            if not group_duties.empty:
                if redress_lookup is None:
                    from .redress.pipeline import build_redress_lookup

                    lookup_years = duty_assignment_years(duty_assignments)
                    redress_lookup = build_redress_lookup(years=lookup_years)
                duty_result = apply_group_duty_assignments(
                    DutyApplicationContext(
                        group_label=group_label,
                        class_names=class_names,
                        payload_by_class=payload_by_class,
                        selected_races=selected_races,
                        event_year=event_year,
                    ),
                    parsed_races_by_class,
                    duty_assignments,
                    redress_lookup,
                )
                parsed_races_by_class = duty_result.parsed_races_by_class
                applied_duty_count = duty_result.applied_count
                if applied_duty_count:
                    print(f"[{group_label}] Applied {applied_duty_count} redress duty assignment(s).")

        completed_races: list[str] = []
        group_races = []
        for race_label in selected_races:
            if not all(race_label in parsed_races_by_class[class_name] for class_name in class_names):
                continue
            completed_races.append(race_label)
            for class_name in class_names:
                group_races.append(parsed_races_by_class[class_name][race_label])

        if not group_races:
            print(f"[{group_label}] No completed aligned races found; skipping group.")
            continue

        print(f"[{group_label}] Completed races used for scoring: {', '.join(completed_races)}")

        group_roster = set().union(*(_roster_from_payload(payload_by_class[class_name]) for class_name in class_names))

        if args.scoring_rule == BAYESIAN_POINT_RULE:
            group_discard_after = []
            print(f"[{group_label}] Bayesian scoring selected: discards disabled.")
        else:
            group_discard_candidates = [
                parse_discard_after_races_from_result_payload(payload_by_class[class_name], max_race=effective_max_race)
                for class_name in class_names
            ]
            group_discard_candidates = [candidate for candidate in group_discard_candidates if candidate]
            group_discard_after = max(group_discard_candidates, key=len) if group_discard_candidates else []
            if group_discard_after:
                print(f"[{group_label}] Discards after races: {', '.join(str(value) for value in group_discard_after)}")

        combined_race_scores = combine_races(group_races, scoring_rule=args.scoring_rule)
        combined_overall = combine_overall_from_races(
            combined_race_scores,
            max_race=effective_max_race,
            all_competitors=sorted(group_roster) if group_roster else None,
            discard_after=group_discard_after,
            scoring_rule=args.scoring_rule,
        )

        pdf_sections.append(
            {
                "group_label": group_label,
                "combined_races": combined_race_scores,
                "combined_overall": combined_overall,
                "race_page_meta": race_page_meta,
                "race_warnings": {
                    race_label: race_warnings[race_label]
                    for race_label in completed_races
                    if race_label in race_warnings
                },
            }
        )

        for class_name in class_names:
            source_descriptions.append(f"{args.event_url} [{group_label} / {class_name}]")

    if not pdf_sections:
        raise RuntimeError("No groups produced completed aligned races.")

    output_pdf = output_dir / args.output_pdf
    build_combined_pdf(output_pdf, pdf_sections, source_descriptions, scoring_rule=args.scoring_rule)

    print(f"PDF created: {output_pdf}")

    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
