"""Shift-mark tests on fake sessions -- no station, no real data, no real mark file.

Each fake session is a folder holding only a session_meta.json whose mtime is set to
when that recording "ended". Every operator name is unique, so the operator count a
code carries is also its session count.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, time as dtime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import stats  # noqa: E402

HOUR = 3600


def at(day, hh, mm=0):
    """Epoch seconds for day at hh:mm local time."""
    return datetime.combine(day, dtime(hh, mm)).timestamp()


class ShiftMarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "captures")
        os.makedirs(self.data)
        self.mark = os.path.join(self.tmp.name, "rf-shift-mark")
        os.environ["RF_SHIFT_MARK"] = self.mark
        self.day = date(2026, 9, 22)
        self.n = 0

    def tearDown(self):
        os.environ.pop("RF_SHIFT_MARK", None)
        self.tmp.cleanup()

    def session(self, ended_at, operator=None):
        """A fake session that finished at `ended_at` (epoch seconds)."""
        self.n += 1
        folder = os.path.join(self.data, f"session_{self.n:03d}")
        os.makedirs(folder)
        meta = os.path.join(folder, "session_meta.json")
        with open(meta, "w") as f:
            json.dump({"operator_name": operator or f"op{self.n}", "task_type": "pick"}, f)
        os.utime(meta, (ended_at, ended_at))

    def count(self, window, target=None):
        since = window["since"] if window else None
        s = stats.gather_stats([self.data], target or self.day, 0, since)
        return sum(v["total"] for v in s.values())

    def export(self, now, target=None):
        """Plan a code as if exported at `now`, record the mark, return (window, sessions)."""
        window = stats.shift_window(target or self.day, now=now)
        n = self.count(window, target)
        planned = dict(window) if window else None  # as it was when the code was built
        stats.record_shift_mark(window, now=now)
        return planned, n

    def day_and_night(self):
        for hh in (8, 10, 14):
            self.session(at(self.day, hh))
        for hh in (17, 20, 22):
            self.session(at(self.day, hh))

    # -- the everyday case --

    def test_first_code_covers_whole_day(self):
        self.day_and_night()
        window, n = self.export(at(self.day, 15, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 6)  # no mark yet today: everything dated today counts

    def test_night_code_counts_only_after_day_mark(self):
        self.day_and_night()
        stats.record_shift_mark(stats.shift_window(self.day, now=at(self.day, 15, 15)),
                                now=at(self.day, 15, 15))
        window, n = self.export(at(self.day, 23, 15))
        self.assertEqual(window["index"], 1)
        self.assertFalse(window["redo"])
        self.assertEqual(n, 3)
        self.assertIn("Night shift", stats.shift_window_desc(window))

    def test_handover_session_goes_to_the_shift_that_finished_it(self):
        self.session(at(self.day, 14))
        stats.record_shift_mark(stats.shift_window(self.day, now=at(self.day, 15, 10)),
                                now=at(self.day, 15, 10))
        self.session(at(self.day, 15, 20), operator="late_day_operator")
        window, n = self.export(at(self.day, 23, 0))
        self.assertEqual(n, 1)  # the 15:20 session, still under its own operator

    # -- exporting twice --

    def test_quick_reexport_is_a_redo_of_the_same_window(self):
        self.session(at(self.day, 9))
        self.export(at(self.day, 15, 0))
        self.session(at(self.day, 15, 10))  # one more session before leaving
        window, n = self.export(at(self.day, 15, 30))
        self.assertTrue(window["redo"])
        self.assertIsNone(window["since"])
        self.assertEqual(n, 2)
        self.assertEqual(len(stats.read_shift_marks()[self.day.isoformat()]), 1)

    def test_same_plan_exported_twice_stretches_instead_of_splitting(self):
        self.session(at(self.day, 9))
        window = stats.shift_window(self.day, now=at(self.day, 15, 0))
        stats.record_shift_mark(window, now=at(self.day, 15, 0))
        stats.record_shift_mark(window, now=at(self.day, 15, 2))  # QR, then copy
        self.assertEqual(stats.read_shift_marks()[self.day.isoformat()],
                         [[None, at(self.day, 15, 2)]])

    def test_reexport_after_grace_opens_a_new_window(self):
        # The known limit: the same shift exporting again hours later gets only the
        # sessions since its first export. The window line on screen shows it.
        self.session(at(self.day, 9))
        self.export(at(self.day, 11, 0))
        self.session(at(self.day, 13))
        window, n = self.export(at(self.day, 15, 0))
        self.assertFalse(window["redo"])
        self.assertEqual(n, 1)
        self.assertIn("after 11:00", stats.shift_window_desc(window))

    # -- days --

    def test_yesterdays_mark_does_not_split_today(self):
        yesterday = self.day - timedelta(days=1)
        stats.record_shift_mark(stats.shift_window(yesterday, now=at(yesterday, 15)),
                                now=at(yesterday, 15))
        self.session(at(self.day, 9))
        window, n = self.export(at(self.day, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 1)

    def test_past_day_codes_are_never_split_or_marked(self):
        yesterday = self.day - timedelta(days=1)
        self.assertIsNone(stats.shift_window(yesterday, now=at(self.day, 8)))
        self.assertIsNone(stats.record_shift_mark(None))
        self.assertFalse(os.path.exists(self.mark))

    def test_old_days_are_pruned_on_save(self):
        old = (self.day - timedelta(days=10)).isoformat()
        with open(self.mark, "w") as f:
            json.dump({old: [[None, 1.0]]}, f)
        self.export(at(self.day, 15))
        self.assertEqual(list(stats.read_shift_marks()), [self.day.isoformat()])

    # -- failure falls back to the old behaviour --

    def test_corrupt_mark_file_reads_as_no_marks(self):
        with open(self.mark, "w") as f:
            f.write("{not json")
        self.session(at(self.day, 9))
        window, n = self.export(at(self.day, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 1)
        self.assertEqual(len(stats.read_shift_marks()[self.day.isoformat()]), 1)

    def test_unwritable_mark_returns_none(self):
        os.environ["RF_SHIFT_MARK"] = os.path.join(self.tmp.name, "missing", "dir", "mark")
        window = stats.shift_window(self.day, now=at(self.day, 15))
        self.assertIsNone(stats.record_shift_mark(window, now=at(self.day, 15)))


class CommandLineTest(unittest.TestCase):
    """End to end through `stats.py --code`, on the real clock."""

    def setUp(self):
        now = datetime.now()
        if now.hour < 3:
            self.skipTest("needs a few hours of today behind the clock")
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "captures")
        self.mark = os.path.join(self.tmp.name, "rf-shift-mark")
        self.now = now.timestamp()
        for i, ago in enumerate((2.5, 0.5)):  # one before the mark, one after
            folder = os.path.join(self.data, f"s{i}")
            os.makedirs(folder)
            meta = os.path.join(folder, "session_meta.json")
            with open(meta, "w") as f:
                json.dump({"operator_name": f"op{i}"}, f)
            t = self.now - ago * HOUR
            os.utime(meta, (t, t))
        with open(self.mark, "w") as f:
            json.dump({date.today().isoformat(): [[None, self.now - 2 * HOUR]]}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def run_code(self, *extra):
        env = dict(os.environ, RF_SHIFT_MARK=self.mark, RF_STATION="test-station")
        return subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(HERE), "stats.py"),
             "--code", "-d", self.data, *extra],
            capture_output=True, text=True, env=env, check=True)

    def test_code_after_mark_is_night_shift(self):
        out = self.run_code("--no-mark")
        self.assertIn("1 operator(s)", out.stderr)
        self.assertIn("Night shift", out.stderr)
        self.assertTrue(out.stdout.strip().startswith("RF2:"))

    def test_no_mark_leaves_the_file_alone_and_export_records(self):
        with open(self.mark) as f:
            before = f.read()
        self.run_code("--no-mark")
        with open(self.mark) as f:
            self.assertEqual(f.read(), before)
        self.run_code()
        with open(self.mark) as f:
            self.assertEqual(len(json.load(f)[date.today().isoformat()]), 2)


if __name__ == "__main__":
    unittest.main()
