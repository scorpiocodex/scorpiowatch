# demo

The animated terminal demo shown at the top of the project [`README.md`](../README.md), and
the toy project it is recorded against.

| Path | What it is |
|---|---|
| `demo.tape` | A [Charm VHS](https://github.com/charmbracelet/vhs) script — the recording, as code |
| `setup.sh` | Everything staged *before* the camera rolls, kept out of the recording |
| `demo.gif` | The rendered clip the README embeds |
| `project/` | A three-test Python project. Real code, real tests, really run by `swatch` |

Nothing here is mocked. The GIF is a recording of `swatch` actually watching `project/`,
actually firing on a file change, and actually running `pytest`. If the CLI's output changes,
re-rendering the tape is what updates the README — there is no hand-written transcript to
drift out of sync.

---

## Rendering

One command, but it has three prerequisites that each fail in a quiet, confusing way if you
skip them. They are all worth two minutes up front.

### 1. Install VHS

VHS also needs `ttyd` (≥ 1.7.2) and `ffmpeg`; the package managers below pull them in.

```bash
brew install vhs                 # macOS, or Linuxbrew
sudo pacman -S vhs               # Arch
go install github.com/charmbracelet/vhs@latest
```

The tape uses `Wait`, so **vhs ≥ 0.7.0** is required. Check with `vhs --version`.

### 2. Install JetBrains Mono in the *render* environment

> [!IMPORTANT]
> This is the one that bites. If the font is missing, VHS does **not** warn you — it silently
> falls back to a font with different metrics, and the recording comes out with mangled
> letter spacing that is only obvious once you look at the finished GIF.

```bash
sudo apt-get install -y fonts-jetbrains-mono
fc-cache -f
fc-list | grep -i jetbrains       # must print something
```

If your distribution has no such package, drop the TTFs into `~/.local/share/fonts/` and run
`fc-cache -f`. Either way, confirm before rendering:

```bash
fc-match "JetBrains Mono"         # must resolve to JetBrainsMono-*.ttf, not DejaVu et al.
```

### 3. Work from a native Linux filesystem

> [!IMPORTANT]
> On WSL, the repository must live under `/home/`, **not** `/mnt/c/`. Two things break
> otherwise: `/mnt/c` is a drvfs mount where inotify does not reliably fire, so the file
> watcher may never see the simulated saves; and a `.venv` created by Windows contains
> `Scripts/*.exe`, which Linux cannot execute. Clone or copy the repo into the WSL
> filesystem and run `uv sync` there.

```bash
uv sync                           # from the repository root
```

The tape puts `./.venv` on `PATH` so the recorded commands read as a plain `swatch ...`
rather than `uv run swatch ...`.

### 4. Render

From the **repository root** — the tape's paths are relative to it:

```bash
vhs demo/demo.tape
```

That writes `demo/demo.gif` (~12.3 s, 1200×700, ~100 KB). Rendering takes about 30 seconds.

### 5. Check and commit

Open the GIF and confirm three things: the recording opens on a bare prompt with **no setup
commands visible**, the text is evenly spaced, and the run finishes green. Then:

```bash
git add demo/demo.gif
git commit -m "chore(demo): re-render demo.gif"
```

---

## Why setup lives in a separate script

Everything the recording needs — the `PATH` and prompt exports, the throwaway copies of the
toy project, the interpreter warm-ups, and above all the **writer that stages the file
change** — lives in [`setup.sh`](./setup.sh), which the tape sources inside a `Hide` block.

That split is not cosmetic. A viewer who watched the save being scheduled would rightly
conclude the demo was rigged, so the plumbing has to stay off camera.

> [!IMPORTANT]
> `Hide` stops VHS **capturing frames**. It does not stop the terminal from existing, and
> anything still on screen when `Show` resumes is recorded. A clean opening frame is therefore
> not a question of hiding the setup — it is a question of the screen being genuinely empty at
> the moment `Show` happens.

That distinction caused a real bug. The tape used to allow `Sleep 3s` for `setup.sh` to finish
and then send `Ctrl+L`. When setup ran even slightly long the keystroke arrived while the shell
was still busy, so readline never interpreted it: `Ctrl+L` echoed as a literal `^L` instead of
clearing, `Show` fired over a dirty screen, and the recording opened on `> source
demo/setup.sh` followed by `^L`.

The fix is to wait for a fact rather than a duration. `setup.sh` prints `SWATCH_DEMO_READY` as
its final act; the tape blocks on `Wait+Screen /SWATCH_DEMO_READY/`, which cannot pass until
every statement in the script has run. The `clear` it then types is guaranteed to arrive at an
idle shell. Verified by injecting a deliberate 6-second delay into `setup.sh`: the opening frame
stayed clean and the recorded clip did not change length.

Two tidier approaches were checked first and do not exist in VHS v0.11.0. `Set Shell "bash
--rcfile demo/setup.sh"` is rejected as an invalid shell. `Env HOME ...` is accepted, but VHS
starts bash with rc files disabled — which is also why its default prompt is a bare `> ` — so
no startup file is read and there is no pre-record hook to hang setup on.

`setup.sh` is *sourced*, not executed, because its `export`s and its final `cd` have to land
in the recording shell itself. For the same reason it deliberately does not use `set -e`: a
non-zero exit from any command would take the whole recording shell down with it.

## What the recording shows

Five beats, one continuous terminal session, no cuts:

1. **`swatch init .`** in an empty directory — scaffolds a `swatch.toml`.
2. **`cd ../my-app`** — into a project that already has one (a throwaway copy of `project/`).
3. **`swatch run .`** — the engine starts watching. No flags: the everyday form.
4. A simulated save; the `tests` trigger matches, the checks run, green verdict.
5. **`Ctrl+C`** — a graceful drain, then the run tally.

Transcribed from the rendered GIF:

```
hello ❯ swatch init .
  ✓ wrote swatch.toml
  edit it, then run:  swatch run .
hello ❯ cd ../my-app
my-app ❯ swatch run .

  swatch  ·  engine starting

  ✓ config loaded    swatch.toml  ·  1 triggers, 1 workflows
  watching .  ·  1 triggers armed  ·  ^C to stop

  16:31:04  ·  tests  → started  r_edac
  16:31:04  ·  tests  ✓ succeeded  0.0s
^C  draining… press Ctrl+C again to force

  processed 1 run(s): 1 succeeded, 0 failed, 0 cancelled
```

### One run, not two

An earlier cut staged a second save to prove the watcher keeps going. It was not worth it. The
tape's guard then had to match *two* verdicts, which made the render brittle in a bad
direction: anything that delayed or swallowed the second run failed the whole recording rather
than merely shortening it. Waiting on a single verdict means a slow machine stretches the clip
instead of breaking it, and the loop is already legible from `^C to stop` and the closing
tally.

### The save is gated on the watcher, not on a timer

This is the part that took the longest to get right, so it is worth stating plainly.

> [!IMPORTANT]
> A save that lands **before** the watcher is armed is not late — it is lost, permanently, and
> the recording then waits for a run that can never happen. Under WSL this is structural rather
> than unlucky: `watchfiles` auto-forces its **polling** backend (`_default_force_polling`
> greps `/proc/version` for `microsoft`; the process has no inotify fd at all). A poller
> snapshots the tree when it starts and reports differences against that snapshot, so a write
> arriving beforehand is baseline, not a change. Nothing later recovers it.

Earlier versions slept a tuned number of seconds and hoped the save landed on the right side of
that line. It was a race against the visible typing, and it broke every time the tape changed.

The writer now waits for **the watcher itself**, not for a duration. `watchfiles` runs its
backend on a dedicated thread that does not exist while `swatch` is still importing and reading
config — it is spawned at the moment watching begins. So the process's thread count stepping
above 1 is a direct causal consequence of the watcher arming. Measured, the transition and the
`1 triggers armed` line land in the same 50 ms sample:

```
t=1.11s   threads=1   armed=no
t=1.18s   threads=3   armed=YES
```

Because the gate is the watcher rather than the clock, nothing in the tape can invalidate it.
Retype the commands, change the sleeps, add a beat, run it on a slower machine — the save still
lands after the watcher is armed. This was verified by starting `swatch` **two seconds after**
the writer: the writer simply waited, and the run fired normally.

The 1.2 s dwell that follows is not a correctness margin. It covers the sub-millisecond gap
between the thread being spawned and it taking its first snapshot, and gives the viewer a beat
to read the banner before anything moves. `seq` guards cap every wait so a failed launch can
never hang the render, and a non-Linux host (no `/proc`) falls back to a generous blind wait.

Detection latency is separate and costs nothing, since it happens after the save: polling adds
up to `poll_delay_ms` (300 ms) and the adapter debounces for 400 ms.

The save is a single append. It used to be two writes 200 ms apart, as insurance against a
not-yet-armed watcher. Under VHS that insurance backfired — scheduling jitter occasionally
pushed the two events onto opposite sides of the debounce window, and the clip recorded **two**
Runs for one apparent save, which reads as a bug. With the gate above, the insurance was
unnecessary anyway.

The tape's `Wait+Screen` guards then hold each beat until its output is really on screen.

### Two things the clip does *not* show, by design

Both are correct behaviour, not defects in the recording:

- **No step output.** The step prints `all checks passed`, and you will not see it. The default
  view is **quiet-on-success, loud-on-failure** ([`UI_DESIGN.md`](../docs/UI_DESIGN.md) §4.3):
  a passing step's stdout is swallowed on purpose. Had it failed, the output tail would be
  printed, framed, under the verdict.
- **No spinner.** The transient liveness spinner is revealed only once a step has been running
  for ~1 s (`reporter.py`, `_LIVENESS_DELAY_S`). The step finishes in ~0.03 s, so the spinner
  never gets to appear — the price of a verdict that feels instant. To see it, give `project/`
  a step slow enough to cross the threshold.

The verdict reads `✓ succeeded  0.0s` for the same reason: the duration is real, and
`_fmt_duration` renders it to one decimal.

---

## Running the toy project yourself

`project/` is a working example, not just a recording prop. From the repository root:

```bash
uv run swatch run demo/project          # watch until Ctrl+C — what the GIF shows
uv run swatch run demo/project --once   # one batch, then exit — the CI form
```

Then edit `demo/project/app.py` in another terminal. Each save reruns the checks.

The watched workflow runs a direct `python -c` rather than `pytest`, because it exists to be
recorded and ~0.03 s beats ~0.21 s for the same two assertions. It is still a real subprocess
asserting against the real module — break `add` or `greet` and the Run goes red. `test_app.py`
and `pytest.ini` are kept beside it so the toy is also a normal project you can `pytest`:

```bash
cd demo/project && pytest -q      # the same code, the conventional way
```

### Two things it demonstrates that are easy to get wrong

**`env_allowlist` is load-bearing.** A Step's child process starts from a *fully scrubbed*
environment — it inherits nothing. That is a security property, but it also means the OS has
no `PATH` with which to find the program you named: POSIX falls back to `/bin:/usr/bin` and
never sees your virtualenv, and Windows loses `SYSTEMROOT`, without which the interpreter
cannot initialise its socket layer and dies with `WinError 10106`. `project/swatch.toml`
allowlists `PATH`, `PATHEXT`, and `SYSTEMROOT` for exactly this reason — that is what makes a
bare `python` resolve to the interpreter of whichever environment `swatch` was launched from.

**`pytest.ini` claims the rootdir.** Without it, pytest walks up out of `demo/project`, finds
the repository's own `pyproject.toml`, and adopts its `[tool.pytest.ini_options]` — including
`--cov=swatch --cov-fail-under=80`. Three toy tests do not cover the engine, so the coverage
gate fails, and the demo records a red run for a reason that has nothing to do with the demo.
