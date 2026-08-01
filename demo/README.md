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
actually firing on two file changes, and actually running `pytest`. If the CLI's output
changes, re-rendering the tape is what updates the README — there is no hand-written
transcript to drift out of sync.

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

That writes `demo/demo.gif` (~18 s, 1200×700, ~120 KB). Rendering takes well under a minute.

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
toy project, the interpreter warm-up, and above all the **background writer that stages the
two file changes** — lives in [`setup.sh`](./setup.sh), which the tape sources inside a `Hide`
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

Six beats, one continuous terminal session, no cuts:

1. **`swatch init .`** in an empty directory — scaffolds a `swatch.toml`.
2. **`cd ../my-app`** — into a project that already has one (a throwaway copy of `project/`).
3. **`swatch run .`** — the engine starts watching. No flags: the everyday form.
4. A simulated save; the `tests` trigger matches, `pytest` runs, green verdict.
5. A second save ~4 s later — proving this is a loop, not a one-shot.
6. **`Ctrl+C`** — a graceful drain, then the run tally.

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

  10:21:59  ·  tests  → started  r_10c7
  10:22:00  ·  tests  ✓ succeeded  0.8s
  10:22:03  ·  tests  → started  r_72ad
  10:22:04  ·  tests  ✓ succeeded  0.6s
^C  draining… press Ctrl+C again to force

  processed 2 run(s): 2 succeeded, 0 failed, 0 cancelled
```

### Timing, and why it does not drift

The two saves are **not** fired at guessed wall-clock offsets. The writer in `setup.sh` blocks
until the `swatch run` process actually exists, then dwells 3.0 s before the first save and
4.2 s before the second. So the saves stay correctly placed no matter how long setup took, how
fast VHS types, or how slow the interpreter is to boot — the one thing a fixed offset cannot
survive. A `seq` guard caps that wait at 60 s so a failed launch can never hang the render.

Each "save" is two writes 200 ms apart: inside the `FilesystemAdapter`'s 400 ms debounce, so
they coalesce into a single Run, while giving the trigger two chances to be caught if the
watcher is still arming. The tape's `Wait+Screen` guards then hold each beat until its output
is really on screen, so a slow machine stretches the clip rather than truncating it.

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
