"""Tests for the ``watchflow`` CLI: ``init``, ``run`` wiring, and §7.2 exit-code mapping.

The ``run`` happy/failure/startup paths drive the *real* Engine/Scheduler/Executor (real
subprocesses) through the actual command, substituting only the event *source* with a scripted
one-shot adapter (the ``FilesystemAdapter`` is proven elsewhere) so the tests are deterministic.
The usage/config/abort exit-code remapping in ``main`` is exercised directly.
"""

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import typer
from typer.testing import CliRunner

import watchflow.cli.commands.run as run_cmd
import watchflow.cli.main as main_mod
from watchflow.cli.commands.run import _aggregate_exit, _exit_code, _ShutdownSignals
from watchflow.cli.exit_codes import ExitCode
from watchflow.cli.main import app
from watchflow.core.config import WatchflowConfig
from watchflow.core.events import Event
from watchflow.core.scheduler import Run
from watchflow.core.triggers import GlobMatch, Trigger
from watchflow.core.workflow import Step, Workflow
from watchflow.execution.executor import RunState


def _config() -> WatchflowConfig:
    """A one-trigger config for banner/summary rendering tests."""
    workflow = Workflow(name="wf", steps=[Step(name="s", command=["true"])])
    trigger = Trigger(
        name="t", source="filesystem", match=GlobMatch(pattern="**/*.py"), workflow=workflow
    )
    return WatchflowConfig(triggers=[trigger])


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


def _write_config(tmp_path: Path, argv: list[str], *, name: str = "watchflow.toml") -> Path:
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
# watchflow init                                                               #
# --------------------------------------------------------------------------- #


def test_init_scaffolds_a_valid_config(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0
    written = tmp_path / "watchflow.toml"
    assert written.exists()
    assert "[[trigger]]" in written.read_text(encoding="utf-8")


def test_init_scaffold_loads_back_as_valid_config(tmp_path: Path) -> None:
    from watchflow.config.loader import load

    runner.invoke(app, ["init", str(tmp_path)])
    config = load(tmp_path / "watchflow.toml")
    assert len(config.triggers) == 1
    assert config.triggers[0].name == "run-tests"


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    target = tmp_path / "watchflow.toml"
    target.write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert target.read_text(encoding="utf-8") == "# mine\n"  # untouched


def test_init_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "watchflow.toml"
    target.write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "[[trigger]]" in target.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# watchflow run — config errors → exit 2.                                      #
# --------------------------------------------------------------------------- #


def test_run_missing_config_is_exit_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", str(tmp_path), "--config", str(tmp_path / "nope.toml")])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "config error" in result.output


def test_run_malformed_config_is_exit_2(tmp_path: Path) -> None:
    bad = tmp_path / "watchflow.toml"
    bad.write_text('[[trigger]]\nname = "t\n', encoding="utf-8")  # unterminated string
    result = runner.invoke(app, ["run", str(tmp_path)])
    assert result.exit_code == int(ExitCode.CONFIG_ERROR)
    assert "config error" in result.output


# --------------------------------------------------------------------------- #
# watchflow run --once — the full pipeline, deterministic via a scripted source.
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
# Banner / summary rendering.                                                  #
# --------------------------------------------------------------------------- #


def test_banner_renders_both_modes(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _config()
    run_cmd._print_banner(cfg, Path("proj"), Path("proj/watchflow.toml"), once=False)
    assert "watching" in capsys.readouterr().out
    run_cmd._print_banner(cfg, Path("proj"), Path("proj/watchflow.toml"), once=True)
    assert "running once" in capsys.readouterr().out


def test_summary_renders_empty_and_nonempty(capsys: pytest.CaptureFixture[str]) -> None:
    run_cmd._print_summary([])
    assert "no runs" in capsys.readouterr().out
    run_cmd._print_summary([_run(RunState.SUCCEEDED)])
    assert "1 succeeded" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# main() exit-code remapping (non-standalone click).                          #
# --------------------------------------------------------------------------- #


def test_main_remaps_usage_error_to_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A non-existent PATH fails typer's `exists=True` → click UsageError (default code 2);
    # main() remaps it to 3 so 2 stays reserved for config errors (§7.2).
    monkeypatch.setattr(sys, "argv", ["watchflow", "run", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == int(ExitCode.USAGE_ERROR)


def test_main_propagates_config_error_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # typer.Exit(2) from the run command surfaces through non-standalone click as the code.
    monkeypatch.setattr(
        sys, "argv", ["watchflow", "run", str(tmp_path), "--config", str(tmp_path / "no.toml")]
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
    monkeypatch.setattr(sys, "argv", ["watchflow", "init", str(tmp_path)])
    assert main_mod.main() is None
    assert (tmp_path / "watchflow.toml").exists()


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
