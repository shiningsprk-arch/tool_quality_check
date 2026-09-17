# -*- coding: utf-8 -*-
"""calibre.ebooks.metadata -> the three pure helpers the ported checks use."""

__all__ = ['authors_to_string', 'check_isbn', 'title_sort', 'fmt_sidx']

_ARTICLES = ('the', 'a', 'an')


def authors_to_string(authors, sep=' & '):
    """calibre accepts either a list or an already-joined string."""
    if authors is None:
        return ''
    if isinstance(authors, str):
        return authors
    return sep.join(a for a in authors if a)


def check_isbn(isbn, evaluate_only=False):
    """Return True when the ISBN-10/13 check digit is valid.

    ``evaluate_only`` keeps calibre's signature (it answers "would this pass"), which is
    the only mode the ported code uses.
    """
    if not isbn:
        return False
    text = ''.join(c for c in str(isbn) if c.isdigit() or c in 'Xx')
    if len(text) == 10:
        if not text[:9].isdigit() or text[9] not in '0123456789Xx':
            return False
        total = 0
        for idx, char in enumerate(text):
            weight = 10 - idx
            value = 10 if char in 'Xx' else int(char)
            total += weight * value
        return total % 11 == 0
    if len(text) == 13:
        if not text.isdigit():
            return False
        total = sum((1 if idx % 2 == 0 else 3) * int(c) for idx, c in enumerate(text))
        return total % 10 == 0
    return False


def _host_title_sort():
    """MyBooks' own title sort function, when the port runs inside the host.

    A MyBooks library stores ``webserver.utils.get_title_sort(title)`` (ASCII/pinyin,
    lowercased) in the title sort column, so this is what "the right answer" means here.
    Imported lazily: the offline smoke run loads this package without MyBooks.
    """
    try:
        from webserver.utils import get_title_sort
    except Exception:
        return None
    return get_title_sort


def title_sort(title, lang=None, **kwargs):
    """calibre's title_sort, deferring to the host's implementation when available.

    calibre carries per-language article tables. Recomputing a sort that way would
    disagree with a MyBooks database for every CJK title (the host stores pinyin), which
    would make the ported "check title sort" flag the whole library. So when
    ``webserver.utils.get_title_sort`` is importable we use it, and the check becomes
    "is the stored sort still what MyBooks would compute for this title" -- a real
    finding (a title edited without regenerating its sort). Offline, where there is no
    host, fall back to the English article rule.
    """
    if not title:
        return title
    host = _host_title_sort()
    if host is not None:
        try:
            return host(str(title))
        except Exception:
            pass
    text = str(title).strip()
    if lang and str(lang).lower().startswith('en'):
        lowered = text.lower()
        for article in _ARTICLES:
            if lowered.startswith(article + ' '):
                return '%s, %s' % (text[len(article):].strip(), text[:len(article)])
    return text


def fmt_sidx(sidx, use_roman=False):
    if sidx is None or sidx == '':
        return ''
    try:
        value = float(sidx)
    except (TypeError, ValueError):
        return str(sidx)
    if value == int(value):
        return '%d' % int(value)
    return ('%.2f' % value).rstrip('0').rstrip('.')
