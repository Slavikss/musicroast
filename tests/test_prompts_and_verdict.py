import json

from app.prompts import BATTLE_VERSION, PromptManager, RoastLevel
from app.services.gemini import parse_verdict, parse_winner


def test_levels_registered():
    manager = PromptManager()
    versions = manager.list_versions()
    for level in RoastLevel:
        assert level.value in versions
    assert BATTLE_VERSION in versions
    assert manager.get_template().version == RoastLevel.MEDIUM.value


def test_get_for_level():
    manager = PromptManager()
    assert manager.get_for_level(RoastLevel.CREMATION).version == "cremation"


def test_json_override(tmp_path):
    config = tmp_path / "prompts.json"
    config.write_text(
        json.dumps({"custom": {"system_prompt": "Ты добрый."}}), encoding="utf-8"
    )
    manager = PromptManager(config_path=config)
    assert manager.get_template("custom").system_prompt == "Ты добрый."


def test_parse_verdict_full():
    raw = (
        "Первый панч.\n\nВторой абзац.\n\n"
        "===VERDICT===\n"
        "SCORE: 3.2\n"
        "DIAGNOSIS_NAME: Синдром запечённого шансона\n"
        "SEVERITY: 7\n"
        "DIAGNOSIS: хроническая пятнадцатилетка.\n"
        "SYMPTOM: волна на грустном с 2021\n"
        "SYMPTOM: 24 трека Кино за вечер\n"
        "SYMPTOM: скробблинг выключен от стыда\n"
        "SHAME: 41% библиотеки из 2016\n"
        "SHAME: 24 трека Кино за день\n"
        "SHAME: шансон в лайках\n"
        "PRESCRIPTION: месяц без «Моей волны», выбирать треки руками\n"
    )
    outcome = parse_verdict(raw)
    assert outcome.text == "Первый панч.\n\nВторой абзац."
    assert outcome.score == 3.2
    assert outcome.diagnosis == "хроническая пятнадцатилетка"
    assert outcome.diagnosis_name == "Синдром запечённого шансона"
    assert outcome.severity == 7
    assert len(outcome.symptoms) == 3
    assert outcome.symptoms[0] == "волна на грустном с 2021"
    assert len(outcome.shame_facts) == 3
    assert outcome.prescription.startswith("месяц без")
    assert "===VERDICT===" not in outcome.text


def test_parse_verdict_comma_score_and_clamp():
    outcome = parse_verdict("Текст.\n===VERDICT===\nSCORE: 11,5\nDIAGNOSIS: x")
    assert outcome.score == 10.0


def test_parse_verdict_missing_block():
    outcome = parse_verdict("**Просто текст** без вердикта.\nВторая строка.")
    assert outcome.score == 5.0
    assert outcome.text.startswith("**Просто текст**")
    assert outcome.diagnosis  # первая строка как диагноз
    # фоллбеки доктора: всё заполнено
    assert outcome.diagnosis_name == outcome.diagnosis
    assert 1 <= outcome.severity <= 10
    assert outcome.prescription


def test_parse_verdict_fallbacks_partial():
    raw = (
        "Текст.\n===VERDICT===\nSCORE: 2.0\n"
        "DIAGNOSIS: приговор без имени болезни\n"
        "SHAME: факт раз\n"
    )
    outcome = parse_verdict(raw)
    assert outcome.diagnosis_name == "приговор без имени болезни"
    assert outcome.severity == 8  # round(10 - 2.0)
    assert outcome.symptoms == ["факт раз"]  # симптомы ← shame
    assert outcome.prescription  # дефолтный рецепт


def test_parse_winner():
    text, winner = parse_winner(
        "Интро.\n🏆 ПОБЕДИТЕЛЬ: Вася — у него меньше кринжа.\nWINNER: Вася"
    )
    assert winner == "Вася"
    assert "WINNER:" not in text
    assert "🏆" in text


def test_parse_winner_missing():
    text, winner = parse_winner("Просто текст без победителя")
    assert winner is None
    assert text == "Просто текст без победителя"
