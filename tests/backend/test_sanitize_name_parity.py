import pytest
from empornium_megapack.build import sanitize_name

CORPUS = [
    ("", "Untitled"),
    ("   ", "Untitled"),
    ("  . . ", "Untitled"),
    ("CON", "_CON"),
    ("prn", "_prn"),
    ("aux", "_aux"),
    ("nul", "_nul"),
    ("COM1", "_COM1"),
    ("com9", "_com9"),
    ("lpt1", "_lpt1"),
    ("LPT9", "_LPT9"),
    ("COM10", "COM10"),
    ("CON.txt", "CON.txt"),
    ('a<b>c:"d"e|f?g*h', "a_b_c__d_e_f_g_h"),
    ("hello\x00world\x1ftest", "hello_world_test"),
    ("  spaced   name  with   tabs\t\nand newlines  ", "spaced name with tabs__and newlines"),
    ("  .. leading and trailing dots and spaces ..  ", "leading and trailing dots and spaces"),
    ("Unicode 日本語 映画 (2026)", "Unicode 日本語 映画 (2026)"),
    ("Русский Фильм 2026 (Оригинал)", "Русский Фильм 2026 (Оригинал)"),
    ("Pack 🚀 and 💎 [4K]", "Pack 🚀 and 💎 [4K]"),
    ("Éléphant and Café", "Éléphant and Café"),
    ("a" * 150 + ".mp4", "a" * 116 + ".mp4"),
    ("a" * 150 + ".superlongextension", "a" * 120),
    ("a" * 150, "a" * 120),
    ("   " + "a" * 150 + "   ", "a" * 120),
]


@pytest.mark.parametrize("raw_input, expected", CORPUS)
def test_sanitize_name_parity_cases(raw_input, expected):
    result = sanitize_name(raw_input)
    assert result == expected
    assert len(result) <= 120
