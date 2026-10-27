"""Kommandolinje, der forbinder de tre respitmoduler."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

from .course import calculate_course, read_marks
from .starts import calculate_starts, positive_number, read_participants
from .pdf import write_pdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Lav PDF med omvendte respitstarttider for Onsdagsbanens deltagere."
    )
    course_options = parser.add_mutually_exclusive_group()
    course_options.add_argument(
        "--distance", help="Banelængde i sømil uden krydstillæg (alternativ til mærker)"
    )
    course_options.add_argument(
        "--route",
        help="Rundingsmærker i rækkefølge, fx A,D,F; start J–X og mål X er implicit",
    )
    parser.add_argument(
        "--marks-csv",
        type=Path,
        default=Path(__file__).parent / "resources/marks.csv",
        help="Mærkepositioner; standard: Kaløvig-banekortet fra 2024",
    )
    parser.add_argument(
        "--first-rounding",
        choices=["port", "starboard"],
        default="port",
        help="Retning hvis første mærke giver 180°: port=bagbord, starboard=styrbord",
    )
    parser.add_argument("--start", help="Starttid for båden ved --start-index, HH:MM eller HH:MM:SS")
    parser.add_argument(
        "--start-index", type=int, default=1,
        help="Bådens nummer i den sorterede startliste (fra 1; standard: 1)",
    )
    parser.add_argument("--date", help="Sejladsdato, ÅÅÅÅ-MM-DD")
    parser.add_argument(
        "--participants-csv",
        type=Path,
        default=Path(__file__).parent / "resources/participants.csv",
        help="Deltagere og TACIL/TACIM/TACIH i en lokal CSV",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Fravælg deltager med præcist navn; kan gentages",
    )
    parser.add_argument(
        "--separate-groups",
        action="store_true",
        help="Separate felter for Stor og Lille Bane, med samme distance og første start",
    )
    parser.add_argument(
        "--output-pdf", type=Path, default=Path("output/startlister/Starttider.pdf")
    )
    parser.add_argument("--title", default="Onsdagsbane Afslutningssejlads")
    args = parser.parse_args(argv)
    interactive = not (sys.argv[1:] if argv is None else argv)
    try:
        if interactive:
            args.route = input(
                "Rundingsmærker i rækkefølge (fx A,D,F; start og mål er implicit): "
            ).strip()
            args.start = input("Starttid (HH:MM[:SS]): ").strip()
            args.start_index = int(input("Bådens nummer i startlisten [1]: ").strip() or "1")
            args.date = input("Sejladsdato (ÅÅÅÅ-MM-DD): ").strip()
            csv_path = input("Deltager-CSV (Enter = standardfil): ").strip()
            if csv_path:
                args.participants_csv = Path(csv_path)
            args.exclude = [
                name.strip()
                for name in input(
                    "Fravælg deltagere (navne adskilt med semikolon; Enter = alle): "
                ).split(";")
                if name.strip()
            ]
            choice = (
                input("Separate felter for Stor og Lille Bane? (j/n) [n]: ")
                .strip()
                .lower()
            )
            if choice not in ("", "j", "n"):
                raise ValueError("Svar j eller n.")
            args.separate_groups = choice == "j"
        if not all((args.distance or args.route, args.start, args.date)):
            raise ValueError("Angiv --route (eller --distance), --start og --date.")
        course = None
        if args.route:
            route = [name.strip() for name in args.route.split(",")]
            default_marks = Path(__file__).parent / "resources/marks.csv"
            marks_source = (
                "https://kblsail.dk/wp-content/uploads/2024/09/banekort-og-waypoints.pdf; "
                "X: brugeroplyst 56°14.622'N 10°20.673'E"
                if args.marks_csv.resolve() == default_marks.resolve()
                else args.marks_csv.name
            )
            course = calculate_course(
                read_marks(args.marks_csv), route, source=marks_source
            )
            distance = course.distance_nm
            print(f"Bane: {' → '.join(route)} · {distance:.4f} sømil")
        else:
            distance = positive_number(args.distance)
        clock = datetime.strptime(
            args.start, "%H:%M:%S" if args.start.count(":") == 2 else "%H:%M"
        ).time()
        first_start = datetime.combine(
            datetime.strptime(args.date, "%Y-%m-%d").date(), clock
        )
        boats = read_participants(args.participants_csv, args.exclude)
        print(f"Indlæst {len(boats)} deltagere fra {args.participants_csv}")
        class_groups = [
            ("Stor Bane", ["Stor bane 1", "Stor bane 2"]),
            ("Lille Bane", ["Lille bane 1", "Lille bane 2"]),
        ]
        if args.separate_groups:
            known = {name for _, names in class_groups for name in names}
            unknown = {boat.series for boat in boats} - known
            if unknown:
                raise ValueError(
                    "Ukendte klasser til separate felter: " + ", ".join(sorted(unknown))
                )
        selections = (
            [
                (label, [b for b in boats if b.series in names])
                for label, names in class_groups
                if any(b.series in names for b in boats)
            ]
            if args.separate_groups
            else [("Samlet felt", boats)]
        )
        groups = [
            (label, calculate_starts(selected, distance, first_start))
            for label, selected in selections
        ]
        write_pdf(
            args.output_pdf,
            [start for _, starts in groups for start in starts],
            first_start,
            start_index=args.start_index,
            title=args.title,
            course=course,
            initial_side=args.first_rounding,
            distance_nm=distance,
        )
        print(f"PDF oprettet: {args.output_pdf.resolve()} ({len(boats)} både)")
        print("Respitgrundlag: GPH = (3 × TACIL + 12 × TACIM + 5 × TACIH) / 20")
        return 0
    except (ValueError, OSError, OverflowError, EOFError) as exc:
        parser.exit(2, f"Fejl: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
