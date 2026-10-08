"""Fake station data for trying the shift marks by hand -- sandbox only, not for rf-stats.

    python3 tools/make_fake.py morning DIR   # a morning shift's sessions + two from yesterday
    python3 tools/make_fake.py night   DIR   # night sessions finished after the morning code
    python3 tools/make_fake.py marks         # today's marks, in readable times

DIR gets the layout the "local" scope reads (DIR/captures, DIR/upload): run stats.py
with HOME pointing at the folder that holds Workspace/roboforce-core-apps.

The mark file is whatever $RF_SHIFT_MARK points at -- never the real ~/.rf-shift-mark.
"""
import csv
import json
import os
import sys
import time
from datetime import date, datetime

MIN = 60


def write_session(folder, ended, operator, task, event, minutes, score=None):
    os.makedirs(folder)
    with open(os.path.join(folder, "realsense_log.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["frame", "monotonic_ns"])
        w.writerow([0, 0])
        w.writerow([1, int(minutes * 60 * 1e9)])
    if score is not None:
        with open(os.path.join(folder, "session_score.json"), "w") as f:
            json.dump({"score": score}, f)
    meta = os.path.join(folder, "session_meta.json")
    with open(meta, "w") as f:
        json.dump({"operator_name": operator, "task_type": task, "event_object": event}, f)
    # session_meta.json's mtime is when the recording ended -- the only time stats.py reads.
    os.utime(meta, (ended, ended))


def mark_file():
    path = os.environ.get("RF_SHIFT_MARK")
    if not path:
        sys.exit("Set RF_SHIFT_MARK first, so the real ~/.rf-shift-mark is never touched.")
    return path


def todays_marks():
    try:
        with open(mark_file()) as f:
            return json.load(f).get(date.today().isoformat(), {})
    except (OSError, ValueError, AttributeError):
        return {}


def morning(root):
    if os.path.exists(root):
        sys.exit(f"{root} already exists -- delete it first, so old fake sessions do not mix in.")
    if datetime.now().hour < 8:
        sys.exit("Run this after 08:00 -- the fake morning needs a few hours of today behind it.")
    mark_file()
    now = time.time()
    cap = os.path.join(root, "captures")
    write_session(os.path.join(cap, "morning_001"), now - 7 * 60 * MIN, "Alice", "pick", ["E10"], 4.0, 0.9)
    write_session(os.path.join(cap, "morning_002"), now - 6 * 60 * MIN, "Alice", "pick", ["E10"], 3.5, 0.8)
    write_session(os.path.join(cap, "morning_003"), now - 5 * 60 * MIN, "Bob", "place", ["E12"], 5.0)
    write_session(os.path.join(cap, "morning_004"), now - 4 * 60 * MIN, "bob", "place", None, 2.5, 0.7)
    # One parent with teleop + two inference runs: only the latest inference counts.
    inf = os.path.join(cap, "morning_inf")
    write_session(os.path.join(inf, "teleop_1"), now - 220 * MIN, "Alice", "pick", ["E10"], 2.0)
    write_session(os.path.join(inf, "inference_1"), now - 210 * MIN, "Alice", "pick", ["E10"], 1.0)
    write_session(os.path.join(inf, "inference_2"), now - 200 * MIN, "Alice", "pick", ["E10"], 1.5)
    # Yesterday, already moved to upload -- must never show up in today's code.
    y = now - 24 * 60 * MIN
    up = os.path.join(root, "upload")
    write_session(os.path.join(up, "old_001"), y, "Alice", "pick", ["E10"], 4.0)
    write_session(os.path.join(up, "old_002"), y + 10 * MIN, "Erin", "pick", ["E10"], 4.0)
    print("Morning shift: 5 sessions today (Alice 3, Bob 2 -- 'bob' joins the Bob row), "
          "and 2 from yesterday.")


def night(root):
    marks = todays_marks()
    if "morning" not in marks:
        sys.exit("No morning mark today -- export the morning code first (y, then p or c).")
    exported = marks["morning"][1]
    now = time.time()
    # The night's sessions finish between the morning code and now. Spread them over
    # that gap, however short it was, so they all land after the mark.
    step = (now - exported) / 5
    cap = os.path.join(root, "captures")
    # Carol's first run finished a minute before the morning code: it stays a morning session.
    write_session(os.path.join(cap, "handover_001"), exported - MIN, "Carol", "pick", ["E11"], 3.0)
    write_session(os.path.join(cap, "night_001"), exported + step, "Carol", "pick", ["E11"], 3.0, 0.95)
    write_session(os.path.join(cap, "night_002"), exported + 2 * step, "Carol", "pick", ["E11"], 3.2)
    corr = os.path.join(cap, "night_corr")
    write_session(os.path.join(corr, "inference_1"), exported + 3 * step, "Dave", "fix", None, 1.0)
    write_session(os.path.join(corr, "inference_2"), exported + 4 * step, "Dave", "fix", None, 1.2)
    print(f"Night shift: Carol 2 and Dave 2 finished after the morning code "
          f"({datetime.fromtimestamp(exported):%H:%M:%S}); one Carol run just before it.")


def show_marks():
    marks = todays_marks()
    if not marks:
        print("No shift marks today.")
    for shift in ("morning", "night"):
        if shift in marks:
            start, end = marks[shift]
            start = "start of day" if start is None else f"{datetime.fromtimestamp(start):%H:%M:%S}"
            print(f"  {shift:8} {start} -> {datetime.fromtimestamp(end):%H:%M:%S}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "marks":
        show_marks()
    elif len(sys.argv) == 3 and sys.argv[1] in ("morning", "night"):
        {"morning": morning, "night": night}[sys.argv[1]](sys.argv[2])
    else:
        sys.exit(__doc__)
