# -*- coding: utf-8 -*-
"""calibre.ebooks.oeb.parse_utils.parse_html -> lxml.html.

Only one check (check_epub_svg_cover) reaches this path. The extra keyword arguments
calibre accepts (decoder, preprocessor, filename, non_html_file_tags, ...) are accepted
and ignored: the SVG cover test is a two-tag lookup, not a full OEB parse.
"""
from lxml import etree, html

_MEDIA_TYPES = {
    'application/xhtml+xml', 'text/html', 'application/xml',
    'application/x-dtbook+xml', 'text/x-oeb1-document',
}


class NotHTML(Exception):
    def __init__(self, msg=''):
        Exception.__init__(self, msg)
        self.msg = msg


def parse_html(raw, log=None, decoder=None, preprocessor=None, filename='',
               non_html_file_tags=frozenset(), **kwargs):
    if raw is None:
        raise NotHTML(filename or 'no content')
    if isinstance(raw, bytes):
        if decoder is not None:
            try:
                raw = decoder(raw)
            except Exception:
                raw = raw.decode('utf-8', 'replace')
        else:
            raw = raw.decode('utf-8', 'replace')
    if not raw.strip():
        raise NotHTML(filename or 'empty content')
    parser = html.HTMLParser(recover=True, encoding='utf-8', remove_comments=False)
    try:
        root = html.fromstring(raw.encode('utf-8'), parser=parser)
    except (etree.ParserError, ValueError, TypeError) as e:
        raise NotHTML('%s: %s' % (filename, e))
    if root is None:
        raise NotHTML(filename or 'unparseable')
    # lxml.html gives an <html> element; XPath('/html/...') style queries in calibre
    # expect the document element, which is what we already have.
    return root
