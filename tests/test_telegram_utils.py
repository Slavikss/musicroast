from app.utils import convert_markdown_to_html, split_for_telegram


def test_short_text_single_chunk():
    assert split_for_telegram("привет") == ["привет"]


def test_empty():
    assert split_for_telegram("") == []


def test_split_on_paragraphs():
    paragraph = "слово " * 100
    text = "\n\n".join([paragraph.strip()] * 10)
    chunks = split_for_telegram(text, limit=1500)
    assert len(chunks) > 1
    assert all(len(chunk) <= 1500 for chunk in chunks)
    # ничего не потеряли
    assert sum(chunk.count("слово") for chunk in chunks) == 1000


def test_giant_paragraph_hard_split():
    text = "а" * 5000
    chunks = split_for_telegram(text, limit=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert sum(len(c) for c in chunks) == 5000


def test_markdown_conversion_keeps_bold():
    html = convert_markdown_to_html("**жирный** и <опасный> текст")
    assert "<b>жирный</b>" in html
    assert "&lt;опасный&gt;" in html
