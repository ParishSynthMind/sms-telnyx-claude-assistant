from smsassist.segments import encoding, is_gsm7, segment_count, truncate_to_segments, units


def test_plain_text_is_gsm7():
    assert is_gsm7("Hello, your appointment is at 3pm!")
    assert encoding("Hello") == "GSM-7"


def test_emoji_and_curly_quotes_are_ucs2():
    assert encoding("See you soon 😀") == "UCS-2"
    assert encoding("It’s booked") == "UCS-2"


def test_160_gsm_chars_is_one_segment_161_is_two():
    assert segment_count("a" * 160) == 1
    assert segment_count("a" * 161) == 2
    assert segment_count("a" * 306) == 2
    assert segment_count("a" * 307) == 3


def test_ucs2_limits_70_and_67():
    assert segment_count("é" * 10 + "😀") == 1  # é is GSM, but the emoji makes the whole text UCS-2
    assert segment_count("Ж" * 70) == 1
    assert segment_count("Ж" * 71) == 2
    assert segment_count("Ж" * 134) == 2
    assert segment_count("Ж" * 135) == 3


def test_extended_chars_take_two_slots():
    assert units("€") == 2
    assert segment_count("€" * 80) == 1
    assert segment_count("€" * 81) == 2


def test_emoji_counts_two_utf16_units():
    assert units("😀") == 2


def test_empty_text_is_one_segment():
    assert segment_count("") == 1


def test_truncate_keeps_short_text():
    assert truncate_to_segments("  short  ", 1) == "short"


def test_truncate_cuts_at_word_boundary():
    text = ("word " * 80).strip()
    out = truncate_to_segments(text, 1)
    assert segment_count(out) == 1
    assert out.endswith("word") and len(out) <= 160


def test_truncate_one_giant_word():
    out = truncate_to_segments("x" * 500, 2)
    assert segment_count(out) == 2 and len(out) == 306
