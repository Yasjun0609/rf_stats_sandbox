#!/usr/bin/env bash
# Walk through the shift marks on fake data -- sandbox only, not for rf-stats.
#
#     bash tools/demo.sh
#
# Everything lives in /tmp/rf-demo (or $RF_DEMO_DIR): fake sessions, the station name,
# the mark file. The real ~/.rf-station and ~/.rf-shift-mark are never touched.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEMO="${RF_DEMO_DIR:-/tmp/rf-demo}"
case "$DEMO" in */rf-demo) ;; *) echo "RF_DEMO_DIR must end in /rf-demo"; exit 1 ;; esac
DATA="$DEMO/Workspace/roboforce-core-apps"
export RF_SHIFT_MARK="$DEMO/test-mark"
export RF_STATION=DEMO

stats() { HOME="$DEMO" python3 "$REPO/stats.py" "$@"; }
fake() { python3 "$REPO/tools/make_fake.py" "$@"; }

step() {
    echo
    echo "== $1"
    shift
    for line in "$@"; do echo "   $line"; done
    echo
    read -r -p "   Press Enter to continue (Ctrl+C to stop)... " _
}

step "Start clean" \
     "Deletes $DEMO from any earlier run and makes a fresh fake station."
rm -rf -- "$DEMO"
mkdir -p "$DEMO"
fake morning "$DATA"
fake marks

step "1. The morning shift leaves" \
     "In the menu: Today, then 'Everything from local'." \
     "The table shows the whole day: Alice 3, Bob 2." \
     "Press c. The question is: Is this the end of the morning shift?  Press y." \
     "The code says 'Morning shift -- covers the whole day so far'." \
     "Press p (QR) or c (copy) -- that is when the mark is saved. Then q, q to quit."
stats
echo "   Marks now:"
fake marks

step "2. Hours later, the night shift has recorded" \
     "Adds Carol and Dave's night sessions, finished after the morning code."
fake night "$DATA"

step "3. The night shift leaves" \
     "Today, 'Everything from local'. The table still shows the whole day." \
     "Press c. The question is: Is this the end of the night shift?  Press y." \
     "The code says 'Night shift -- sessions finished after HH:MM', 2 operator(s):" \
     "Carol and Dave only. Press p or c, then q, q."
stats
echo "   Marks now:"
fake marks

step "4. Re-making the morning code after the night" \
     "Today, 'Everything from local', c." \
     "Redo the night shift code?  n.   Redo the morning shift code?  y." \
     "The code says 'Morning shift -- sessions finished by HH:MM (re-made, marks unchanged)'," \
     "3 operator(s): Alice, Bob, and Carol's run that finished just before the morning code." \
     "Press p to see it still works, then q, q -- the marks below do not move."
stats
echo "   Marks now:"
fake marks

step "5. Just looking" \
     "Today, 'Everything from local', c, then n to both questions." \
     "The code says 'Numbers only, no shift mark' -- 0 operator(s), since nothing has" \
     "finished after the night code -- and nothing is recorded. q, q."
stats

step "6. Yesterday's code is never split and asks nothing" \
     "Runs: stats.py -d local --code -t 1"
stats -d local --code -t 1

step "7. Clean up" \
     "Deletes $DEMO."
rm -rf -- "$DEMO"
echo "   Done."
