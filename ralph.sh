#!/usr/bin/env bash
# A will. The request does not change. The loop repeats it until it is done.
#
#   ./ralph.sh "request"
#   ./ralph.sh --check "python3 test_x.py" "request"
#   ./ralph.sh -n 20 "request"     # optional fuse; without -n it does not stop early
#
# Done means the check exits 0. With no --check, done means `team.py do` exits 0
# (files written, parsed, imported, smoke-tested). That proves it runs, not that
# it is right. The request is passed through unchanged every round; progress is
# whatever the last round left on disk.
#
# Exit: 0 done, 1 the -n fuse blew, 2 bad arguments.
# Env: RALPH_TEAM overrides the command used for team.py (tests use a stub).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM="${RALPH_TEAM:-$HERE/team.py}"
MAX=0
CHECK=""
ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK="${2:?--check needs a command}"; shift 2 ;;
    -n|--max-rounds) MAX="${2:?-n needs a number}"; shift 2 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

[ "${#ARGS[@]}" -gt 0 ] || { echo "ralph: no request given" >&2; exit 2; }
case "$MAX" in *[!0-9]*) echo "ralph: -n must be a whole number" >&2; exit 2 ;; esac

LAST=$(( ${#ARGS[@]} - 1 ))
REQUEST="${ARGS[$LAST]}"
unset 'ARGS[$LAST]'
DO_FLAGS=("${ARGS[@]+"${ARGS[@]}"}")

LOG="ralph.log"
OUT="$(mktemp)"
trap 'rm -f "$OUT"' EXIT
: >"$LOG"
if [ "$MAX" -gt 0 ]; then echo "ralph: until done, fuse at $MAX" | tee -a "$LOG"
else echo "ralph: until done" | tee -a "$LOG"; fi

round=0
while true; do
  round=$((round + 1))
  if [ "$MAX" -gt 0 ] && [ "$round" -gt "$MAX" ]; then
    echo "ralph: fuse ($MAX) reached, still not done" | tee -a "$LOG"
    exit 1
  fi
  echo "== ralph round $round ==" | tee -a "$LOG"

  if [ -n "$CHECK" ]; then
    # Same deadline as the integration gate, and the same killer: a check
    # that never returns is a failed round, and its process group dies with it.
    if python3 -c '
import sys
sys.path.insert(0, sys.argv[1])
import codegen
ok, text = codegen.run_command(sys.argv[2], ".", timeout=600)
if ok:
    pages = codegen.page_console_errors(".")
    if pages:
        ok = False
        text = pages
open(sys.argv[3], "w").write(text)
sys.exit(0 if ok else 1)
' "$HERE" "$CHECK" "$OUT"; then
      rm -f .ralph/last-check.txt
      cat "$OUT" >>"$LOG"
      echo "ralph: done after $((round - 1)) round(s)" | tee -a "$LOG"
      exit 0
    fi
    mkdir -p .ralph
    # The program's own prints are not the failure. A generator that logged
    # every level filled this file with maps, and the plan never saw the check.
    if grep -q "CHECK FAILED" "$OUT"; then
      grep "CHECK FAILED" "$OUT" > .ralph/last-check.txt
      # A frame like physics.js:62 is the file. The program's own prints stay out.
      grep -E '\.(js|mjs|cjs|py):[0-9]+' "$OUT" \
        | grep -v -E 'node:internal|node_modules' >> .ralph/last-check.txt || true
    else
      tail -n 80 "$OUT" > .ralph/last-check.txt
    fi
    cat "$OUT" >>"$LOG"
  fi

  check_args=()
  [ -n "$CHECK" ] && check_args=(--check "$CHECK")
  "$TEAM" do "${check_args[@]+"${check_args[@]}"}" "${DO_FLAGS[@]+"${DO_FLAGS[@]}"}" "$REQUEST" >>"$LOG" 2>&1
  rc=$?
  # The writer has exited, so the floor-4 box is free. One note for the
  # next round, and no second call once that round starts.
  if [ -n "$CHECK" ]; then
    "$TEAM" between --check "$CHECK" >>"$LOG" 2>&1 || true
  fi
  if [ -z "$CHECK" ] && [ "$rc" -eq 0 ]; then
    echo "ralph: done after $round round(s)" | tee -a "$LOG"
    exit 0
  fi
done
