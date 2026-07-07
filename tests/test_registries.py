import time

import pytest

from app.services.battle import BattleError, BattleRegistry, BattleSide
from app.services.roast_registry import RoastRecord, RoastRegistry


def _side(name="Вася", score=5.0):
    return BattleSide(
        display_name=name,
        score=score,
        diagnosis="диагноз",
        stats_block="факты",
        sample_lines="- трек",
    )


def _record(user_key="1", name="Вася"):
    return RoastRecord(
        roast_id="",
        user_key=user_key,
        display_name=name,
        text="Прожарка.",
        score=4.2,
        diagnosis="диагноз",
        shame_facts=["факт"],
        stats_block="факты",
        sample_lines="- трек",
        playlist_title="Мне нравится",
        level="medium",
    )


@pytest.mark.asyncio
async def test_battle_create_join_result():
    registry = BattleRegistry(ttl=60)
    battle = await registry.create(_side("A"), "medium")
    assert len(battle.code) == 6

    joined = await registry.join(battle.code.lower(), _side("B"))
    assert joined.opponent.display_name == "B"

    await registry.set_result(battle.code, "вердикт", "A")
    stored = await registry.get(battle.code)
    assert stored.status == "done"
    assert stored.winner == "A"


@pytest.mark.asyncio
async def test_battle_double_join_rejected():
    registry = BattleRegistry(ttl=60)
    battle = await registry.create(_side("A"), "medium")
    await registry.join(battle.code, _side("B"))
    with pytest.raises(BattleError):
        await registry.join(battle.code, _side("C"))


@pytest.mark.asyncio
async def test_battle_unknown_code():
    registry = BattleRegistry(ttl=60)
    with pytest.raises(BattleError):
        await registry.join("NOPE42", _side("B"))


@pytest.mark.asyncio
async def test_battle_ttl_expiry(monkeypatch):
    registry = BattleRegistry(ttl=60)
    battle = await registry.create(_side("A"), "medium")
    monkeypatch.setattr(time, "time", lambda: battle.expires_at + 1)
    assert await registry.get(battle.code) is None


@pytest.mark.asyncio
async def test_roast_registry_roundtrip():
    registry = RoastRegistry(ttl=60)
    roast_id = await registry.create(_record(user_key="42"))
    record = await registry.get(roast_id)
    assert record is not None
    assert record.display_name == "Вася"

    latest = await registry.latest_for_user("42")
    assert latest.roast_id == roast_id


@pytest.mark.asyncio
async def test_roast_registry_ttl(monkeypatch):
    registry = RoastRegistry(ttl=60)
    roast_id = await registry.create(_record())
    record = await registry.get(roast_id)
    monkeypatch.setattr(time, "time", lambda: record.expires_at + 1)
    assert await registry.get(roast_id) is None


@pytest.mark.asyncio
async def test_pending_referrals():
    registry = RoastRegistry(ttl=60)
    roast_id = await registry.create(_record(user_key="1"))
    await registry.set_pending_referral("2", roast_id)

    popped = await registry.pop_pending_referral("2")
    assert popped is not None
    assert popped.roast_id == roast_id
    # второй раз — пусто
    assert await registry.pop_pending_referral("2") is None
