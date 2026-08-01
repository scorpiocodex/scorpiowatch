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

# Warm the interpreter so the first *recorded* command is not also paying import cost.
swatch --help >/dev/null 2>&1

# ── The two simulated saves ─────────────────────────────────────────────────────────────
# Started last, so its clock begins as close to the recording as possible.
#
# It does not fire at a guessed wall-clock offset. It waits for the `swatch run` process
# to actually exist, then dwells — so the saves stay correctly placed no matter how long
# setup took, how fast vhs types, or how slow the interpreter is to boot. The `seq` guard
# caps the wait at 60s so a failed launch can never hang the render forever.
#
# Each "save" is two writes 200ms apart: comfortably inside the FilesystemAdapter's 400ms
# debounce, so they coalesce into a single Run, while giving the trigger two chances to be
# caught if the watcher is still arming.
F="$WORK/my-app/app.py"
(
  if command -v pgrep >/dev/null 2>&1; then
    for _ in $(seq 1 600); do
      pgrep -f 'swatch run' >/dev/null 2>&1 && break
      sleep 0.1
    done
  else
    sleep 7          # fallback: blind offset measured from the end of setup
  fi
  sleep 3.0          # dwell — the banner stays on screen long enough to read
  echo >> "$F"; sleep 0.2; echo >> "$F"      # save #1
  sleep 4.2
  echo >> "$F"; sleep 0.2; echo >> "$F"      # save #2
) >/dev/null 2>&1 &
disown

# ── Hand over a clean screen ────────────────────────────────────────────────────────────
# The `clear` is the last thing this script does, so even if Hide/Show misbehaves the most
# a viewer can ever see is the single `source demo/setup.sh` line, wiped immediately.
cd "$WORK/hello" || return
clear
