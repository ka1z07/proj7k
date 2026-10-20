"""The language of messages a person reads in the dashboard and desktop app (ADR-0035).

Messages are written in both languages where they are raised: `tr("缺少音频", "Missing audio")`. The dashboard sets
the language per request from the `p7lang` cookie its pages keep, and per job from the connection that started it;
everything else (the command line, tests) stays Chinese.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from http.cookies import SimpleCookie
import locale
import os
from typing import Iterator, Optional

LANGUAGES = ("zh", "en")
_LANG: ContextVar[str] = ContextVar("proj7k_lang", default="zh")


def current() -> str:
    return _LANG.get()


def tr(zh: str, en: str) -> str:
    """`zh` or `en`, whichever the current language is."""
    return en if _LANG.get() == "en" else zh


def set_language(lang: Optional[str]) -> None:
    _LANG.set(lang if lang in LANGUAGES else "zh")


def enter(lang: Optional[str]) -> Token:
    """Switch to `lang` until `leave(token)`; for a worker thread running one job after another."""
    return _LANG.set(lang if lang in LANGUAGES else "zh")


def leave(token: Token) -> None:
    _LANG.reset(token)


@contextmanager
def language(lang: Optional[str]) -> Iterator[None]:
    token = enter(lang)
    try:
        yield
    finally:
        leave(token)


def system_language() -> str:
    """Chinese when the operating system's language is, English otherwise: for messages shown before any page is."""
    names = [os.environ.get(k, "") for k in ("LC_ALL", "LC_MESSAGES", "LANG")]
    try:
        names.append(locale.getlocale()[0] or "")
    except ValueError:
        pass
    name = next((n for n in names if n and n not in ("C", "POSIX")), "")
    return "zh" if not name or name.lower().startswith(("zh", "chinese")) else "en"


def from_cookie(header: Optional[str]) -> str:
    """The language a `Cookie` header names in `p7lang`, Chinese when it names none."""
    if not header:
        return "zh"
    try:
        morsel = SimpleCookie(header).get("p7lang")
    except Exception:
        return "zh"
    return morsel.value if morsel is not None and morsel.value in LANGUAGES else "zh"
