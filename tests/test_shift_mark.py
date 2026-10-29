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
        until = window["until"] if window else None
        s = stats.gather_stats([self.data], target or self.day, 0, since, until)
        return sum(v["total"] for v in s.values())

    def plan(self, choice, now):
        return stats.shift_window(stats.shift_state(self.day, now=now), choice)

    def export(self, choice, now):
        """Answer `choice` and export the code at `now`. Returns (window as planned, sessions)."""
        window = self.plan(choice, now)
        n = self.count(window)
        planned = dict(window)  # as it was when the code was built
        if window["record"]:
            self.assertTrue(stats.record_shift_mark(window, now=now))
        return planned, n

    def marks(self):
        return stats.read_shift_marks().get(self.day.isoformat(), {})

    def morning_and_night(self):
        for hh in (8, 10, 14):
            self.session(at(self.day, hh))
        for hh in (17, 20, 22):
            self.session(at(self.day, hh))

    # -- which question is asked --

    def test_questions_follow_the_marks(self):
        def asked(now):
            return [q for q, _ in stats.shift_questions(stats.shift_state(self.day, now=now))]

        self.assertEqual(asked(at(self.day, 15)), ["Is this the end of the morning shift?"])
        self.export("end morning", at(self.day, 15))
        self.assertEqual(asked(at(self.day, 23)), ["Is this the end of the night shift?",
                                                   "Redo the morning shift code?"])
        self.export("end night", at(self.day, 23))
        self.assertEqual(asked(at(self.day, 23, 30)), ["Redo the night shift code?",
                                                       "Redo the morning shift code?"])

    def test_first_yes_wins_and_no_to_all_is_numbers_only(self):
        stats.record_shift_mark(self.plan("end morning", at(self.day, 15)), now=at(self.day, 15))
        state = stats.shift_state(self.day, now=at(self.day, 23))
        self.assertEqual(stats.ask_shift_questions(state, lambda q: True), "end night")
        answers = iter([False, True])
        self.assertEqual(stats.ask_shift_questions(state, lambda q: next(answers)), "redo morning")
        self.assertIsNone(stats.ask_shift_questions(state, lambda q: False))
        self.assertEqual(stats.ask_shift_questions(state, lambda q: None), "cancel")

    # -- the everyday case --

    def test_morning_code_covers_whole_day(self):
        self.morning_and_night()
        window, n = self.export("end morning", at(self.day, 15, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 6)  # no mark yet today: everything dated today counts
        self.assertEqual(self.marks(), {"morning": [None, at(self.day, 15, 15)]})
        self.assertIn("Morning shift -- covers the whole day", stats.shift_window_desc(window))

    def test_night_code_counts_only_after_morning_mark(self):
        self.morning_and_night()
        self.export("end morning", at(self.day, 15, 15))
        window, n = self.export("end night", at(self.day, 23, 15))
        self.assertEqual(n, 3)
        self.assertEqual(self.marks()["night"], [at(self.day, 15, 15), at(self.day, 23, 15)])
        self.assertEqual(stats.shift_window_desc(window),
                         "Night shift -- sessions finished after 15:15")

    def test_night_right_after_morning_is_still_split(self):
        # The grace period used to read this as the morning re-exporting.
        self.session(at(self.day, 14))
        self.export("end morning", at(self.day, 15, 0))
        self.session(at(self.day, 15, 5), operator="first_night_session")
        window, n = self.export("end night", at(self.day, 15, 10))
        self.assertEqual(n, 1)

    def test_handover_session_goes_to_the_shift_that_finished_it(self):
        self.session(at(self.day, 14))
        self.export("end morning", at(self.day, 15, 10))
        self.session(at(self.day, 15, 20), operator="late_morning_operator")
        window, n = self.export("end night", at(self.day, 23, 0))
        self.assertEqual(n, 1)  # the 15:20 session, still under its own operator

    # -- re-making a code --

    def test_redo_morning_before_night_stretches_the_morning(self):
        self.session(at(self.day, 9))
        self.export("end morning", at(self.day, 11, 0))
        self.session(at(self.day, 13))
        window, n = self.export("redo morning", at(self.day, 15, 0))
        self.assertEqual(n, 2)  # hours later, still the whole morning
        self.assertEqual(self.marks(), {"morning": [None, at(self.day, 15, 0)]})
        self.assertIn("(re-export)", stats.shift_window_desc(self.plan("redo morning",
                                                                       at(self.day, 15, 5))))

    def test_redo_night_stretches_the_night(self):
        self.morning_and_night()
        self.export("end morning", at(self.day, 15))
        self.export("end night", at(self.day, 21))
        window, n = self.export("redo night", at(self.day, 23))
        self.assertEqual(n, 3)  # the 22:00 session joins the night
        self.assertEqual(self.marks()["night"], [at(self.day, 15), at(self.day, 23)])
        self.assertEqual(self.marks()["morning"], [None, at(self.day, 15)])

    def test_redo_morning_after_night_remakes_it_and_leaves_marks(self):
        self.morning_and_night()
        self.export("end morning", at(self.day, 15))
        self.export("end night", at(self.day, 23))
        before = self.marks()
        window, n = self.export("redo morning", at(self.day, 23, 30))
        self.assertFalse(window["record"])
        self.assertEqual(window["until"], at(self.day, 15))
        self.assertEqual(n, 3)  # the morning's three, none of the night's
        self.assertEqual(self.marks(), before)
        self.assertIn("by 15:00 (re-made, marks unchanged)", stats.shift_window_desc(window))

    def test_same_code_exported_twice_moves_one_mark(self):
        self.session(at(self.day, 9))
        window = self.plan("end morning", at(self.day, 15, 0))
        stats.record_shift_mark(window, now=at(self.day, 15, 0))
        stats.record_shift_mark(window, now=at(self.day, 15, 2))  # QR, then copy
        self.assertEqual(self.marks(), {"morning": [None, at(self.day, 15, 2)]})
        self.assertTrue(window["redo"])

    # -- numbers only --

    def test_numbers_only_with_no_marks_covers_whole_day_and_warns(self):
        self.morning_and_night()
        window, n = self.export(None, at(self.day, 23))
        self.assertEqual(n, 6)
        self.assertFalse(window["record"])
        self.assertIn("No morning shift code today", window["warning"])
        self.assertEqual(self.marks(), {})

    def test_numbers_only_after_morning_counts_since_it(self):
        self.morning_and_night()
        self.export("end morning", at(self.day, 15))
        window, n = self.export(None, at(self.day, 23))
        self.assertEqual(n, 3)  # 17:00, 20:00 and 22:00, nothing recorded
        self.assertIsNone(window["warning"])
        self.assertNotIn("night", self.marks())
        self.assertTrue(stats.shift_window_desc(window).startswith("Numbers only"))

    # -- days --

    def test_yesterdays_mark_does_not_split_today(self):
        yesterday = self.day - timedelta(days=1)
        state = stats.shift_state(yesterday, now=at(yesterday, 15))
        stats.record_shift_mark(stats.shift_window(state, "end morning"), now=at(yesterday, 15))
        self.session(at(self.day, 9))
        window, n = self.export("end morning", at(self.day, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 1)

    def test_past_day_codes_are_never_split_or_marked(self):
        yesterday = self.day - timedelta(days=1)
        self.assertIsNone(stats.shift_state(yesterday, now=at(self.day, 8)))
        self.assertIsNone(stats.shift_window(None, "end morning"))
        self.assertIsNone(stats.record_shift_mark(None))
        self.assertFalse(os.path.exists(self.mark))

    def test_old_days_are_pruned_on_save(self):
        old = (self.day - timedelta(days=10)).isoformat()
        with open(self.mark, "w") as f:
            json.dump({old: {"morning": [None, 1.0]}}, f)
        self.export("end morning", at(self.day, 15))
        self.assertEqual(list(stats.read_shift_marks()), [self.day.isoformat()])

    def test_reset_forgets_only_today(self):
        yesterday = self.day - timedelta(days=1)
        with open(self.mark, "w") as f:
            json.dump({yesterday.isoformat(): {"morning": [None, at(yesterday, 15)]}}, f)
        self.export("end morning", at(self.day, 15))
        removed = stats.reset_shift_marks(self.day)
        self.assertEqual(removed, {"morning": [None, at(self.day, 15)]})
        self.assertEqual(list(stats.read_shift_marks()), [yesterday.isoformat()])

    # -- failure falls back to the old behaviour --

    def test_corrupt_mark_file_reads_as_no_marks(self):
        with open(self.mark, "w") as f:
            f.write("{not json")
        self.session(at(self.day, 9))
        window, n = self.export("end morning", at(self.day, 15))
        self.assertIsNone(window["since"])
        self.assertEqual(n, 1)
        self.assertEqual(list(self.marks()), ["morning"])

    def test_marks_from_the_grace_period_version_read_as_none(self):
        with open(self.mark, "w") as f:
            json.dump({self.day.isoformat(): [[None, at(self.day, 15)]]}, f)
        self.assertEqual(stats.shift_questions(stats.shift_state(self.day, now=at(self.day, 16))),
                         [("Is this the end of the morning shift?", "end morning")])

    def test_unwritable_mark_returns_none(self):
        os.environ["RF_SHIFT_MARK"] = os.path.join(self.tmp.name, "missing", "dir", "mark")
        window = self.plan("end morning", at(self.day, 15))
        self.assertIsNone(stats.record_shift_mark(window, now=at(self.day, 15)))
        self.assertFalse(window["redo"])


class CommandLineTest(unittest.TestCase):
    """End to end through `stats.py --code`, on the real clock. No terminal is attached,
    so nothing is asked: the answer comes from --end/--redo or not at all."""

    def setUp(self):
        now = datetime.now()
        if now.hour < 3:
            self.skipTest("needs a few hours of today behind the clock")
        self.tmp = tempfile.TemporaryDirectory()
        self.data = os.path.join(self.tmp.name, "captures")
        self.mark = os.path.join(self.tmp.name, "rf-shift-mark")
        self.now = now.timestamp()
        for i, ago in enumerate((2.5, 0.5)):  # one before the morning mark, one after
            folder = os.path.join(self.data, f"s{i}")
            os.makedirs(folder)
            meta = os.path.join(folder, "session_meta.json")
            with open(meta, "w") as f:
                json.dump({"operator_name": f"op{i}"}, f)
            t = self.now - ago * HOUR
            os.utime(meta, (t, t))
        with open(self.mark, "w") as f:
            json.dump({date.today().isoformat(): {"morning": [None, self.now - 2 * HOUR]}}, f)

    def tearDown(self):
        self.tmp.cleanup()

    def run_stats(self, *args):
        env = dict(os.environ, RF_SHIFT_MARK=self.mark, RF_STATION="test-station")
        return subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(HERE), "stats.py"),
             "-d", self.data, *args],
            capture_output=True, text=True, env=env, check=True, stdin=subprocess.DEVNULL)

    def read_mark(self):
        with open(self.mark) as f:
            return f.read()

    def test_end_night_counts_after_the_morning(self):
        out = self.run_stats("--code", "--end", "night", "--no-mark")
        self.assertIn("1 operator(s)", out.stderr)
        self.assertIn("Night shift", out.stderr)
        self.assertTrue(out.stdout.strip().startswith("RF2:"))

    def test_nobody_to_ask_shows_numbers_and_records_nothing(self):
        before = self.read_mark()
        out = self.run_stats("--code")
        self.assertIn("Nobody to ask", out.stderr)
        self.assertIn("Numbers only", out.stderr)
        self.assertEqual(self.read_mark(), before)

    def test_no_mark_leaves_the_file_alone_and_end_records(self):
        before = self.read_mark()
        self.run_stats("--code", "--end", "night", "--no-mark")
        self.assertEqual(self.read_mark(), before)
        self.run_stats("--code", "--end", "night")
        self.assertEqual(set(json.loads(self.read_mark())[date.today().isoformat()]),
                         {"morning", "night"})

    def test_answer_that_does_not_fit_shows_numbers_only(self):
        out = self.run_stats("--code", "--end", "morning")
        self.assertIn("does not fit", out.stderr)
        self.assertIn("Numbers only", out.stderr)

    def test_reset_marks(self):
        out = self.run_stats("--reset-marks")
        self.assertIn("Removed today's morning mark", out.stderr)
        self.assertEqual(json.loads(self.read_mark()), {})


if __name__ == "__main__":
    unittest.main()
