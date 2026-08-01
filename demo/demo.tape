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
#   4. a simulated save fires a real Run      -> green verdict
#   5. Ctrl+C                                 -> graceful drain, then the run tally
#
# Nothing else. Every export, temp copy, and the writer that stages the save lives in
# demo/setup.sh, sourced below while the camera is off — and the screen is cleared, on a
# confirmed-idle shell, before recording resumes. See the Hidden setup block for why that
# ordering is the entire trick.
#
# ── TIMING ──────────────────────────────────────────────────────────────────────────────
# There is no timing race here to get wrong. The writer in setup.sh does not wait for a
# DURATION before saving — it waits for the watcher's own backend thread to appear, which
# happens exactly when watching begins. Nothing below can shift that: retype the commands,
# change the sleeps, run it on a slower machine, and the save still lands after the watcher
# is armed, because it is gated on the watcher rather than on the clock.
#
# The `Wait+Screen` guards then hold each beat until its output is really on screen, so a
# slow machine stretches the clip rather than truncating it.

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
# `Hide` stops vhs CAPTURING frames. It does not stop the terminal from existing, so
# whatever is left on screen when `Show` resumes IS recorded. Getting a clean opening frame
# is therefore not about hiding the setup — it is about the screen being genuinely empty at
# the moment Show happens.
#
# The previous version guessed that with `Sleep 3s`. When setup ran even slightly long, the
# next keystroke arrived while the shell was still busy and readline never interpreted it:
# `Ctrl+L` echoed as a literal `^L` rather than clearing, and Show fired over a screen still
# showing the `source` line. That is a race, and it is why this now waits for a fact.
#
# setup.sh prints SWATCH_DEMO_READY as its final act, so this Wait cannot pass until every
# statement in it has run. The `clear` that follows is therefore guaranteed to arrive at an
# idle shell, where readline will interpret it rather than echo it. Nothing here waits for a
# duration, so nothing here can be outrun by a slow machine.
#
# (Two options were checked first and are unavailable in vhs v0.11.0: `Set Shell "bash
# --rcfile ..."` is rejected as an invalid shell, and while `Env HOME ...` is accepted, vhs
# starts bash with rc files disabled — hence its bare `> ` default prompt — so no startup
# file of any kind is read. There is no pre-record hook to run setup in.)
Hide
Type "source demo/setup.sh"
Enter
Wait+Screen /SWATCH_DEMO_READY/
Type "clear"
Enter
Sleep 1s
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

# ── 4. The watcher arms, which is what releases the save ────────────────────────────────
# Held until `1 triggers armed` is genuinely on screen. This guard is also the honest place
# to fail: if the engine never arms, the recording stops here rather than waiting on a run
# that was never going to happen.
Wait+Screen /triggers armed/

# The save fires now — gated on the watcher, not on this script. Hold for the verdict.
Wait+Screen /succeeded/
Sleep 1800ms

# ── 5. Graceful shutdown ────────────────────────────────────────────────────────────────
# One Ctrl+C drains in-flight Runs and prints the tally; a second would force-cancel.
Ctrl+C
Wait+Screen /processed/
Sleep 2s
