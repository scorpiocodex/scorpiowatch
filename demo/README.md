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

That writes `demo/demo.gif` (~15.5 s, 1200×700, ~100 KB). Rendering takes about 30 seconds.

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
toy project, the interpreter warm-ups, and above all the **background writer that stages the
file change** — lives in [`setup.sh`](./setup.sh), which the tape sources inside a `Hide`
block.

That split is not cosmetic. A viewer who watched the saves being scheduled would rightly
conclude the demo was rigged, so the plumbing has to stay off camera. Routing it through one
sourced script means the tape types exactly **one** line while hidden, and `setup.sh` ends
with `clear` — so even if `Hide` were to fail outright, the worst a viewer could ever see is a
single `source demo/setup.sh` line, wiped a moment later.

`setup.sh` is *sourced*, not executed, because its `export`s and its final `cd` have to land
in the recording shell itself. For the same reason it deliberately does not use `set -e`: a
non-zero exit from any command would take the whole recording shell down with it.

## What the recording shows

Five beats, one continuous terminal session, no cuts:

1. **`swatch init .`** in an empty directory — scaffolds a `swatch.toml`.
2. **`cd ../my-app`** — into a project that already has one (a throwaway copy of `project/`).
3. **`swatch run .`** — the engine starts watching. No flags: the everyday form.
4. A simulated save; the `tests` trigger matches, `pytest` runs, green verdict.
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

  15:23:56  ·  tests  → started  r_b295
  15:23:56  ·  tests  ✓ succeeded  0.2s
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

### Timing, and why it does not drift

The save is **not** fired at a guessed wall-clock offset. The writer in `setup.sh` blocks until
the `swatch run` process actually exists, then dwells 5 s. So it stays correctly placed no
matter how long setup took, how fast VHS types, or how slow the interpreter is to boot — the
one thing a fixed offset cannot survive. A `seq` guard caps that wait at 60 s so a failed
launch can never hang the render.

> [!IMPORTANT]
> A save that lands **before** the watcher is armed is not late — it is lost, permanently, and
> the recording then waits for a run that can never happen. Under WSL this is guaranteed rather
> than likely: `watchfiles` auto-forces its **polling** backend (`_default_force_polling`
> greps `/proc/version` for `microsoft`, and the process has no inotify fd at all). A poller
> snapshots the tree when it starts and reports differences against that snapshot, so a write
> arriving beforehand is baseline, not a change. Nothing later recovers it.

The dwell is therefore sized for the worst case, not the typical one. Measured exec →
`1 triggers armed` in WSL: 0.22 s / 0.22 s / 0.50 s warm, 0.58 s with the page cache dropped,
and 1.01 s cold under eight busy CPU loops. 5 s is that worst case plus a 4 s margin — about
5× headroom, at a cost of roughly 4 s of banner on screen before anything moves.

Detection latency is separate and costs nothing: polling adds up to `poll_delay_ms` (300 ms)
and the adapter debounces for 400 ms, so expect ~0.7 s between the write and `→ started`.
Measured end to end, write → verdict on screen is ~0.37 s in practice.

The save is a single append. It used to be two writes 200 ms apart — nominally inside the
`FilesystemAdapter`'s 400 ms debounce, with the second as insurance against a not-yet-armed
watcher. Under VHS that insurance backfired: scheduling jitter occasionally pushed the two
events onto opposite sides of the debounce window, and the clip recorded **two** Runs for one
apparent save, which reads as a bug. The `pgrep` gate already guarantees the watcher is armed,
so the second write bought nothing and cost correctness.

The tape's `Wait+Screen` guards then hold each beat until its output is really on screen.

### Why the recorded run reports 0.1 s

The verdict prints a real measured duration, so the number is only as good as the conditions it
was measured under. Two things in `setup.sh` make it the steady-state cost of the tests rather
than a one-off:

- **A throwaway `pytest` run before recording.** A first-ever pytest in a fresh tree pays cold
  imports and bytecode compilation of `app.py`/`test_app.py` — ~0.95 s cold against ~0.39 s
  warm, measured. Running it once off camera means the viewer sees the second number. The
  recorded run is genuine either way; this only decides which of two honest numbers is shown.
- **`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`**, exported in `setup.sh` and allowlisted through by
  `project/swatch.toml`. On every start pytest scans installed entry points and imports every
  plugin it finds — here asyncio, cov, anyio and hypothesis, none of which three toy tests
  need. Skipping the scan roughly halves the run again (~0.39 s → ~0.19 s). Note this is
  entry-point *discovery*: `-p no:...` on the command line blocks a plugin only after paying to
  find it, and measured no meaningful saving.

Neither is required. Drop both and the demo still works, with a larger number on screen.

### Two things the clip does *not* show, by design

Both are correct behaviour, not defects in the recording:

- **No `pytest` output.** The default view is **quiet-on-success, loud-on-failure**
  ([`UI_DESIGN.md`](../docs/UI_DESIGN.md) §4.3). Had a test failed, the failing step's output
  tail would be printed, framed, under the verdict.
- **No spinner.** The transient liveness spinner is revealed only once a step has been running
  for ~1 s (`reporter.py`, `_LIVENESS_DELAY_S`). These three tests finish in a fraction of
  that, so the spinner never gets to appear — the price of the fast, always-green suite the
  demo wants. To see it, give `project/` a step slow enough to cross the threshold.

---

## Running the toy project yourself

`project/` is a working example, not just a recording prop. From the repository root:

```bash
uv run swatch run demo/project          # watch until Ctrl+C — what the GIF shows
uv run swatch run demo/project --once   # one batch, then exit — the CI form
```

Then edit `demo/project/app.py` in another terminal. Each save reruns the three tests.

### Two things it demonstrates that are easy to get wrong

**`env_allowlist` is load-bearing.** A Step's child process starts from a *fully scrubbed*
environment — it inherits nothing. That is a security property, but it also means the OS has
no `PATH` with which to find the program you named: POSIX falls back to `/bin:/usr/bin` and
never sees your virtualenv, and Windows loses `SYSTEMROOT`, without which the interpreter
cannot initialise its socket layer and dies with `WinError 10106`. `project/swatch.toml`
allowlists `PATH`, `PATHEXT`, and `SYSTEMROOT` for exactly this reason — that is what makes a
bare `pytest` resolve to the pytest of whichever environment `swatch` was launched from.

**`pytest.ini` claims the rootdir.** Without it, pytest walks up out of `demo/project`, finds
the repository's own `pyproject.toml`, and adopts its `[tool.pytest.ini_options]` — including
`--cov=swatch --cov-fail-under=80`. Three toy tests do not cover the engine, so the coverage
gate fails, and the demo records a red run for a reason that has nothing to do with the demo.
