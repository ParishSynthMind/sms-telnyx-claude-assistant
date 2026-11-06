"""SMS segment maths.

A text that fits the GSM-7 alphabet is 160 characters in one segment, or 153 per
segment once it is split (the rest of each segment is the header that joins them).
Anything outside GSM-7 (an emoji, a curly quote, most non-Latin scripts) switches the
whole message to UCS-2: 70 characters in one segment, 67 per segment when split.
Carriers bill per segment, so the spend cap and the reply length cap both count these."""
import math

GSM_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# These take two GSM-7 slots each (an escape character plus the character).
GSM_EXTENDED = "^{}\\[~]|€\f"

_BASIC = set(GSM_BASIC)
_EXTENDED = set(GSM_EXTENDED)


def is_gsm7(text: str) -> bool:
    return all(c in _BASIC or c in _EXTENDED for c in text)


def encoding(text: str) -> str:
    return "GSM-7" if is_gsm7(text) else "UCS-2"


def units(text: str) -> int:
    """Length in the units the encoding counts: GSM-7 septets, or UTF-16 code units."""
    if is_gsm7(text):
        return sum(2 if c in _EXTENDED else 1 for c in text)
    return len(text.encode("utf-16-le")) // 2


def segment_count(text: str) -> int:
    if not text:
        return 1
    n = units(text)
    single, multi = (160, 153) if is_gsm7(text) else (70, 67)
    return 1 if n <= single else math.ceil(n / multi)


def truncate_to_segments(text: str, max_segments: int, suffix: str = "") -> str:
    """Shorten text (at a word boundary when possible) so it fits in max_segments."""
    text = text.strip()
    if segment_count(text) <= max_segments:
        return text
    words = text.split(" ")
    while words:
        words.pop()
        candidate = " ".join(words).rstrip(" ,;:-") + suffix
        if words and segment_count(candidate) <= max_segments:
            return candidate
    # One very long word: cut characters instead.
    out = text
    while out and segment_count(out + suffix) > max_segments:
        out = out[:-1]
    return out + suffix
