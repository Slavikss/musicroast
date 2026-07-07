from app.utils.oauth import parse_token_input

VALID_TOKEN = "y0_AgAAAABkZXYtdG9rZW4tZm9yLXRlc3Rz"


def test_full_redirect_url_with_fragment():
    url = (
        "https://music.yandex.ru/#access_token="
        f"{VALID_TOKEN}&token_type=bearer&expires_in=31535645"
    )
    parsed = parse_token_input(url)
    assert parsed is not None
    assert parsed.access_token == VALID_TOKEN
    assert parsed.expires_in == 31535645


def test_fragment_only():
    parsed = parse_token_input(f"access_token={VALID_TOKEN}&token_type=bearer")
    assert parsed is not None
    assert parsed.access_token == VALID_TOKEN


def test_query_style():
    parsed = parse_token_input(f"https://example.com/cb?access_token={VALID_TOKEN}")
    assert parsed is not None
    assert parsed.access_token == VALID_TOKEN


def test_bare_token():
    parsed = parse_token_input(VALID_TOKEN)
    assert parsed is not None
    assert parsed.access_token == VALID_TOKEN
    assert parsed.expires_in is None


def test_token_inside_text():
    parsed = parse_token_input(f"вот мой токен: {VALID_TOKEN} держи")
    assert parsed is not None
    assert parsed.access_token == VALID_TOKEN


def test_legacy_token():
    legacy = "AQAA" + "x" * 30
    parsed = parse_token_input(f"токен {legacy}")
    assert parsed is not None
    assert parsed.access_token == legacy


def test_double_underscore_token():
    token = "y0__wgAAAABkZXYtdG9rZW4tZm9yLXRlc3Rz"
    parsed = parse_token_input(token)
    assert parsed is not None
    assert parsed.access_token == token


def test_garbage_is_rejected():
    # Регрессия: раньше любая строка считалась токеном
    assert parse_token_input("привет") is None
    assert parse_token_input("дай прожарку") is None
    assert parse_token_input("") is None
    assert parse_token_input("   ") is None
    assert parse_token_input("y0_короткий") is None


def test_bad_expires_in_is_ignored():
    parsed = parse_token_input(f"#access_token={VALID_TOKEN}&expires_in=abc")
    assert parsed is not None
    assert parsed.expires_in is None
