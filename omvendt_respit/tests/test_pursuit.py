from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest

from omvendt_respit import (
    Boat,
    calculate_gph,
    calculate_starts,
    read_participants,
    positive_number,
    write_pdf,
)


def sample_boats():
    return [
        Boat(
            "1",
            "Ægir & Ørn",
            "<Båd>",
            "DEN 1",
            "Stor bane 1",
            D(605),
            (D(800), D(600), D(500)),
        )
    ]


class PursuitTests(unittest.TestCase):
    def test_gph_weighting(self):
        self.assertEqual(calculate_gph(800, 600, 500), D("605"))
        self.assertEqual(calculate_gph("699,4", "575.4", "509.6"), D("577.55"))

    def test_official_distance_example(self):
        boats = [
            Boat(str(i), str(i), "", "", "", D(r))
            for i, r in enumerate([500, 1000, 800])
        ]
        starts = calculate_starts(boats, D(30), datetime(2026, 9, 23, 10))
        self.assertEqual([s.delay for s in starts], [0, 6000, 15000])
        self.assertEqual(
            [s.time.strftime("%H:%M:%S") for s in starts],
            ["10:00:00", "11:40:00", "14:10:00"],
        )

    def test_rounding_ties_and_midnight(self):
        boats = [
            Boat(str(i), str(i), "", "", "", D(r))
            for i, r in enumerate(["800", "799.5", "799.5"])
        ]
        starts = calculate_starts(boats, D(1), datetime(2026, 9, 23, 23, 59, 59))
        self.assertEqual([s.delay for s in starts], [0, 1, 1])
        self.assertEqual(starts[-1].time, datetime(2026, 9, 24))

    def test_invalid_numbers(self):
        for value in ["nan", "Infinity", "0", "-1", "abc", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                positive_number(value)

    def test_pdf_with_special_characters(self):
        boats = sample_boats()
        when = datetime(2026, 9, 23, 18, 30)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "start.pdf"
            write_pdf(path, calculate_starts(boats, D(5), when), when)
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))

    def test_pdf_uses_new_start_time_and_three_columns(self):
        import shutil
        import subprocess
        from omvendt_respit.starts import Start

        if not shutil.which("pdftotext"):
            self.skipTest("pdftotext er ikke installeret")
        boats = sample_boats()
        old = datetime(2026, 9, 23, 18, 30)
        starts = [Start(boats[0], 0, old), Start(boats[0], 90, old)]
        new = datetime(2026, 9, 23, 23, 59)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "start.pdf"
            write_pdf(path, starts, new)
            text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        self.assertIn("00:00:30 (+1 d)", text)
        self.assertNotIn("18:30", text)
        self.assertIn("Starttid", text)
        self.assertIn("Båd/Deltager", text)
        self.assertIn("Bådtype", text)
        self.assertIn("<Båd> / Ægir & Ørn", text)
        self.assertNotIn("GPH", text)
        self.assertNotIn("Distance", text)
        self.assertEqual(starts[0].time, old)

    def test_pdf_start_index_shifts_sorted_list_across_midnight(self):
        import shutil
        import subprocess
        from omvendt_respit.starts import Start

        if not shutil.which("pdftotext"):
            self.skipTest("pdftotext er ikke installeret")
        when = datetime(2026, 9, 23, 0, 1)
        boat = sample_boats()[0]
        starts = [Start(boat, delay, when) for delay in (300, 0, 120, 120)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "indexed.pdf"
            write_pdf(path, starts, when, start_index=3)
            text = subprocess.check_output(["pdftotext", str(path), "-"], text=True)
        self.assertIn("23:59:00 (-1 d)", text)
        self.assertEqual(text.count("00:01:00"), 2)
        self.assertIn("00:04:00", text)
        self.assertEqual([s.delay for s in starts], [300, 0, 120, 120])

    def test_pdf_rejects_invalid_start_index(self):
        when = datetime(2026, 9, 23, 18, 30)
        starts = calculate_starts(sample_boats(), D(5), when)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.pdf"
            for index in (0, -1, 2, 1.5, True):
                with self.subTest(index=index), self.assertRaisesRegex(ValueError, "Startindeks"):
                    write_pdf(path, starts, when, start_index=index)
            self.assertFalse(path.exists())

    def test_cli_passes_start_index_and_interactive_default(self):
        from unittest.mock import patch
        from omvendt_respit.__main__ import main

        for extra, expected in (([], 1), (["--start-index", "5"], 5)):
            with patch("omvendt_respit.__main__.write_pdf") as output:
                self.assertEqual(main([
                    "--distance", "5", "--start", "18:30", "--date", "2026-09-23",
                    *extra,
                ]), 0)
                self.assertEqual(output.call_args.kwargs["start_index"], expected)
        for answer, expected in (("", 1), ("5", 5)):
            with patch("builtins.input", side_effect=[
                "A,D,F", "18:30", answer, "2026-09-23", "", "", "n",
            ]), patch("omvendt_respit.__main__.write_pdf") as output:
                self.assertEqual(main([]), 0)
                self.assertEqual(output.call_args.kwargs["start_index"], expected)


class CourseTests(unittest.TestCase):
    def test_wgs84_equator_distance(self):
        from omvendt_respit.course import Mark, calculate_legs

        course = calculate_legs(
            {"A": Mark("A", 0, 0), "B": Mark("B", 0, 1)}, ["A", "B"]
        )
        self.assertAlmostEqual(
            float(course.distance_nm) * 1852, 111319.490793, places=5
        )

    def test_route_order_and_laps(self):
        from omvendt_respit.course import Mark, calculate_legs

        marks = {"A": Mark("A", 56, 10), "B": Mark("B", 56, 10.1)}
        one = calculate_legs(marks, ["A", "B"])
        two = calculate_legs(marks, ["A", "B", "A"])
        self.assertEqual(two.distance_nm, 2 * one.distance_nm)
        self.assertEqual(len(one.legs), 1)

    def test_invalid_route_and_coordinates(self):
        from omvendt_respit.course import Mark, calculate_legs

        marks = {"A": Mark("A", 56, 10)}
        for route in [[], ["A"], ["A", "B"], ["A", "A"]]:
            with self.subTest(route=route), self.assertRaises(ValueError):
                calculate_legs(marks, route)
        for lat, lon in [(91, 0), (0, 181), (float("nan"), 10)]:
            with self.assertRaises(ValueError):
                Mark("X", lat, lon)

    def test_club_chart_minutes_conversion(self):
        from omvendt_respit.course import read_marks

        path = Path(__file__).resolve().parents[1] / "resources/marks.csv"
        marks = read_marks(path)
        self.assertEqual(set(marks), set("ABCDEFGIJX"))
        self.assertAlmostEqual(marks["J"].latitude, 56 + 14.68 / 60)
        self.assertAlmostEqual(marks["J"].longitude, 10 + 21.05 / 60)
        self.assertAlmostEqual(marks["X"].latitude, 56 + 14.622 / 60)
        self.assertAlmostEqual(marks["X"].longitude, 10 + 20.673 / 60)


class ImplicitCourseTests(unittest.TestCase):
    def test_start_line_and_finish_are_implicit(self):
        from omvendt_respit.course import Mark, calculate_course

        marks = {"J": Mark("J", 0, 0), "X": Mark("X", 0, 2), "A": Mark("A", 1, 1)}
        course = calculate_course(marks, ["A"])
        self.assertAlmostEqual(course.marks[0].longitude, 1)
        self.assertAlmostEqual(course.marks[0].latitude, 0)
        self.assertEqual(course.marks[-1], marks["X"])
        self.assertEqual(course.rounding_marks, (marks["A"],))
        self.assertEqual(len(course.legs), 2)

    def test_projection_is_not_midpoint(self):
        from omvendt_respit.course import Mark, calculate_course

        marks = {"J": Mark("J", 0, 0), "X": Mark("X", 0, 2), "A": Mark("A", 1, 0.4)}
        course = calculate_course(marks, ["A"])
        self.assertAlmostEqual(course.marks[0].longitude, 0.4, places=6)
        self.assertAlmostEqual(course.marks[0].latitude, 0, places=6)

    def test_projection_outside_both_ends(self):
        from omvendt_respit.course import Mark, calculate_course

        for longitude, expected in [(-1, "J"), (3, "X")]:
            with self.subTest(longitude=longitude):
                marks = {
                    "J": Mark("J", 0, 0),
                    "X": Mark("X", 0, 2),
                    "A": Mark("A", 1, longitude),
                }
                course = calculate_course(marks, ["A"])
                self.assertEqual(course.marks[0], marks[expected])
                self.assertEqual(course.marks[-1], marks["X"])

    def test_missing_x_is_not_guessed(self):
        from omvendt_respit.course import Mark, calculate_course

        with self.assertRaisesRegex(ValueError, "koordinater til: X"):
            calculate_course(
                {"J": Mark("J", 56, 10), "A": Mark("A", 56.1, 10.1)}, ["A"]
            )

    def test_rounding_marks_can_repeat(self):
        from omvendt_respit.course import Mark, calculate_course

        marks = {
            "J": Mark("J", 0, 0),
            "X": Mark("X", 0, 2),
            "A": Mark("A", 1, 1),
            "B": Mark("B", 2, 1),
        }
        course = calculate_course(marks, ["A", "B", "A"])
        self.assertEqual([m.name for m in course.rounding_marks], ["A", "B", "A"])
        self.assertEqual(len(course.legs), 4)


class RoundingTests(unittest.TestCase):
    def course(self, coordinates):
        from omvendt_respit.course import Mark, calculate_legs

        marks = {str(i): Mark(str(i), *point) for i, point in enumerate(coordinates)}
        return calculate_legs(marks, list(marks))

    def test_left_and_right_turns(self):
        from omvendt_respit.course import rounding_sides

        self.assertEqual(
            rounding_sides(self.course([(0, 0), (1, 0), (1, 1)])), ("starboard",)
        )
        self.assertEqual(
            rounding_sides(self.course([(0, 0), (1, 0), (1, -1)])), ("port",)
        )

    def test_180_keeps_previous_direction(self):
        from omvendt_respit.course import rounding_sides

        for longitude, expected in [(1, "starboard"), (-1, "port")]:
            with self.subTest(expected=expected):
                course = self.course(
                    [(0, 0), (1, 0), (1, longitude), (1, 0), (1, longitude)]
                )
                self.assertEqual(rounding_sides(course), (expected, expected, expected))

    def test_direction_can_change_on_next_ordinary_turn(self):
        from omvendt_respit.course import rounding_sides

        course = self.course([(0, 0), (1, 0), (1, 1), (2, 1)])
        self.assertEqual(rounding_sides(course), ("starboard", "port"))

    def test_initial_180_defaults_to_port(self):
        from omvendt_respit.course import rounding_sides

        course = self.course([(0, 0), (1, 0), (0, 0)])
        self.assertEqual(rounding_sides(course), ("port",))
        self.assertEqual(rounding_sides(course, "port"), ("port",))
        self.assertEqual(rounding_sides(course, "starboard"), ("starboard",))


class ParticipantCSVTests(unittest.TestCase):
    header = "competitor,boat_name,sail_number,series,TACIL,TACIM,TACIH,source\n"
    row = "Ægir & Ørn,<Båd>,DEN 1,Stor bane 1,800,600,500,Manuelt\n"

    def read(self, text, excluded=None):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "boats.csv"
            path.write_text(text, encoding="utf-8-sig")
            return read_participants(path, excluded)

    def test_csv_values_are_authoritative(self):
        boats = self.read(self.header + self.row)
        self.assertEqual(boats[0].rating, D(605))
        self.assertEqual(boats[0].name, "<Båd>")
        updated = self.read(self.header + self.row.replace(",800,", ",900,"))
        self.assertEqual(updated[0].rating, D(620))

    def test_invalid_or_duplicate_rows(self):
        for text in [
            self.header,
            self.header + self.row * 2,
            self.header + self.row.replace(",800,", ",nan,"),
            self.header + "Name,Boat\n",
            "competitor\nName\n",
        ]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.read(text)

    def test_exclusion(self):
        text = self.header + self.row + self.row.replace("Ægir & Ørn", "Second")
        self.assertEqual(len(self.read(text, ["Second"])), 1)
        with self.assertRaisesRegex(ValueError, "Ukendte"):
            self.read(text, ["Typo"])

    def test_complete_saved_fleet(self):
        path = Path(__file__).resolve().parents[1] / "resources/participants.csv"
        boats = read_participants(path)
        self.assertEqual(len(boats), 31)
        boat = next(b for b in boats if b.competitor == "Harpelunde Senior")
        self.assertEqual(boat.components, (D(953), D("735.2"), D("648.1")))
        self.assertEqual(boat.rating, D("746.095"))
        self.assertEqual(boat.boat_type, "Hansen Spidsgatter")

    def test_cli_works_with_network_blocked(self):
        from unittest.mock import patch
        from omvendt_respit.__main__ import main

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "offline.pdf"
            with patch(
                "socket.socket", side_effect=AssertionError("Netværk er ikke tilladt")
            ):
                result = main(
                    [
                        "--route",
                        "A,D,F",
                        "--date",
                        "2026-09-23",
                        "--start",
                        "18:30",
                        "--output-pdf",
                        str(path),
                    ]
                )
            self.assertEqual(result, 0)
            self.assertTrue(path.read_bytes().startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
