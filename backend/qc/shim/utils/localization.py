# -*- coding: utf-8 -*-
"""calibre.utils.localization.get_udc() -> an ASCII-folding translator.

calibre ships a hand-maintained character map; this uses Unicode decomposition, which
agrees on the accented-Latin cases the author checks care about.
"""
import unicodedata


class _UnicodeDataCodec(object):
    def decode(self, text, errors='replace'):
        if text is None:
            return ''
        decomposed = unicodedata.normalize('NFKD', str(text))
        return decomposed.encode('ascii', 'ignore').decode('ascii')

    def encode(self, text):
        return str(text)


_UDC = _UnicodeDataCodec()


def get_udc():
    return _UDC
