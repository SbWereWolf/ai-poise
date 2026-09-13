from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _api():
    try:
        return importlib.import_module("poise.modules.ownership.domain")
    except ModuleNotFoundError:
        pytest.fail("the WorkOwnership domain boundary is not implemented")


def _state(api, owners, requirements, liveness):
    return api.OwnershipState(
        owners=owners,
        worktree_required=requirements,
        liveness=liveness,
    )


def test_four_combinations_and_task_type_dependency():
    api = _api()

    assert api.OwnershipSet.empty() == api.OwnershipSet(None, None)
    assert api.OwnershipSet.for_task("DOC", worktree_required=False) == api.OwnershipSet(
        "DOC", None
    )
    assert api.OwnershipSet.for_worktree("WT") == api.OwnershipSet(None, "WT")
    assert api.OwnershipSet.for_task("DEV", worktree_required=True) == api.OwnershipSet(
        "DEV", "DEV"
    )


def test_complete_set_acquisition_replaces_same_kind_atomically():
    api = _api()
    state = _state(
        api,
        {"executor": api.OwnershipSet("OLD", "OLD")},
        {"OLD": True, "NEW": True},
        {"executor": api.Liveness.LIVE},
    )

    change = state.acquire(
        "executor", api.OwnershipSet.for_task("NEW", worktree_required=True)
    )

    assert change.before == api.OwnershipSet("OLD", "OLD")
    assert change.after == api.OwnershipSet("NEW", "NEW")
    assert change.released == api.OwnershipSet("OLD", "OLD")
    assert change.acquired == api.OwnershipSet("NEW", "NEW")
    assert change.state.owner("OLD", kind="task") is None
    assert change.state.owner("OLD", kind="worktree") is None
    assert change.state.owner("NEW", kind="task") == "executor"
    assert change.state.owner("NEW", kind="worktree") == "executor"


def test_self_acquisition_is_idempotent_and_live_owner_is_rejected():
    api = _api()
    owned = api.OwnershipSet.for_task("T", worktree_required=True)
    state = _state(
        api,
        {"executor": owned, "reviewer": api.OwnershipSet.empty()},
        {"T": True},
        {"executor": api.Liveness.LIVE, "reviewer": api.Liveness.LIVE},
    )

    replay = state.acquire("executor", owned)
    assert replay.state is state
    assert replay.released == api.OwnershipSet.empty()
    assert replay.acquired == api.OwnershipSet.empty()

    with pytest.raises(api.OwnershipConflict, match="live.*executor|executor.*live"):
        state.acquire("reviewer", owned)


def test_partial_acquisition_rolls_back():
    api = _api()
    before = _state(
        api,
        {
            "executor": api.OwnershipSet.for_task("NEW", worktree_required=False),
            "reviewer": api.OwnershipSet.for_worktree("NEW"),
        },
        {"NEW": True},
        {"executor": api.Liveness.LIVE, "reviewer": api.Liveness.LIVE},
    )

    with pytest.raises(api.OwnershipConflict, match="worktree.*reviewer|reviewer.*worktree"):
        before.acquire(
            "executor", api.OwnershipSet.for_task("NEW", worktree_required=True)
        )

    assert before.owner("NEW", kind="task") == "executor"
    assert before.owner("NEW", kind="worktree") == "reviewer"
    assert before.owners["executor"] == api.OwnershipSet("NEW", None)


def test_release_couples_required_worktree_but_preserves_independent():
    api = _api()
    dependent = _state(
        api,
        {"executor": api.OwnershipSet("DEV", "DEV")},
        {"DEV": True},
        {"executor": api.Liveness.LIVE},
    )
    released = dependent.release_task("executor", "DEV")
    assert released.after == api.OwnershipSet.empty()
    assert released.released == api.OwnershipSet("DEV", "DEV")

    independent = _state(
        api,
        {"executor": api.OwnershipSet("DOC", "WT")},
        {"DOC": False},
        {"executor": api.Liveness.LIVE},
    )
    preserved = independent.release_task("executor", "DOC")
    assert preserved.after == api.OwnershipSet.for_worktree("WT")
    assert preserved.released == api.OwnershipSet("DOC", None)


def test_dead_owner_recovery_requires_definitive_liveness():
    api = _api()
    target = api.OwnershipSet.for_task("T", worktree_required=True)
    dead = _state(
        api,
        {"old": target, "new": api.OwnershipSet.empty()},
        {"T": True},
        {"old": api.Liveness.DEAD, "new": api.Liveness.LIVE},
    )
    recovered = dead.acquire("new", target)
    assert recovered.recovered_sessions == ("old",)
    assert recovered.state.owners["old"] == api.OwnershipSet.empty()
    assert recovered.state.owners["new"] == target

    uncertain = _state(
        api,
        {"old": target, "new": api.OwnershipSet.empty()},
        {"T": True},
        {"old": api.Liveness.UNCERTAIN, "new": api.Liveness.LIVE},
    )
    with pytest.raises(api.OwnershipConflict, match="uncertain.*old|old.*uncertain"):
        uncertain.acquire("new", target)


def test_one_task_and_one_worktree_limit():
    api = _api()

    with pytest.raises(api.OwnershipInvariant, match="task.*one|one.*task"):
        api.OwnershipSet(("T1", "T2"), None)
    with pytest.raises(api.OwnershipInvariant, match="worktree.*one|one.*worktree"):
        api.OwnershipSet(None, ("W1", "W2"))
    with pytest.raises(api.OwnershipInvariant, match="exclusive|already.*owned"):
        _state(
            api,
            {
                "first": api.OwnershipSet.for_worktree("W"),
                "second": api.OwnershipSet.for_worktree("W"),
            },
            {},
            {"first": api.Liveness.LIVE, "second": api.Liveness.LIVE},
        )


def test_acquire_release_preserve_cwd_roots_and_wip(tmp_path, monkeypatch):
    api = _api()
    launch_root = tmp_path / "launch"
    launch_root.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    wip = worktree / "untracked.txt"
    wip.write_text("user WIP\n", encoding="utf-8")
    monkeypatch.chdir(launch_root)

    before_cwd = Path.cwd()
    before_wip = wip.read_bytes()
    state = _state(
        api,
        {"executor": api.OwnershipSet.for_worktree("W")},
        {},
        {"executor": api.Liveness.LIVE},
    )
    acquired = state.acquire(
        "executor", api.OwnershipSet.for_task("T", worktree_required=False)
    )
    released = acquired.state.release_task("executor", "T")

    assert released.after == api.OwnershipSet.for_worktree("W")
    assert Path.cwd() == before_cwd
    assert wip.read_bytes() == before_wip
    assert worktree.is_dir()

