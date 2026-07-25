"""Behavioral tests for the v0.1.0 TriggerEngine: boolean glob matching (no scoring)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from watchflow.core.events import Event
from watchflow.core.triggers import (
    CronExprMatch,
    GlobMatch,
    McpToolNameMatch,
    PredicateMatch,
    Trigger,
    TriggerEngine,
)
from watchflow.core.workflow import Workflow


def make_event(path: str | None = "src/api.py", *, source: str = "filesystem") -> Event:
    """Build an Event, optionally omitting the ``path`` key from the payload."""
    payload: dict[str, object] = {} if path is None else {"path": path}
    return Event(
        id=uuid4(),
        source=source,
        type="modified",
        payload=payload,
        timestamp=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    )


def make_trigger(
    pattern: str, *, name: str = "run-tests", source: str = "filesystem"
) -> Trigger:
    """Build a glob Trigger with a trivial Workflow."""
    return Trigger(
        name=name,
        source=source,
        match=GlobMatch(pattern=pattern),
        workflow=Workflow(name=f"{name}-wf"),
    )


# --------------------------------------------------------------------------- #
# GlobMatch semantics.                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("**/*.py", "src/api.py", True),
        ("**/*.py", "api.py", True),  # ** spans zero directories
        ("**/*.py", "src/deep/api.py", True),
        ("**/*.py", "/home/u/proj/api.py", True),  # absolute path
        ("**/*.py", "src/api.txt", False),
        ("*.py", "api.py", True),
        ("*.py", "src/api.py", False),  # * does not cross a separator
        ("**/test_*.py", "tests/test_api.py", True),
        ("src/**/*.py", "src/a/b/c.py", True),
        ("src/**/*.py", "other/a.py", False),
        ("data?.csv", "data1.csv", True),
        ("data?.csv", "data12.csv", False),
    ],
)
def test_glob_match_semantics(pattern: str, path: str, *, expected: bool) -> None:
    assert GlobMatch(pattern=pattern).matches(make_event(path)) is expected


def test_glob_normalizes_backslashes_cross_platform() -> None:
    # A Windows-style path matches a forward-slash pattern (Article II parity).
    event = make_event(r"C:\Users\dev\proj\api.py")
    assert GlobMatch(pattern="**/*.py").matches(event) is True


def test_glob_does_not_match_event_without_a_path() -> None:
    assert GlobMatch(pattern="**/*.py").matches(make_event(None)) is False


def test_glob_does_not_match_non_string_path() -> None:
    event = make_event(None)
    event.payload["path"] = 123  # payload is Any-typed; a non-str path must not match
    assert GlobMatch(pattern="**/*.py").matches(event) is False


# --------------------------------------------------------------------------- #
# TriggerEngine registry matching.                                             #
# --------------------------------------------------------------------------- #


def test_empty_registry_matches_nothing() -> None:
    assert TriggerEngine().matching_triggers(make_event()) == []


def test_single_trigger_hit() -> None:
    engine = TriggerEngine()
    trigger = make_trigger("**/*.py")
    engine.register(trigger)
    assert engine.matching_triggers(make_event("src/api.py")) == [trigger]


def test_single_trigger_miss() -> None:
    engine = TriggerEngine()
    engine.register(make_trigger("**/*.py"))
    assert engine.matching_triggers(make_event("README.md")) == []


def test_multiple_triggers_match_one_event_in_registration_order() -> None:
    engine = TriggerEngine()
    py = make_trigger("**/*.py", name="py")
    everything = make_trigger("**/*", name="all")
    js = make_trigger("**/*.js", name="js")
    for trigger in (py, everything, js):
        engine.register(trigger)
    assert engine.matching_triggers(make_event("src/api.py")) == [py, everything]


def test_source_gating_excludes_other_adapters() -> None:
    engine = TriggerEngine()
    engine.register(make_trigger("**/*.py", source="cron"))  # wrong source
    # The event is from "filesystem"; the cron-sourced trigger must not be considered.
    assert engine.matching_triggers(make_event("src/api.py", source="filesystem")) == []


def test_register_and_triggers_property() -> None:
    engine = TriggerEngine()
    first = make_trigger("**/*.py", name="a")
    second = make_trigger("**/*.js", name="b")
    engine.register(first)
    engine.register(second)
    assert engine.triggers == (first, second)


# --------------------------------------------------------------------------- #
# Stubbed MatchSpec variants raise NotImplementedError.                        #
# --------------------------------------------------------------------------- #


def test_cron_expr_match_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="cron_expr"):
        CronExprMatch(expr="* * * * *").matches(make_event())


def test_predicate_match_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="predicate"):
        PredicateMatch(predicate="is_big").matches(make_event())


def test_mcp_tool_name_match_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="mcp_tool_name"):
        McpToolNameMatch(tool_name="deploy").matches(make_event())


def test_trigger_accepts_glob_matchspec_via_discriminator() -> None:
    trigger = Trigger(
        name="t",
        source="filesystem",
        match={"kind": "glob", "pattern": "**/*.py"},
        workflow=Workflow(name="wf"),
    )
    assert isinstance(trigger.match, GlobMatch)
    assert trigger.match.pattern == "**/*.py"


# --------------------------------------------------------------------------- #
# Property tests: glob soundness across many inputs.                           #
# --------------------------------------------------------------------------- #

_PATHISH = st.text(alphabet="abcXYZ0/._-", min_size=0, max_size=40)


@given(path=_PATHISH)
def test_prop_double_star_matches_every_path(path: str) -> None:
    # `**` is the catch-all: it matches any path-like string, always.
    assert GlobMatch(pattern="**").matches(make_event(path)) is True


@given(path=st.text(alphabet="abcXYZ0/._-", min_size=1, max_size=30))
def test_prop_literal_pattern_matches_exactly_itself(path: str) -> None:
    # A wildcard-free pattern matches the identical path and nothing structurally longer.
    assert GlobMatch(pattern=path).matches(make_event(path)) is True
    assert GlobMatch(pattern=path).matches(make_event(path + "x")) is False
