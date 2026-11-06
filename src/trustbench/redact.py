"""Remove personal data and secrets from text before we store it.

A marker such as [EMAIL] replaces each match. We count the matches, never keep them.
Redaction by patterns is not perfect. It does not find names or addresses in free text.
Treat it as one layer. The default is also to store no content at all (see dataprotect).
"""

import re
from collections import Counter

# Order matters. Secrets first, so that the code does not read a token as a phone number.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("SECRET", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
                          r"AIza[0-9A-Za-z_-]{30,}|tb_[A-Za-z0-9_-]{30,})\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("SECRET", re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("SECRET", re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*[\"']?[^\s\"',;]{6,}")),
    ("URL_WITH_LOGIN", re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@[^\s]+", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")),
    ("IP_ADDRESS", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
]
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){13,19}(?![\w-])")
# A phone number must not be a part of a longer run of digit groups (such as a card number).
_PHONE = re.compile(r"(?<![\w.-])(?<!\d[ .-])(?:\+\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?|\d{2,4}[ .-])\d{3,4}[ .-]\d{3,4}(?![\w-])(?![ .-]\d)")


def luhn_ok(digits: str) -> bool:
    nums = [int(d) for d in digits][::-1]
    return sum(d if i % 2 == 0 else (d * 2 - 9 if d * 2 > 9 else d * 2) for i, d in enumerate(nums)) % 10 == 0


class RedactionError(ValueError):
    pass


def compile_custom(patterns: list[str]) -> list[re.Pattern]:
    out = []
    for p in patterns:
        if len(p) > 200:
            raise RedactionError("A custom pattern is longer than 200 characters.")
        try:
            out.append(re.compile(p))
        except re.error as e:
            raise RedactionError(f"The pattern {p!r} is not a valid regular expression: {e}") from e
    return out


def redact(text: str, custom: list[str] | None = None) -> tuple[str, dict[str, int]]:
    """Returns the cleaned text and the number of matches for each kind."""
    counts: Counter = Counter()

    def sub(kind: str, pattern: re.Pattern, text: str, check=None) -> str:
        def repl(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            counts[kind] += 1
            return f"[{kind}]"
        return pattern.sub(repl, text)

    for kind, pattern in _PATTERNS:
        text = sub(kind, pattern, text)
    text = sub("CARD_NUMBER", _CARD, text, lambda s: 13 <= len(re.sub(r"\D", "", s)) <= 19 and luhn_ok(re.sub(r"\D", "", s)))
    text = sub("PHONE", _PHONE, text)
    for pattern in compile_custom(custom or []):
        text = sub("CUSTOM", pattern, text)
    return text, dict(counts)
