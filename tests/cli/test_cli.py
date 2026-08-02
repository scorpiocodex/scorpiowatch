"""Tests for the ``swatch`` CLI: ``init``, ``run`` wiring, and §7.2 exit-code mapping.

The ``run`` happy/failure/startup paths drive the *real* Engine/Scheduler/Executor (real
subprocesses) through the actual command, substituting only the event *source* with a scripted
one-shot adapter (the ``FilesystemAdapter`` is proven elsewhere) so the tests are deterministic.
The usage/config/abort exit-code remapping in ``main`` is exercised directly.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import typer
from typer.testing import CliRunner

import swatch.cli.commands.run as run_cmd
import swatch.cli.main as main_mod
from swatch.cli.commands.run import _aggregate_exit, _exit_code, _resolve_mode, _ShutdownSignals
from swatch.cli.exit_codes import ExitCode
from swatch.cli.main import app
from swatch.cli.reporter import OutputMode
from swatch.core.config import SwatchConfig
from swatch.core.events import Event
from swatch.core.scheduler import Run
from swatch.core.triggers import GlobMatch, Trigger
from swatch.core.workflow import Step, Workflow
from swatch.execution.executor import Executor, RunContext, RunState


def _config() -> SwatchConfig:
    """A one-trigger config for banner/summary rendering tests."""
    workflow = Workflow(name="wf", steps=[Step(name="s", command=["true"])])
    trigger = Trigger(
        name="t", source="filesystem", match=GlobMatch(pattern="**/*.py"), workflow=workflow
    )
    return SwatchConfig(triggers=[trigger])


runner = CliRunner()


def _make_event(path: str = "x.py") -> Event:
    """A filesystem 'modified' event carrying ``path``."""
    return Event(
        id=uuid4(),
        source="filesystem",
        type="modified",
        payload={"path": path},
        timestamp=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )


def _toml_array(items: list[str]) -> str:
    """Render ``items`` as a TOML array of literal (single-quoted) strings.

    Literal strings avoid escaping — safe for Windows paths and the double-quoted Python
    ``-c`` code embedded below (which contains no single quotes).
    """
    return "[" + ", ".join(f"'{item}'" for item in items) + "]"


def _write_config(tmp_path: Path, argv: list[str], *, name: str = "swatch.toml") -> Path:
    """Write a valid one-trigger config whose single step runs ``argv``."""
    body = (
        "[[trigger]]\n"
        'name = "t"\n'
        'patterns = ["**/*.py"]\n'
        "  [trigger.workflow]\n"
        f"  steps = [{{ command = {_toml_array(argv)} }}]\n"
    )
    target = tmp_path / name
    target.write_text(body, encoding="utf-8")
    return target


class _OneShotSource:
    """A scripted ``SourceAdapter`` that emits one matching event, then idles until stopped."""

    name = "filesystem"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self._stop = asyncio.Event()

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        self._stop.set()

    async def events(self):
        yield _make_event("x.py")
        await self._stop.wait()


class _FailingSource:
    """A scripted ``SourceAdapter`` whose ``start`` fails (drives the startup-error path)."""

    name = "filesystem"

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def start(self) -> None:
        raise OSError("adapter could not initialize")

    async def stop(self) -> None:
        pass

    async def events(self):
        for _ in ():
            yield _make_event()


# --------------------------------------------------------------------------- #
# --help and command surface.                                                  #
# --------------------------------------------------------------------------- #


def test_help_lists_run_and_init() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "init" in result.output


# --------------------------------------------------------------------------- #
# swatch init                                                               #
# --------------------------------------------------------------------------- #


def test_init_scaffolds_a_valid_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    written = tmp_path / "swatch.toml"
    assert written.exists()
    assert "[[trigger]]" in written.read_text(encoding="utf-8")


def test_init_scaffold_loads_back_as_valid_config(tmp_path: Path) -> None:
    from swatch.config.loader import load

    runner.invoke(app, ["init", str(tmp_path)])
    config = load(tmp_path / "swatch.toml")
    # Only the one active, language-neutral trigger loads; the multi-stack examples below it
    # are commented documentation, not active triggers.
    assert len(config.triggers) == 1
    assert config.triggers[0].name == "on-change"
    assert config.triggers[0].match.pattern == "**/*"  # any file, no language assumption


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "swatch.toml"
    target.write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert target.read_text(encoding="utf-8") == "# mine\n"  # untouched


def test_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "swatch.toml"
    target.write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "[[trigger]]" in target.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# swatch run — config errors → exit 2.                                      #
# --------------------------------------------------------------------------- #


def test_run_missing_config_is_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(tmp_path / "nope.toml")])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "config error" in result.output


def test_run_malformed_config_is_exit_2(tmp_path: Path) -> None:
    bad = tmp_path / "swatch.toml"
    bad.write_text('[[trigger]]\nname = "t\n', encoding="utf-8")  # unterminated string
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "config error" in result.output


# --------------------------------------------------------------------------- #
# swatch run --once — the full pipeline, deterministic via a scripted source.
# --------------------------------------------------------------------------- #


def test_run_once_succeeds_exit_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / "ran.txt"
    argv = [sys.executable, "-c", 'import sys; open(sys.argv[1],"w").write("ok")', str(marker)]
    config = _write_config(tmp_path, argv)
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    assert marker.read_text(encoding="utf-8") == "ok"


def test_run_once_failure_is_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    argv = [sys.executable, "-c", "import sys; sys.exit(1)"]
    config = _write_config(tmp_path, argv)
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.WORKFLOW_FAILURE)


def test_run_startup_failure_is_exit_4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(tmp_path, ["true"])
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _FailingSource)
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.STARTUP_ERROR)
    assert "startup error" in result.output


# --------------------------------------------------------------------------- #
# Output modes: flag resolution and the --json / --quiet renderings end to end. #
# --------------------------------------------------------------------------- #


def test_resolve_mode_precedence_and_conflict() -> None:
    assert _resolve_mode(verbose=False, quiet=False, as_json=False) is OutputMode.DEFAULT
    assert _resolve_mode(verbose=True, quiet=False, as_json=False) is OutputMode.VERBOSE
    assert _resolve_mode(verbose=False, quiet=True, as_json=False) is OutputMode.QUIET
    assert _resolve_mode(verbose=True, quiet=True, as_json=True) is OutputMode.JSON  # --json wins
    with pytest.raises(typer.BadParameter):
        _resolve_mode(verbose=True, quiet=True, as_json=False)


def test_run_once_json_streams_lifecycle_and_output_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = [sys.executable, "-c", 'print("hello-from-step")']
    config = _write_config(tmp_path, argv)
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(config), "--once", "--json"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    objs = [json.loads(line) for line in result.output.splitlines() if line.strip().startswith("{")]
    events = [o["event"] for o in objs]
    assert "run.started" in events
    assert "step.output" in events  # the program's own output, streamed as JSON
    assert "run.completed" in events
    assert "run.summary" in events
    assert any("hello-from-step" in o.get("text", "") for o in objs)


def test_run_once_quiet_prints_tally_not_lifecycle_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = [sys.executable, "-c", 'print("noise")']
    config = _write_config(tmp_path, argv)
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(
        app, ["run", str(tmp_path), "--config", str(config), "--once", "--quiet"]
    )
    assert result.exit_code == int(ExitCode.SUCCESS)
    assert "1 succeeded" in result.output  # the verdict tally
    assert "→ started" not in result.output  # no per-event engine lines
    assert "engine starting" not in result.output  # banner suppressed


def test_main_verbose_quiet_conflict_is_exit_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _write_config(tmp_path, ["true"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["swatch", "run", str(tmp_path), "--config", str(config), "--verbose", "--quiet"],
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == int(ExitCode.USAGE_ERROR)


# --------------------------------------------------------------------------- #
# Step cwd defaulting: a config runs in the watched project, not the caller's dir.
# --------------------------------------------------------------------------- #

# A step that records its own working directory into the file given as argv[1]. Double quotes
# only (no single quotes) so it embeds safely in the single-quoted TOML literal `_write_config`
# builds.
_RECORD_CWD = 'import os, sys; open(sys.argv[1], "w").write(os.getcwd())'


def test_run_defaults_unset_step_cwd_to_the_watched_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    recorded = tmp_path / "cwd.txt"
    argv = [sys.executable, "-c", _RECORD_CWD, str(recorded)]
    config = _write_config(proj, argv)  # no cwd in config → defaults to the watched root
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(proj), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    # The step ran in the watched project, not wherever the CLI was invoked.
    assert Path(recorded.read_text(encoding="utf-8")).resolve() == proj.resolve()


def test_run_explicit_cwd_wins_over_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    other = tmp_path / "elsewhere"
    other.mkdir()
    recorded = tmp_path / "cwd.txt"
    argv = [sys.executable, "-c", _RECORD_CWD, str(recorded)]
    config = proj / "swatch.toml"
    config.write_text(
        "[[trigger]]\n"
        'name = "t"\n'
        'patterns = ["**/*.py"]\n'
        "  [trigger.workflow]\n"
        f"  steps = [{{ command = {_toml_array(argv)}, cwd = '{other}' }}]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(proj), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    # The explicit cwd is honored over the watched-root default.
    assert Path(recorded.read_text(encoding="utf-8")).resolve() == other.resolve()


def test_run_resolves_a_relative_watched_root_to_an_absolute_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    recorded = tmp_path / "cwd.txt"
    argv = [sys.executable, "-c", _RECORD_CWD, str(recorded)]
    config = _write_config(proj, argv)
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    monkeypatch.chdir(tmp_path)  # invoke from the parent, watch by relative name
    result = runner.invoke(app, ["run", "proj", "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    # A relative watched root still lands the subprocess in the correct absolute directory.
    assert Path(recorded.read_text(encoding="utf-8")).resolve() == proj.resolve()


def test_run_relative_cwd_resolves_under_the_watched_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The mechanism the scaffold's `cwd = "."` relies on: a relative cwd resolves against the
    # watched root, not the invocation dir.
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sub").mkdir()
    recorded = tmp_path / "cwd.txt"
    argv = [sys.executable, "-c", _RECORD_CWD, str(recorded)]
    config = proj / "swatch.toml"
    config.write_text(
        "[[trigger]]\n"
        'name = "t"\n'
        'patterns = ["**/*.py"]\n'
        "  [trigger.workflow]\n"
        f"  steps = [{{ command = {_toml_array(argv)}, cwd = 'sub' }}]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(proj), "--config", str(config), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    assert Path(recorded.read_text(encoding="utf-8")).resolve() == (proj / "sub").resolve()


def test_init_scaffold_is_language_neutral_and_documents_cwd(tmp_path: Path) -> None:
    runner.invoke(app, ["init", str(tmp_path)])
    written = (tmp_path / "swatch.toml").read_text(encoding="utf-8")
    assert "language-agnostic" in written  # framing: the engine knows nothing about languages
    # Breadth shown as commented examples across stacks, plus per-trigger cwd in the full-stack one.
    for token in ("pytest", "npm", "cargo", "go-build", 'cwd = "frontend"'):
        assert token in written


def test_init_scaffold_active_trigger_runs_on_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fresh init + run does something visible on any project: the active trigger's portable
    # command runs on a matching change through the real Engine/Scheduler/Executor. This is the
    # end-to-end guard on the scaffold's `env_allowlist` (see the block below): on POSIX a
    # scaffold without PATH cannot resolve its own interpreter and this run fails outright.
    runner.invoke(app, ["init", str(tmp_path)])
    monkeypatch.setattr(run_cmd, "FilesystemAdapter", _OneShotSource)
    result = runner.invoke(app, ["run", str(tmp_path), "--once"])
    assert result.exit_code == int(ExitCode.SUCCESS)
    assert "1 succeeded" in result.output  # the active trigger fired and its command ran


# --------------------------------------------------------------------------- #
# The scaffold's env_allowlist — the first-run contract.                       #
# A Step's child starts from a *fully scrubbed* environment (`Executor.run_step`#
# builds it from `env_allowlist` alone), so a scaffold that names nothing hands #
# its subprocess an empty env. On POSIX the program lookup then falls back to   #
# `os.defpath` (/bin:/usr/bin) and an interpreter in a venv, pyenv, or uv       #
# install cannot be found: `swatch init && swatch run` — the very first thing a #
# new user does — dies with "No such file or directory: 'python'". These pin    #
# the starter allowlist that keeps that path working.                           #
# --------------------------------------------------------------------------- #


def _scaffold_steps(tmp_path: Path) -> list[Step]:
    """Scaffold into ``tmp_path`` and return the active (non-commented) Steps it declares."""
    from swatch.config.loader import load

    runner.invoke(app, ["init", str(tmp_path)])
    config = load(tmp_path / "swatch.toml")
    return [step for trigger in config.triggers for step in trigger.workflow.steps]


def _child_env(step: Step) -> dict[str, str]:
    """Build the child environment exactly as ``Executor.run_step`` does."""
    return {name: os.environ[name] for name in step.env_allowlist if name in os.environ}


def test_init_scaffold_names_the_interpreter_lookup_vars(tmp_path: Path) -> None:
    for step in _scaffold_steps(tmp_path):
        # PATH is load-bearing on every platform; the Windows trio is simply absent (and so
        # skipped) elsewhere, which is what lets one starter list work cross-platform.
        assert "PATH" in step.env_allowlist
        assert {"PATHEXT", "SYSTEMROOT", "SystemDrive"} <= set(step.env_allowlist)


def test_scaffold_allowlist_delivers_path_to_a_real_child_process(tmp_path: Path) -> None:
    # Falsifiable on every platform: with the pre-fix empty allowlist the child's environment
    # is empty and it prints nothing.
    (step,) = _scaffold_steps(tmp_path)
    probe = Step(
        name="probe",
        command=[sys.executable, "-c", "import os; print(os.environ.get('PATH', ''))"],
        env_allowlist=step.env_allowlist,
    )
    result = asyncio.run(Executor().run_step(probe, RunContext()))
    assert result.state is RunState.SUCCEEDED
    assert result.stdout.strip(), "the child process was handed no PATH"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="CreateProcess resolves the program from the parent's PATH, so the scrub cannot hide it",
)
def test_scaffold_command_resolves_under_the_scrubbed_child_env(tmp_path: Path) -> None:
    # The reported blocker at its mechanism. POSIX looks the program up in
    # `os.get_exec_path(child_env)`; with an empty allowlist that collapses to `os.defpath`.
    (step,) = _scaffold_steps(tmp_path)
    program = step.command[0]
    if shutil.which(program) is None:
        pytest.skip(f"{program!r} is not on this machine's PATH at all — a separate concern")
    search_path = os.pathsep.join(os.get_exec_path(_child_env(step)))
    assert shutil.which(program, path=search_path) is not None


def test_example_configs_allowlist_path_for_every_step() -> None:
    """The shipped examples must survive the environment scrub too, not just the scaffold."""
    from swatch.config.loader import load

    for example in (
        Path("examples/local-dev/swatch.toml"),
        Path("examples/fullstack/swatch.toml"),
    ):
        steps = [s for t in load(example).triggers for s in t.workflow.steps]
        assert steps, f"{example} declares no steps"
        for step in steps:
            assert "PATH" in step.env_allowlist, f"{example}: step {step.name!r} loses its PATH"


def test_fullstack_example_config_loads_with_both_triggers() -> None:
    from swatch.config.loader import load

    config = load(Path("examples/fullstack/swatch.toml"))
    # frontend expands over its 2 patterns, backend over 1 → 3 core triggers, two languages.
    names = [t.name for t in config.triggers]
    assert names == ["frontend", "frontend", "backend"]
    assert {str(t.workflow.steps[0].cwd) for t in config.triggers} == {"frontend", "backend"}


# --------------------------------------------------------------------------- #
# Exit-code aggregation.                                                       #
# --------------------------------------------------------------------------- #


def _run(state: RunState) -> Run:
    return Run(run_id=uuid4(), trigger_name="t", workflow_name="wf", state=state)


def test_aggregate_exit_maps_states_to_codes() -> None:
    assert _aggregate_exit([]) is ExitCode.SUCCESS
    assert _aggregate_exit([_run(RunState.SUCCEEDED)]) is ExitCode.SUCCESS
    assert _aggregate_exit([_run(RunState.SUCCEEDED), _run(RunState.FAILED)]) is (
        ExitCode.WORKFLOW_FAILURE
    )
    assert _aggregate_exit([_run(RunState.TIMED_OUT)]) is ExitCode.WORKFLOW_FAILURE


def test_exit_code_prefers_sigint_over_aggregate() -> None:
    # A received signal makes the run's exit 130 regardless of the runs' states.
    assert _exit_code(1, [_run(RunState.SUCCEEDED)]) == int(ExitCode.SIGINT)
    assert _exit_code(0, []) == int(ExitCode.SUCCESS)
    assert _exit_code(0, [_run(RunState.FAILED)]) == int(ExitCode.WORKFLOW_FAILURE)


# --------------------------------------------------------------------------- #
# Signal handling: two-stage shutdown (§7.1).                                  #
# --------------------------------------------------------------------------- #


class _RecordingEngine:
    """A stand-in Engine that records its shutdown calls (drain vs. force)."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def shutdown(self, *, force: bool = False) -> None:
        self.calls.append(force)


async def test_shutdown_signals_drive_two_stage_shutdown() -> None:
    engine = _RecordingEngine()
    signals = _ShutdownSignals(engine)  # type: ignore[arg-type]
    signals.on_signal()  # first → graceful drain
    signals.on_signal()  # second → force
    await asyncio.sleep(0.05)  # let the scheduled shutdown coroutines run
    assert signals.count == 2
    assert engine.calls == [False, True]


# --------------------------------------------------------------------------- #
# Real OS SIGINT reaching a live `swatch run` process (§7.1).               #
#                                                                              #
# The tests above prove the two-stage *decision* (`on_signal` → drain/force) and #
# the engine's drain/force *behavior* in-process. This one closes the last seam: #
# an actual SIGINT, delivered by the OS to a real `swatch run` subprocess with #
# a run in flight, must drive the graceful drain and exit 130 — the seam the     #
# daemon's SIGTERM handling (v1.1.0) will build on. Signal delivery is POSIX-     #
# specific (Windows consoles use CTRL_C_EVENT semantics), so it skips there.     #
# --------------------------------------------------------------------------- #


# Single-line so it embeds as a TOML literal; double-quotes only so the single-quoted
# literal in `_write_config` stays intact. Writes a start marker (proving the run is in
# flight) before a short sleep, then a done marker (proving it *drained to completion*,
# i.e. graceful — a forced cancel would kill it mid-sleep and the done marker never lands).
_DRAIN_STEP_CODE = (
    "import sys, time; "
    'open(sys.argv[1], "w").write("in-flight"); '
    "time.sleep(0.6); "
    'open(sys.argv[2], "w").write("drained")'
)


@pytest.mark.filesystem
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="real SIGINT delivery is POSIX-specific; Windows console CTRL_C_EVENT semantics "
    "are the daemon's concern (v1.1.0), not this seam",
)
def test_real_sigint_drains_gracefully_and_exits_130(tmp_path: Path) -> None:
    watchdir = tmp_path / "watch"
    watchdir.mkdir()
    start_marker = tmp_path / "step-started.txt"
    done_marker = tmp_path / "step-drained.txt"
    argv = [sys.executable, "-c", _DRAIN_STEP_CODE, str(start_marker), str(done_marker)]
    config = _write_config(tmp_path, argv)  # config lives outside watchdir, so it stirs no events

    out_log = tmp_path / "run.out"
    err_log = tmp_path / "run.err"
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    command = [
        sys.executable,
        "-m",
        "swatch.cli.main",
        "run",
        str(watchdir),
        "--config",
        str(config),
    ]
    with out_log.open("wb") as out, err_log.open("wb") as err:
        proc = subprocess.Popen(command, stdout=out, stderr=err, env=env)
        try:
            # Drive the watch until a run is demonstrably in flight: touch a fresh matching file
            # each tick until the step writes its start marker. Gating on the marker (not on a
            # log line or a fixed sleep) closes two races at once — the watch may not be armed
            # for the first touch, and the signal handlers are installed strictly before a run
            # can start, so a written start marker proves the engine is fully armed for SIGINT.
            deadline = time.monotonic() + 25
            tick = 0
            while not start_marker.exists():
                if proc.poll() is not None:
                    raise AssertionError(
                        f"swatch exited early (code {proc.returncode}) before any run started"
                        f"\n--- stderr ---\n{err_log.read_text(errors='replace')}"
                    )
                if time.monotonic() > deadline:
                    raise AssertionError(
                        "no run went in flight before the deadline"
                        f"\n--- stderr ---\n{err_log.read_text(errors='replace')}"
                    )
                (watchdir / f"change-{tick}.py").write_text("x = 1\n", encoding="utf-8")
                tick += 1
                time.sleep(0.5)

            # A run is in flight (mid-sleep); deliver a real interrupt and let the engine drain.
            proc.send_signal(signal.SIGINT)
            returncode = proc.wait(timeout=15)
        finally:
            if proc.poll() is None:  # never leave a watcher behind if an assertion fired
                proc.kill()
                proc.wait(timeout=5)

    stderr_text = err_log.read_text(errors="replace")
    assert returncode == int(ExitCode.SIGINT), (
        f"expected 130, got {returncode}\n--- stderr ---\n{stderr_text}"
    )
    # Graceful, not forced: the in-flight run ran to its terminal state during the drain.
    assert done_marker.exists(), (
        f"the in-flight run did not drain to completion\n--- stderr ---\n{stderr_text}"
    )
    # A clean drain, not a crash: no interpreter traceback escaped (e.g. an uncaught
    # KeyboardInterrupt from a SIGINT that beat the handler install).
    assert "Traceback (most recent call last)" not in stderr_text


# Banner / summary rendering moved to the RunReporter; see tests/cli/test_reporter.py.


# --------------------------------------------------------------------------- #
# main() exit-code remapping (non-standalone click).                          #
# --------------------------------------------------------------------------- #


def test_main_remaps_usage_error_to_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A non-existent PATH fails typer's `exists=True` → click UsageError (default code 2);
    # main() remaps it to 3 so 2 stays reserved for config errors (§7.2).
    monkeypatch.setattr(sys, "argv", ["swatch", "run", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == int(ExitCode.USAGE_ERROR)


def test_main_propagates_config_error_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # typer.Exit(2) from the run command surfaces through non-standalone click as the code.
    monkeypatch.setattr(
        sys, "argv", ["swatch", "run", str(tmp_path), "--config", str(tmp_path / "no.toml")]
    )
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == int(ExitCode.CONFIG_ERROR)


def test_main_maps_abort_to_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def _abort(**_kwargs: object) -> object:
        raise typer.Abort()  # the vendored click Abort that main() catches

    monkeypatch.setattr(main_mod, "app", _abort)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == int(ExitCode.SIGINT)


def test_main_success_does_not_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A successful command returns None in non-standalone mode; main() falls through (exit 0).
    monkeypatch.setattr(sys, "argv", ["swatch", "init", str(tmp_path)])
    assert main_mod.main() is None
    assert (tmp_path / "swatch.toml").exists()


# --------------------------------------------------------------------------- #
# UTF-8 stdio reconfiguration (portable Unicode chrome, Article II).           #
# --------------------------------------------------------------------------- #


class _FakeStream:
    """A text stream that records the ``reconfigure`` call main() makes on stdout/stderr."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self._fail = fail

    def reconfigure(self, *, encoding: str, errors: str) -> None:
        if self._fail:
            raise ValueError("stream cannot be reconfigured")
        self.calls.append((encoding, errors))


def test_reconfigure_utf8_sets_encoding() -> None:
    stream = _FakeStream()
    main_mod._reconfigure_utf8(stream)
    assert stream.calls == [("utf-8", "replace")]


def test_reconfigure_utf8_ignores_stream_without_reconfigure() -> None:
    # A plain object has no ``reconfigure``; the call must be a silent no-op, not an error.
    main_mod._reconfigure_utf8(object())


def test_reconfigure_utf8_swallows_reconfigure_errors() -> None:
    # A stream whose reconfigure fails (already wrapped/redirected) must not crash the CLI.
    stream = _FakeStream(fail=True)
    main_mod._reconfigure_utf8(stream)
    assert stream.calls == []
