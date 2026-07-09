import pytest
from fastapi.testclient import TestClient

from app.services.gemini import GeminiRoaster, RoastOutcome

VALID_TOKEN = "y0_AgAAAABkZXYtdG9rZW4tZm9yLXRlc3Rz"

FAKE_ROAST = RoastOutcome(
    text="**Вердикт**: библиотека — музей 2016 года.\n\nВторой абзац с панчем.",
    score=3.2,
    diagnosis="хроническая пятнадцатилетка",
    diagnosis_name="Синдром законсервированного бунта",
    severity=7,
    symptoms=["волна на грустном", "24 трека Кино за вечер", "скробблинг выключен"],
    shame_facts=["37.5% библиотеки — Кино", "24 трека Кино за день", "шансон в лайках"],
    prescription="месяц выбирать треки руками",
)


@pytest.fixture()
def client(monkeypatch):
    def fake_generate_roast(
        self, tracks, stats_block=None, level=None, prompt_version=None, **kwargs
    ):
        return FAKE_ROAST

    def fake_battle_verdict(self, dossier_a, dossier_b):
        return "Бой был жарким.\n🏆 ПОБЕДИТЕЛЬ: A — меньше кринжа.\nWINNER: A"

    monkeypatch.setattr(GeminiRoaster, "generate_roast", fake_generate_roast)
    monkeypatch.setattr(GeminiRoaster, "generate_battle_verdict", fake_battle_verdict)

    # Лирик-слой не должен ходить в реальный Gemini из тестов
    import app.services.music_roast as music_roast_module

    monkeypatch.setattr(
        music_roast_module, "extract_lyric_theme", lambda *a, **kw: None
    )

    from app import create_app

    return TestClient(create_app())


def test_validate_token_ok(client):
    response = client.post(
        "/auth/validate",
        json={"provider": "yandex", "raw_input": f"бла бла {VALID_TOKEN}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["access_token"] == VALID_TOKEN
    assert data["account"]["display_name"] == "Тестовый Меломан"
    assert data["account"]["liked_count"] > 0


def test_validate_token_garbage(client):
    response = client.post(
        "/auth/validate", json={"provider": "yandex", "raw_input": "привет"}
    )
    assert response.status_code == 422


def test_playlists(client):
    response = client.post(
        "/streaming/playlists",
        json={"provider": "yandex", "access_token": VALID_TOKEN},
    )
    assert response.status_code == 200
    playlists = response.json()["playlists"]
    assert playlists[0]["kind"] == "liked"
    assert playlists[0]["track_count"] > 0


def test_roast_flow_and_teaser(client):
    response = client.post(
        "/roast",
        json={
            "provider": "yandex",
            "access_token": VALID_TOKEN,
            "playlist_kind": "liked",
            "level": "cremation",
            "display_name": "Вася",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 3.2
    assert data["diagnosis"] == "хроническая пятнадцатилетка"
    assert data["level"] == "cremation"
    assert data["roast_id"]
    assert data["stats"]["total_tracks"] > 0
    assert len(data["shame_facts"]) == 3

    # LLM-доктор: диагноз с уникальным названием, стадией, симптомами и рецептом
    taste = data["taste_diagnosis"]
    assert taste is not None
    assert taste["diagnosis_name"] == "Синдром законсервированного бунта"
    assert taste["severity"] == 7
    assert len(taste["symptoms"]) == 3
    assert taste["prescription"]
    assert len(taste["evidence"]) == 3
    assert taste["snapshot_hash"]
    coverage = data["medkarta_coverage"]
    assert coverage["likes"] >= 200
    assert coverage["history"] is True
    # axis_scores наружу не выходят — только внутренний дебаг
    assert "axis_scores" not in taste

    teaser = client.get(f"/roast/{data['roast_id']}/teaser")
    assert teaser.status_code == 200
    tdata = teaser.json()
    assert tdata["score"] == 3.2
    assert tdata["display_name"] == "Вася"
    assert tdata["first_punch"].startswith("**Вердикт**")

    missing = client.get("/roast/nope/teaser")
    assert missing.status_code == 404


def test_battle_full_cycle(client):
    start = client.post(
        "/battle/start",
        json={
            "provider": "yandex",
            "access_token": VALID_TOKEN,
            "display_name": "A",
            "level": "medium",
        },
    )
    assert start.status_code == 200
    code = start.json()["code"]
    assert start.json()["initiator"]["score"] == 3.2

    status = client.get(f"/battle/{code}")
    assert status.status_code == 200
    assert status.json()["status"] == "waiting"

    join = client.post(
        "/battle/join",
        json={
            "provider": "yandex",
            "access_token": VALID_TOKEN,
            "code": code,
            "display_name": "B",
        },
    )
    assert join.status_code == 200
    jdata = join.json()
    assert jdata["status"] == "done"
    assert jdata["winner"] == "A"
    assert "🏆" in jdata["result"]

    final = client.get(f"/battle/{code}")
    assert final.json()["status"] == "done"

    again = client.post(
        "/battle/join",
        json={
            "provider": "yandex",
            "access_token": VALID_TOKEN,
            "code": code,
            "display_name": "C",
        },
    )
    assert again.status_code == 409


def test_battle_unknown_code(client):
    response = client.get("/battle/NOPE42")
    assert response.status_code == 404


def test_index_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "MusicRoast" in response.text
