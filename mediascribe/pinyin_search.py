"""Pinyin-normalized search keys for the wiki vault (Phase 4 add-on).

Lets users find notes by typing pinyin instead of exact Chinese text:

* full pinyin — ``xutao`` finds a note titled ``徐涛考研政治基础班 01``;
* initials — ``xt`` matches the same note;
* fuzzy sounds — the classic IME equivalence classes (``zh/z``, ``ch/c``,
  ``sh/s``, ``n/l``, ``f/h``, ``an/ang``, ``en/eng``, ``in/ing``) are folded
  into canonical forms on both the title and the query side, so a fuzzy
  query like ``zansi`` still matches ``战士``.

Titles and queries are reduced to the same canonical key (lowercase
``[a-z0-9]``) and matched with plain substring semantics, mirroring how
the vault's text search behaves.

Degradation: when pypinyin is not importable the key builders return
``None`` and :meth:`mediascribe.wiki.WikiVault.search` simply skips the
pinyin pass — plain substring search is unaffected.
"""

import re

__all__ = [
    "FUZZY_FOLDS",
    "fold_fuzzy",
    "normalize_query",
    "match_title",
    "pinyin_key",
    "initials_key",
]

# Ordered fuzzy-sound folds toward canonical forms. Order matters:
# retroflex initials must fold before ``h -> f`` so the ``h`` inside
# ``zh/ch/sh`` is consumed first — every remaining ``h`` is a real /x/
# initial at that point.
FUZZY_FOLDS: tuple[tuple[str, str], ...] = (
    ("zh", "z"),
    ("ch", "c"),
    ("sh", "s"),
    ("ang", "an"),
    ("eng", "en"),
    ("ing", "in"),
    ("l", "n"),
    ("h", "f"),
)

_HAN = re.compile(r"[\u4e00-\u9fff]")
_KEEP = re.compile(r"[^a-z0-9]")


def fold_fuzzy(s: str) -> str:
    """Apply fuzzy-sound folds to a lowercase pinyin string."""
    for pat, repl in FUZZY_FOLDS:
        s = s.replace(pat, repl)
    return s


def _han_syllables(text: str) -> list[str] | None:
    """Pinyin reading of every CJK char in ``text``, in order.

    Returns ``None`` when pypinyin is unavailable or ``text`` has no CJK
    (callers treat that as "pinyin search not possible for this text").
    """
    if not _HAN.search(text):
        return None
    try:
        from pypinyin import lazy_pinyin
    except ImportError:
        return None
    out: list[str] = []
    for ch in text:
        if _HAN.match(ch):
            reading = lazy_pinyin(ch)
            if reading:
                out.append(reading[0])
    return out


def pinyin_key(text: str) -> str | None:
    """Full-pinyin folded key of ``text``; ``None`` if not computable.

    Latin letters and digits in mixed titles are appended (lowercased) after
    the CJK readings, and folds apply to the whole key — so ``Phase 4 概念``
    keys as ``gainianpfase4``. Queries fold identically, hence ``phase4``
    still matches that title.
    """
    syllables = _han_syllables(text)
    if syllables is None:
        return None
    return _KEEP.sub("", fold_fuzzy(("".join(syllables) + text.lower())))


def initials_key(text: str) -> str | None:
    """First-letter key of the CJK readings in ``text``; ``None`` if not computable."""
    syllables = _han_syllables(text)
    if not syllables:
        return None
    return fold_fuzzy("".join(s[0] for s in syllables).lower())


def normalize_query(query: str) -> str | None:
    """Folded pinyin key of a user query, or ``None`` if not pinyin-like.

    Only pure-ASCII alphabetic queries (length >= 2) count as pinyin;
    anything else (Chinese text, digits, mixed) falls back to substring
    search.
    """
    q = (query or "").strip()
    if len(q) < 2 or not q.isascii() or not q.isalpha():
        return None
    return fold_fuzzy(q.lower())


def match_title(title: str, query: str) -> bool:
    """Whether a note title matches a folded pinyin ``query``.

    Both key forms match as substring: ``xutao`` inside a longer title,
    or a consecutive-initial run like ``jjjp`` inside ``机械键盘``'s
    initials. The 2+ letter minimum is enforced by :func:`normalize_query`.
    """
    full = pinyin_key(title)
    if full and query in full:
        return True
    initials = initials_key(title)
    return bool(initials and query in initials)
