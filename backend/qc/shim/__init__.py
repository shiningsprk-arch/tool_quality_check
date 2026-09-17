# -*- coding: utf-8 -*-
"""Minimal stand-in for the few non-GUI calibre APIs the ported checks import.

Everything here exists to satisfy an import in the ported check modules; none of it is
a general-purpose calibre reimplementation.
"""
import mimetypes

# calibre registers a handful of ebook types with mimetypes; stdlib does not know them,
# and _get_opf_xml() indexes guess_type()'s result, so the return must stay a 2-tuple.
_EXTRA_TYPES = {
    '.opf': 'application/oebps-package+xml',
    '.epub': 'application/epub+zip',
    '.ncx': 'application/x-dtbncx+xml',
    '.xhtml': 'application/xhtml+xml',
    '.xpgt': 'application/vnd.adobe-page-template+xml',
}

for _ext, _mime in _EXTRA_TYPES.items():
    mimetypes.add_type(_mime, _ext)

# Messages that calibre would pop up in a Qt dialog. The port reports through the check
# log instead, so these only need to be callable and non-fatal.
DIALOG_HISTORY = []


def guess_type(url, strict=True):
    return mimetypes.guess_type(url, strict=strict)


def error_dialog(parent=None, title='', msg='', **kwargs):
    DIALOG_HISTORY.append(('error', title, msg))
    return None


def info_dialog(parent=None, title='', msg='', **kwargs):
    DIALOG_HISTORY.append(('info', title, msg))
    return None


class _GlobalPrefs(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


gprefs = _GlobalPrefs()
