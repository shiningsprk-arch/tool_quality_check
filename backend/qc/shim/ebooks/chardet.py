# -*- coding: utf-8 -*-
"""calibre.ebooks.chardet.xml_to_unicode -> BOM/declaration aware decoding.

calibre routes this through chardet; for XML we can be much cheaper: the declaration
(or BOM) tells us the encoding, and XML in an EPUB is required to be UTF-8/UTF-16.
"""
import codecs
import re

_DECL_RE = re.compile(br'encoding\s*=\s*["\']([A-Za-z0-9_\-]+)["\']')

_FALLBACKS = ('utf-8', 'utf-16', 'gb18030', 'big5', 'latin-1')


def _sniff(raw):
    if raw.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return 'utf-16'
    m = _DECL_RE.search(raw[:512])
    if m:
        try:
            return m.group(1).decode('ascii')
        except Exception:
            pass
    return None


def xml_to_unicode(raw, strip_encoding_pats=False, assume_utf8=False,
                   resolve_entities=True, **kwargs):
    """Return ``(text, encoding)`` like calibre's helper."""
    if isinstance(raw, str):
        text, used = raw, 'utf-8'
    else:
        used = _sniff(raw)
        order = ([used] if used else []) + list(_FALLBACKS)
        text = None
        for enc in order:
            try:
                text = raw.decode(enc)
                used = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text, used = raw.decode('utf-8', 'replace'), 'utf-8'
    if strip_encoding_pats:
        text = re.sub(r'^\s*<\?xml[^>]*\?>', '', text, count=1)
    return text, used
