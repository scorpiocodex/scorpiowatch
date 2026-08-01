# demo/setup.sh — everything demo.tape needs staged before the camera rolls.
#
# This is SOURCED, not executed: `demo.tape` runs `source demo/setup.sh` inside a Hide
# block, so the exports and the final `cd` have to land in the recording shell itself.
#
# It exists because the recorded portion of the demo must contain NOTHING but the three
# swatch commands. Every line of plumbing — and above all the background writer that
# stages the file changes — lives here, where the camera cannot see it. A viewer who
# watched the saves being scheduled would rightly conclude the whole thing was rigged.
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
# also the PATH the toy project's `env_allowlist` forwards to pytest, so the Run resolves
# this same environment.
export PATH="$REPO/.venv/bin:$REPO/.venv/Scripts:$PATH"

# Skip pytest's entry-point scan in the recorded Run. The toy's swatch.toml allowlists this
# variable through to the step; see the comment there for why it roughly halves the run. It is
# an optimisation only — unset it and the demo still works, just a fraction slower.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

# ── Prompt ──────────────────────────────────────────────────────────────────────────────
# A dim directory name plus an amber ❯ (the #E8B847 brand accent, via brightYellow).
# PROMPT_COMMAND is unset first: a starship/oh-my-posh style prompt re-renders PS1 before
# every prompt and would silently overwrite this one line later.
unset PROMPT_COMMAND
PS1='\[\e[90m\]\W\[\e[0m\] \[\e[93m\]❯\[\e[0m\] '

# ── Throwaway working copies ────────────────────────────────────────────────────────────
# Never record against the tracked files: `swatch init` would overwrite
# demo/project/swatch.toml, and the simulated saves would leave app.py dirty. mktemp also
# puts the watched directory on a real Linux filesystem, which is what makes inotify fire
# even when the repository itself is mounted from elsewhere.
WORK="$(mktemp -d)"
mkdir -p "$WORK/hello"
cp -r "$REPO/demo/project" "$WORK/my-app"

# ── Warm-up ─────────────────────────────────────────────────────────────────────────────
# Both interpreters that the recording depends on, primed off camera.
#
# `swatch --help` warms swatch's own imports, so the first recorded command is not also
# paying start-up cost. The throwaway `pytest` run matters more, and for a subtler reason:
# the RECORDED verdict prints a real measured duration, and a first-ever pytest in this tree
# pays cold imports plus bytecode compilation of app.py/test_app.py (~0.95s cold vs ~0.39s
# warm, measured). Running it once here means the number the viewer sees is the steady-state
# cost of the tests, not a one-off cache miss. The run itself is genuine either way — this
# only decides which of two honest numbers gets recorded.
swatch --help >/dev/null 2>&1
( cd "$WORK/my-app" && pytest -q ) >/dev/null 2>&1

# ── The simulated save ──────────────────────────────────────────────────────────────────
# Started last, so its clock begins as close to the recording as possible.
#
# It does not fire at a guessed wall-clock offset. It waits for the `swatch run` process to
# actually exist, then dwells — so the save stays correctly placed no matter how long setup
# took, how fast vhs types, or how slow the interpreter is to boot. The `seq` guard caps the
# wait at 60s so a failed launch can never hang the render forever.
#
# ── Why the dwell is generous ───────────────────────────────────────────────────────────
# A save that lands before the watcher is armed is not late — it is LOST, permanently, and
# the recording then waits forever for a run that can never happen. That failure is silent
# and total, so the dwell is sized for the worst case rather than the typical one.
#
# It is worth knowing exactly why the change vanishes. Under WSL, watchfiles auto-forces its
# POLLING backend (`_default_force_polling` greps /proc/version for "microsoft"; there is no
# inotify fd on the process at all). A poller takes a snapshot of the tree when it starts and
# reports differences against it — so a write landing before that first snapshot is not a
# change, it is just part of the baseline. No amount of later waiting recovers it.
#
# Measured exec -> `1 triggers armed`, in this WSL instance:
#     warm            0.22s, 0.22s, 0.50s
#     cold page cache 0.58s
#     cold + 8 busy CPU loops (worst case attempted)    1.01s
# 5s is that worst case plus a 4s margin — roughly 5x headroom. The cost is ~4s of the banner
# sitting on screen before anything moves, which reads as deliberate rather than slow.
#
# Detection is not instant either, and the dwell is not what covers that: polling adds up to
# `poll_delay_ms` (300ms) and the adapter then debounces for 400ms, so expect ~0.7s between
# the write and `→ started`. That delay is after the save, so it costs nothing but patience.
#
# The save is ONE append, deliberately. It used to be two writes 200ms apart — nominally
# inside the FilesystemAdapter's 400ms debounce, so they would coalesce into a single Run,
# with the second write as insurance in case the watcher was still arming. Under vhs that
# insurance backfired: scheduling jitter occasionally pushed the two events onto opposite
# sides of the debounce window and the clip recorded two Runs for one apparent save, which
# reads as a bug. The pgrep gate plus the dwell below already guarantee the watcher is
# armed, so the second write bought nothing and cost correctness. One write, one event,
# one Run.
F="$WORK/my-app/app.py"
(
  if command -v pgrep >/dev/null 2>&1; then
    for _ in $(seq 1 600); do
      pgrep -f 'swatch run' >/dev/null 2>&1 && break
      sleep 0.1
    done
  else
    # No pgrep: nothing to anchor to, so this must cover the ENTIRE visible sequence from
    # here to the watcher arming. Budget, from this script backgrounding the writer:
    #   to `Show`                                          3.9s   (worst case of the 4s Hide sleep)
    #   Sleep 800ms + type `swatch init .` (13ch@50ms)     1.45s
    #   Sleep 350ms + `swatch init` running                3.35s  (measured ~0.5s; 3s worst case)
    #   Sleep 1500ms + type `cd ../my-app` (12ch@50ms)     2.10s
    #   Sleep 300ms + Sleep 700ms                          1.00s
    #   type `swatch run .` (12ch@50ms) + Sleep 350ms      0.95s
    #   engine boot to armed                               2.00s  (measured 1.01s worst; doubled)
    #                                                    -------
    #                                                     14.75s  + 4s margin = 19s
    sleep 19
  fi
  sleep 5            # dwell — see above: measured worst-case boot 1.01s + 4s margin
  echo >> "$F"
) >/dev/null 2>&1 &
disown

# ── Hand over a clean screen ────────────────────────────────────────────────────────────
# The `clear` is the last thing this script does, so even if Hide/Show misbehaves the most
# a viewer can ever see is the single `source demo/setup.sh` line, wiped immediately.
cd "$WORK/hello" || return
clear
