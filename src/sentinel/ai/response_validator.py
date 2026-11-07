"""Validation and sanitising of provider output before it is shown or stored."""

from __future__ import annotations

import re
import unicodedata

from .errors import AIError, ErrorKind

MAX_ANSWER_CHARS = 8000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩​-‏﻿]")
_BLANKS = re.compile(r"\n{3,}")
_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+(.+?)\s*$")


def validate_answer(text: object, max_chars: int = MAX_ANSWER_CHARS, provider_id: str = "") -> tuple[str, tuple[str, ...]]:
    """Return (clean text, notes). Raises AIError(MALFORMED/EMPTY) for unusable output.

    The result is displayed as *plain text* only (never HTML or Markdown links), so a link in an answer is
    inert: Sentinel does not open links suggested by a model.
    """
    if not isinstance(text, str):
        raise AIError(ErrorKind.MALFORMED, "answer is not text", provider_id=provider_id)
    notes: list[str] = []
    clean = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    stripped = _CONTROL.sub("", clean)
    if stripped != clean:
        notes.append("Invisible or control characters were removed from the answer.")
    clean = _BLANKS.sub("\n\n", stripped).strip()
    if not clean:
        raise AIError(ErrorKind.EMPTY, "the answer contained no text", provider_id=provider_id)
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip() + "…"
        notes.append(f"The answer was shortened by Sentinel to {max_chars} characters.")
    return clean, tuple(notes)


def parse_suggestions(text: str, max_items: int = 8, max_chars: int = 120) -> list[str]:
    """Extract short list items (for 'suggest tasks'). Plain strings only; the user chooses which to keep."""
    out: list[str] = []
    for line in text.splitlines():
        m = _ITEM.match(line)
        if not m:
            continue
        item = _CONTROL.sub("", m.group(1)).strip().strip("*_` ")
        if len(item) < 3:
            continue
        item = item[:max_chars].rstrip()
        if item.lower() not in (o.lower() for o in out):
            out.append(item)
        if len(out) >= max_items:
            break
    return out
