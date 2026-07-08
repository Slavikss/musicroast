import pytest

from app.services.diagnosis_registry import (
    DiagnosisRecord,
    DiagnosisRegistry,
    taste_diff,
)


def _record(snapshot_hash="h1", user_key="42", archetype="Позёр", z=2.0):
    return DiagnosisRecord(
        snapshot_hash=snapshot_hash,
        user_key=user_key,
        archetype=archetype,
        axis="gap",
        verdict_phrase="приговор",
        evidence=("t1 — a", "t2 — b", "t3 — c"),
        axis_scores={"gap": z},
        gate_status={"likes_gate": True},
    )


@pytest.mark.asyncio
async def test_append_and_dedupe_by_hash():
    registry = DiagnosisRegistry()
    first = await registry.append(_record())
    duplicate = await registry.append(_record(archetype="Другой"))
    assert duplicate is first  # append-only: дубликат хеша не перезаписывается
    assert (await registry.get_by_hash("h1")).archetype == "Позёр"


@pytest.mark.asyncio
async def test_history_and_previous():
    registry = DiagnosisRegistry()
    await registry.append(_record(snapshot_hash="h1"))
    await registry.append(_record(snapshot_hash="h2", archetype="Некроз вкуса"))
    history = await registry.history_for_user("42")
    assert [r.snapshot_hash for r in history] == ["h1", "h2"]

    previous = await registry.previous_for_user("42", current_hash="h2")
    assert previous.snapshot_hash == "h1"
    assert await registry.previous_for_user("42", current_hash="h1") is not None
    assert await registry.previous_for_user("нет", current_hash="x") is None


def test_taste_diff_variants():
    assert taste_diff(None, "Позёр", "gap", {"gap": 2.0}) is None

    changed = taste_diff(_record(archetype="Некроз вкуса"), "Позёр", "gap", {"gap": 2.0})
    assert "сменился" in changed

    worse = taste_diff(_record(z=1.0), "Позёр", "gap", {"gap": 2.0})
    assert "усугубился" in worse

    better = taste_diff(_record(z=2.0), "Позёр", "gap", {"gap": 1.0})
    assert "лучше" in better

    stable = taste_diff(_record(z=2.0), "Позёр", "gap", {"gap": 2.1})
    assert "стабилен" in stable
