# demo

The animated terminal demo shown at the top of the project [`README.md`](../README.md), and
the toy project it is recorded against.

| Path | What it is |
|---|---|
| `demo.tape` | A [Charm VHS](https://github.com/charmbracelet/vhs) script — the recording, as code |
| `demo.gif` | The rendered clip the README embeds. **Not committed yet** — see [Rendering](#rendering) |
| `project/` | A three-test Python project. Real code, real tests, really run by `swatch` |

Nothing here is mocked. The GIF is a recording of `swatch` actually watching `project/`,
actually firing on a file change, and actually running `pytest`. If the CLI's output
changes, re-rendering the tape is what updates the README — there is no hand-written
transcript to drift out of sync.

---

## Rendering

**Claude Code cannot do this step** — VHS drives a real PTY. It needs a human with a
terminal. It is one command once the prerequisites are in place.

### 1. Install VHS

VHS also needs `ttyd` (≥ 1.7.2) and `ffmpeg`; the package managers below pull them in.

```bash
brew install vhs                 # macOS, or Linuxbrew
sudo pacman -S vhs               # Arch
go install github.com/charmbracelet/vhs@latest
```

The tape uses the `Wait` command, so **vhs ≥ 0.7.0** is required. Check with `vhs --version`.

> **On Windows** — render from **WSL2**, not from PowerShell. The tape drives a POSIX shell
> (`Set Shell bash`, `mktemp`, `cp -r`), and VHS's Windows support for `ttyd` is not
> dependable. One thing to get right: a `.venv` created by Windows `uv` contains
> `Scripts/*.exe`, which Linux cannot execute. Work from a copy of the repository inside the
> WSL filesystem and run `uv sync` there so `.venv/bin/` exists.

### 2. Sync the environment

The tape puts `./.venv` on `PATH` so the recorded commands can read as a plain `swatch ...`
instead of `uv run swatch ...`. From the repository root:

```bash
uv sync
```

### 3. Render

From the **repository root** — the tape's paths are relative to it:

```bash
vhs demo/demo.tape
```

That writes `demo/demo.gif` (roughly 19 seconds, 1200×700). Expect it to take a little longer
than the clip itself: VHS renders every frame, then encodes.

### 4. Check and commit

Open the GIF and confirm it shows the run finishing green. Then:

```bash
git add demo/demo.gif
git commit -m "chore(demo): render demo.gif from demo.tape"
```

---

## What the recording shows

Six beats, one continuous terminal session, no cuts:

1. **`swatch init .`** in an empty directory — scaffolds a `swatch.toml`.
2. **`cd ../my-app`** — into a project that already has one (a throwaway copy of `project/`).
3. **`swatch run .`** — the engine starts watching. No flags: the everyday form.
4. A background writer simulates an editor save; the `tests` trigger matches, `pytest` runs,
   and the Run reports a green verdict.
5. A second save, ~4 s later — proving this is a loop, not a one-shot.
6. **`Ctrl+C`** — a graceful drain, then the run tally.

Verbatim output from a rehearsal of beats 1–5:

```
  ✓ wrote swatch.toml
  edit it, then run:  swatch run .

  swatch  ·  engine starting

  ✓ config loaded    swatch.toml  ·  1 triggers, 1 workflows
  watching .  ·  1 triggers armed  ·  ^C to stop

  02:04:43  ·  tests  → started  r_5c1f
  02:04:44  ·  tests  ✓ succeeded  0.9s
  02:04:47  ·  tests  → started  r_8b69
  02:04:48  ·  tests  ✓ succeeded  0.9s
```

Beat 6 then adds a `draining…` notice and the tally
(`processed 2 run(s): 2 succeeded, 0 failed, 0 cancelled`), exiting 130 as an interrupt
should. That beat is the one part **not** reproduced in the rehearsal above: it was authored
on Windows, where neither MSYS `kill -INT` nor a console control event reaches the handler
`swatch` installs. It is ordinary POSIX signal handling and will work under the Linux/WSL
terminal the tape renders in — but it is the first thing to check in the finished GIF.

### Two things the clip will *not* show, by design

Both are correct behaviour, not defects in the recording:

- **No `pytest` output.** The default view is **quiet-on-success, loud-on-failure**
  ([`UI_DESIGN.md`](../docs/UI_DESIGN.md) §4.3). Had a test failed, the failing step's output
  tail would be printed, framed, under the verdict.
- **No spinner.** The transient liveness spinner is revealed only once a step has been
  running for ~1 s (`reporter.py`, `_LIVENESS_DELAY_S`). These three tests finish in a
  fraction of that, so the spinner never gets to appear — the price of the fast, always-green
  suite the demo wants. To see it, give `project/` a step slow enough to cross the threshold.

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
