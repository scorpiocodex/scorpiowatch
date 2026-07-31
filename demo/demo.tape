# demo.tape — the animated terminal demo embedded in the project README.
#
# A Charm VHS script (https://github.com/charmbracelet/vhs). Everything it records is a real
# ScorpioWatch run against the real toy project in demo/project — no faked terminal text.
#
#   Render:  vhs demo/demo.tape      (run from the REPOSITORY ROOT, not from demo/)
#   Output:  demo/demo.gif
#
# Prerequisites and the full render walkthrough live in demo/README.md. Requires vhs >= 0.7
# (the `Wait` command) and a synced virtualenv at ./.venv.
#
# Shape of the recording (~18s, loops cleanly):
#   1. `swatch init .` in an empty directory  -> scaffolds swatch.toml
#   2. `cd` into the toy project
#   3. `swatch run . --once`                  -> a real file change fires a real pytest Run
#
# The file change comes from a background writer started in the hidden setup block. It does
# not fire once at a guessed moment — it writes every second for 20 seconds, so the Run is
# triggered by whichever write first lands after the watcher arms. That makes the recording
# immune to a slow interpreter start rather than racing it.

Output demo/demo.gif

# ── Terminal ────────────────────────────────────────────────────────────────────────────
Set Shell bash
Set FontFamily "JetBrains Mono"
Set FontSize 18
Set Width 960
Set Height 560
Set Padding 22
Set Margin 24
Set MarginFill "#0F1219"
Set BorderRadius 10
Set WindowBar Colorful
Set WindowBarSize 40
Set Framerate 20
Set TypingSpeed 60ms
Set WaitTimeout 60s

# The UI_DESIGN.md §2.1 palette mapped onto the 16 ANSI slots the CLI's rich.Theme uses:
# green = success, red = failure, yellow = warning, blue = run IDs, brightYellow = the amber
# brand accent (cursor + prompt), brightBlack = the muted/dim secondary text.
Set Theme { "name": "ScorpioWatch", "background": "#0B0D12", "foreground": "#C8CCD4", "cursor": "#E8B847", "cursorAccent": "#0B0D12", "selection": "#1E2433", "black": "#0B0D12", "red": "#E24B4A", "green": "#5DCAA5", "yellow": "#EF9F27", "blue": "#85B7EB", "magenta": "#C89A30", "cyan": "#85B7EB", "white": "#C8CCD4", "brightBlack": "#5A5F69", "brightRed": "#E24B4A", "brightGreen": "#5DCAA5", "brightYellow": "#E8B847", "brightBlue": "#85B7EB", "brightMagenta": "#C89A30", "brightCyan": "#85B7EB", "brightWhite": "#FFFFFF" }

# ── Hidden setup ────────────────────────────────────────────────────────────────────────
# None of this is recorded. It types at 1ms so setup costs ~1s of render time rather than
# ~20s, and ends with `clear` so the visible recording opens on a clean prompt.
Hide

# The repository root — vhs starts the shell wherever it was invoked.
Type@1ms 'REPO="$PWD"' Enter

# Put the project virtualenv first on PATH, so the visible commands read as a plain
# `swatch ...` rather than `uv run swatch ...`. Both layouts are prepended: POSIX
# virtualenvs use bin/, Windows ones Scripts/; only the one that exists matters. This is
# also the PATH the toy project's `env_allowlist` forwards to pytest, so the Run resolves
# this same environment.
Type@1ms 'export PATH="$REPO/.venv/bin:$REPO/.venv/Scripts:$PATH"' Enter

# An amber ❯ (the #E8B847 accent, via brightYellow) after a dim directory name.
Type@1ms "export PS1='\[\e[90m\]\W\[\e[0m\] \[\e[93m\]❯\[\e[0m\] '" Enter

# Record against throwaway copies, never the tracked files: `swatch init` would otherwise
# overwrite demo/project/swatch.toml, and the simulated edit would leave app.py dirty.
Type@1ms 'WORK="$(mktemp -d)"' Enter
Type@1ms 'mkdir -p "$WORK/hello"' Enter
Type@1ms 'cp -r "$REPO/demo/project" "$WORK/my-app"' Enter

# Warm the interpreter so the first *visible* command is not also paying import cost.
Type@1ms 'swatch --help >/dev/null 2>&1' Enter

# The simulated editor save: append a newline to app.py once a second. Absolute path — a
# background subshell keeps the directory it was forked in, which is not where the Run
# happens. `disown` stops bash printing "[1]+ Done" over the recording when it finishes.
Type@1ms '( sleep 12; for _ in {1..20}; do echo >> "$WORK/my-app/app.py"; sleep 1; done ) >/dev/null 2>&1 & disown' Enter

Type@1ms 'cd "$WORK/hello"' Enter
Type@1ms 'clear' Enter
Show

# ── 1. Scaffold a config ────────────────────────────────────────────────────────────────
Sleep 1s
Type "swatch init ."
Sleep 400ms
Enter
Sleep 2500ms

# ── 2. Move to a project that already has one ───────────────────────────────────────────
Type "cd ../my-app"
Sleep 300ms
Enter
Sleep 800ms

# ── 3. Watch it react to a real file change ─────────────────────────────────────────────
# `--once` rather than a continuous watch: the clip then ends because the program ended,
# not because a signal was typed at it, which is what lets the GIF loop cleanly. The
# everyday form is `swatch run .`, which watches until Ctrl+C.
Type "swatch run . --once"
Sleep 400ms
Enter

# Hold until the verdict is actually on screen, however long the trigger took to land.
Wait+Screen /succeeded/
Sleep 2500ms
