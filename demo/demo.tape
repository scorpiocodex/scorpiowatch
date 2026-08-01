# demo.tape — the animated terminal demo embedded in the project README.
#
# A Charm VHS script (https://github.com/charmbracelet/vhs). Everything it records is a real
# ScorpioWatch run against the real toy project in demo/project — no faked terminal text.
#
#   Render:  vhs demo/demo.tape      (run from the REPOSITORY ROOT, not from demo/)
#   Output:  demo/demo.gif
#
# Prerequisites and the full render walkthrough live in demo/README.md. Two of them decide
# whether this works at all: JetBrains Mono must be installed in the RENDER environment
# (vhs silently falls back to a broken-metrics font otherwise), and the repository must sit
# on a native Linux filesystem, not a /mnt/c mount.
#
# ── WHAT IS RECORDED ────────────────────────────────────────────────────────────────────
#   1. `swatch init .` in an empty directory  -> scaffolds swatch.toml
#   2. `cd` into the toy project
#   3. `swatch run .`                         -> starts watching, continuously
#   4. a simulated save fires a real pytest Run -> green verdict
#   5. Ctrl+C                                 -> graceful drain, then the run tally
#
# Nothing else. Every export, temp copy, and the background writer that stages the save
# lives in demo/setup.sh, sourced below while the camera is off — a viewer who saw the save
# being scheduled would rightly conclude the demo was rigged.
#
# ── TIMING ──────────────────────────────────────────────────────────────────────────────
# The writer in setup.sh does NOT fire at a fixed wall-clock offset. It blocks until the
# `swatch run` process exists, then dwells 5s. So the save is anchored to the engine actually
# starting, and stays correctly placed regardless of how long setup took or how fast vhs
# types. The `Wait+Screen` guards below then hold each beat until its output is really on
# screen, so a slow machine stretches the clip rather than truncating it.
#
# The dwell is deliberately ~5x the measured worst-case engine boot (1.01s cold, under load).
# A save landing before the watcher is armed is not late but LOST — under WSL the watcher
# polls, and a write before its first snapshot is baseline, not a change — after which this
# recording waits for a run that can never happen. Cheap insurance; see demo/setup.sh.
#
# ONE save, not two. A second save only earns its ~5s if the first run is quick, and it
# made the recording fragile: the guard had to match two verdicts, so anything that delayed
# or swallowed the second run failed the whole render rather than merely shortening it. The
# loop is already legible from one run plus the `^C to stop` hint and the closing tally.

Output demo/demo.gif

# ── Terminal ────────────────────────────────────────────────────────────────────────────
Set Shell bash
Set FontFamily "JetBrains Mono"
Set FontSize 20
Set Width 1200
Set Height 700
Set Padding 24
Set Margin 26
Set MarginFill "#0F1219"
Set BorderRadius 10
Set WindowBar Colorful
Set WindowBarSize 40
Set Framerate 20
Set TypingSpeed 50ms
Set WaitTimeout 120s

# The UI_DESIGN.md §2.1 palette mapped onto the 16 ANSI slots the CLI's rich.Theme uses:
# green = success, red = failure, yellow = warning, blue = run IDs, brightYellow = the amber
# brand accent (cursor + prompt), brightBlack = the muted/dim secondary text.
Set Theme { "name": "ScorpioWatch", "background": "#0B0D12", "foreground": "#C8CCD4", "cursor": "#E8B847", "cursorAccent": "#0B0D12", "selection": "#1E2433", "black": "#0B0D12", "red": "#E24B4A", "green": "#5DCAA5", "yellow": "#EF9F27", "blue": "#85B7EB", "magenta": "#C89A30", "cyan": "#85B7EB", "white": "#C8CCD4", "brightBlack": "#5A5F69", "brightRed": "#E24B4A", "brightGreen": "#5DCAA5", "brightYellow": "#E8B847", "brightBlue": "#85B7EB", "brightMagenta": "#C89A30", "brightCyan": "#85B7EB", "brightWhite": "#FFFFFF" }

# ── Hidden setup ────────────────────────────────────────────────────────────────────────
# One line, and it clears the screen itself as its final act. That is the whole point of
# routing setup through a script: even in the worst case — Hide failing outright — the most
# that can reach the GIF is this single `source` line, erased a moment later.
Hide
Type "source demo/setup.sh"
Enter
Sleep 4s
Show

# ── 1. Scaffold a config ────────────────────────────────────────────────────────────────
Sleep 800ms
Type "swatch init ."
Sleep 350ms
Enter
Wait+Screen /wrote swatch.toml/
Sleep 1500ms

# ── 2. Move to a project that already has one ───────────────────────────────────────────
Type "cd ../my-app"
Sleep 300ms
Enter
Sleep 700ms

# ── 3. Start watching — the everyday form, no flags ─────────────────────────────────────
Type "swatch run ."
Sleep 350ms
Enter

# ── 4. The save lands here and fires a real pytest Run ──────────────────────────────────
# Held open until the verdict is genuinely on screen. A slow machine stretches this beat;
# it cannot truncate it.
Wait+Screen /succeeded/
Sleep 1800ms

# ── 5. Graceful shutdown ────────────────────────────────────────────────────────────────
# One Ctrl+C drains in-flight Runs and prints the tally; a second would force-cancel.
Ctrl+C
Wait+Screen /processed/
Sleep 2s
