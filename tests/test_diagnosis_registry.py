import pytest

from app.services.diagnosis_registry import (
    DiagnosisRecord,
    DiagnosisRegistry,
    taste_diff,
)


def _record(snapshot_hash="h1", user_key="42", name="Синдром запечённого шансона", severity=6):
    return DiagnosisRecord(
        snapshot_hash=snapshot_hash,
        user_key=user_key,
        diagnosis_name=name,
        severity=severity,
        verdict_phrase="приговор",
        symptoms=("симптом 1", "симптом 2"),
        prescription="слушать джаз по утрам",
        evidence=("t1 — a", "t2 — b", "t3 — c"),
        score=3.2,
    )


@pytest.mark.asyncio
async def test_append_and_dedupe_by_hash():
    registry = DiagnosisRegistry()
    first = await registry.append(_record())
    duplicate = await registry.append(_record(name="Другая болезнь"))
    assert duplicate is first  # append-only: дубликат хеша не перезаписывается
    stored = await registry.get_by_hash("h1")
    assert stored.diagnosis_name == "Синдром запечённого шансона"


@pytest.mark.asyncio
async def test_history_and_previous():
    registry = DiagnosisRegistry()
    await registry.append(_record(snapshot_hash="h1"))
    await registry.append(_record(snapshot_hash="h2", name="Некроз хайпа", severity=8))
    history = await registry.history_for_user("42")
    assert [r.snapshot_hash for r in history] == ["h1", "h2"]

    previous = await registry.previous_for_user("42", current_hash="h2")
    assert previous.snapshot_hash == "h1"
    assert await registry.previous_for_user("нет", current_hash="x") is None


def test_taste_diff_variants():
    assert taste_diff(None, "Болезнь", 5) is None

    worse = taste_diff(_record(severity=4), "Новая форма", 7)
    assert "прогрессирует" in worse
    assert "стадия 4" in worse

    better = taste_diff(_record(severity=8), "Болезнь", 5)
    assert "положительная" in better

    stable = taste_diff(_record(severity=6), "Болезнь", 6)
    assert "держится" in stable
