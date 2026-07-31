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

That writes `demo/demo.gif` (roughly 18 seconds, 960×560). Expect it to take a little longer
than the clip itself: VHS renders every frame, then encodes.

### 4. Check and commit

Open the GIF and confirm it shows the run finishing green. Then:

```bash
git add demo/demo.gif
git commit -m "chore(demo): render demo.gif from demo.tape"
```

---

## What the recording shows

Three beats, one continuous terminal session, no cuts:

1. **`swatch init .`** in an empty directory — scaffolds a `swatch.toml`.
2. **`cd ../my-app`** — into a project that already has one (a throwaway copy of `project/`).
3. **`swatch run . --once`** — the engine starts, a background writer simulates an editor
   save, the `tests` trigger matches, `pytest` runs, and the Run reports its verdict.

Verbatim output from a rehearsal of exactly that sequence:

```
  ✓ wrote swatch.toml
  edit it, then run:  swatch run .

  swatch  ·  engine starting

  ✓ config loaded    swatch.toml  ·  1 triggers, 1 workflows
  running once over .  ·  first match, then exit

  01:47:44  ·  tests  → started  r_6cc1
  01:47:45  ·  tests  ✓ succeeded  0.8s

  processed 1 run(s): 1 succeeded, 0 failed, 0 cancelled
```

`pytest`'s own output is absent by design, not by omission: the default view is
**quiet-on-success, loud-on-failure** ([`UI_DESIGN.md`](../docs/UI_DESIGN.md) §4.3). Had a
test failed, the failing step's output tail would be printed under the verdict.

**Why `--once` and not a continuous watch.** `swatch run .` watches until you interrupt it,
and that is the everyday form. But a recording of it has to end with a typed `Ctrl+C`, which
leaves `draining… press Ctrl+C again to force` as the closing frame — a clip that appears to
end in an abort, and loops back to the prompt from a half-shut-down state. With `--once` the
clip ends because the program ended, on the verdict, at exit code 0.

---

## Running the toy project yourself

`project/` is a working example, not just a recording prop. From the repository root:

```bash
uv run swatch run demo/project --once   # one batch, then exit — what the GIF shows
uv run swatch run demo/project          # watch until Ctrl+C — the everyday form
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
