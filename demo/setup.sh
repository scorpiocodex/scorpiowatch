# demo/setup.sh — everything demo.tape needs staged before the camera rolls.
#
# This is SOURCED, not executed: `demo.tape` runs `source demo/setup.sh` inside a Hide
# block, so the exports and the final `cd` have to land in the recording shell itself.
#
# It exists because the recorded portion of the demo must contain NOTHING but the three
# swatch commands. Every line of plumbing — and above all the writer that stages the file
# change — lives here, where the camera cannot see it. A viewer who watched the save being
# scheduled would rightly conclude the whole thing was rigged.
#
# Run from the repository root (vhs starts its shell wherever it was invoked).
#
# NOTE: deliberately no `set -e`. This is sourced into an interactive shell; a non-zero
# exit from any command would take the whole recording shell down with it.

# ── Where things live ───────────────────────────────────────────────────────────────────
REPO="$PWD"

# Put the project virtualenv first on PATH so the recorded commands read as a plain
# `swatch ...` rather than `uv run swatch ...`. Both layouts are prepended: POSIX
# virtualenvs use bin/, Windows ones Scripts/; only the one that exists matters. This is
# also the PATH the toy project's `env_allowlist` forwards to the step, so the Run resolves
# this same environment.
export PATH="$REPO/.venv/bin:$REPO/.venv/Scripts:$PATH"

# ── Prompt ──────────────────────────────────────────────────────────────────────────────
# A dim directory name plus an amber ❯ (the #E8B847 brand accent, via brightYellow).
# PROMPT_COMMAND is unset first: a starship/oh-my-posh style prompt re-renders PS1 before
# every prompt and would silently overwrite this one line later.
unset PROMPT_COMMAND
PS1='\[\e[90m\]\W\[\e[0m\] \[\e[93m\]❯\[\e[0m\] '

# ── Throwaway working copies ────────────────────────────────────────────────────────────
# Never record against the tracked files: `swatch init` would overwrite
# demo/project/swatch.toml, and the simulated save would leave app.py dirty. mktemp also
# puts the watched directory on a real Linux filesystem, which is what makes file watching
# work even when the repository itself is mounted from elsewhere.
WORK="$(mktemp -d)"
mkdir -p "$WORK/hello"
cp -r "$REPO/demo/project" "$WORK/my-app"

# ── Warm-up ─────────────────────────────────────────────────────────────────────────────
# Prime both interpreters off camera, so the first recorded command is not also paying
# start-up cost and the recorded verdict shows steady-state cost rather than a one-off
# cache miss. The run is genuine either way; this only decides which of two honest numbers
# gets recorded.
swatch --help >/dev/null 2>&1
( cd "$WORK/my-app" && python -c "import app; assert app.add(2, 3) == 5" ) >/dev/null 2>&1

# ── The simulated save ──────────────────────────────────────────────────────────────────
# This used to sleep a tuned number of seconds and hope the save landed after the watcher
# was armed. That was a race, and it broke every time the visible sequence changed. It no
# longer waits for a DURATION — it waits for the watcher to actually be ready, and there is
# no timing assumption left to invalidate.
#
# The signal is the watcher's own thread. `watchfiles` runs its backend on a dedicated
# thread that does not exist while swatch is still importing and loading config; it is
# spawned at the moment watching begins. So the process's thread count stepping above 1 is
# a direct causal consequence of the watcher arming, not a proxy for it. Measured, the
# transition and the `1 triggers armed` line land in the same 50ms sample:
#
#     t=1.11s   threads=1   armed=no
#     t=1.18s   threads=3   armed=YES
#
# Why this matters so much: a save that lands BEFORE the watcher is armed is not late, it
# is lost permanently, and the recording then waits for a run that can never happen. Under
# WSL that loss is structural — watchfiles auto-forces its polling backend
# (`_default_force_polling` greps /proc/version for "microsoft"), and a poller treats
# anything written before its first snapshot as baseline rather than as a change.
#
# The dwell below is no longer a correctness margin. It covers the sub-millisecond gap
# between the thread being spawned and it taking that first snapshot, and it gives the
# viewer a beat to read the banner before anything moves.
F="$WORK/my-app/app.py"
(
  # 1. Wait for the `swatch run` process to exist. The seq guards cap every wait so a
  #    failed launch can never hang the render.
  SW=""
  for _ in $(seq 1 600); do
    SW="$(pgrep -f 'swatch run' 2>/dev/null | head -1)"
    [ -n "$SW" ] && break
    sleep 0.1
  done

  # 2. Wait for it to ARM. No /proc (non-Linux) means no signal to gate on, so fall back to
  #    a generous blind wait rather than firing immediately into the dead zone.
  if [ -n "$SW" ] && [ -d "/proc/$SW/task" ]; then
    for _ in $(seq 1 1200); do
      [ "$(ls "/proc/$SW/task" 2>/dev/null | wc -l)" -gt 1 ] && break
      sleep 0.05
    done
  else
    sleep 12
  fi

  sleep 1.2          # readability beat; see above — not a correctness margin
  echo >> "$F"
) >/dev/null 2>&1 &
disown

# ── Hand over a clean screen ────────────────────────────────────────────────────────────
# The tape also types `clear` before `Show`, so this is belt and braces: even if Hide/Show
# misbehaved, the most a viewer could ever see is the single `source demo/setup.sh` line,
# wiped immediately.
cd "$WORK/hello" || return
clear
