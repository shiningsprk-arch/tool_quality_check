# -*- coding: utf-8 -*-
"""calibre.utils.titlecase.titlecase -> a John Gruber style title-caser.

Deliberately approximate: calibre's version is tuned for latin scripts, and the port
does not offer the "titles for title case" check, so this only exists to keep
check_metadata's module-level import working.
"""
import re

_SMALL_WORDS = set((
    'a an and as at but by en for if in of on or the to v vs via from into over with'
).split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*")


def _cap(word):
    if not word:
        return word
    # Leave words that already contain an uppercase run alone (acronyms, iPhone, ...).
    if any(c.isupper() for c in word[1:]):
        return word
    return word[0].upper() + word[1:]


def titlecase(text):
    if not text:
        return text
    matches = list(_WORD.finditer(text))
    if not matches:
        return text
    out, last_end = [], 0
    for idx, m in enumerate(matches):
        word = m.group(0)
        is_first = idx == 0
        is_last = idx == len(matches) - 1
        after_colon = text[:m.start()].rstrip().endswith(':')
        out.append(text[last_end:m.start()])
        if word.lower() in _SMALL_WORDS and not (is_first or is_last or after_colon):
            out.append(word.lower())
        else:
            out.append(_cap(word))
        last_end = m.end()
    out.append(text[last_end:])
    return ''.join(out)
