# -*- coding: utf-8 -*-
"""calibre.ebooks.oeb.base.XPath -> an lxml XPath with calibre's namespace prefixes.

calibre registers a large nsmap; only the prefixes the ported checks use are needed.
"""
from lxml import etree

NSMAP = {
    'h': 'http://www.w3.org/1999/xhtml',
    'xhtml': 'http://www.w3.org/1999/xhtml',
    'svg': 'http://www.w3.org/2000/svg',
    'opf': 'http://www.idpf.org/2007/opf',
    'dc': 'http://purl.org/dc/elements/1.1/',
    'ncx': 'http://www.daisy.org/z3986/2005/ncx/',
    'ocf': 'urn:oasis:names:tc:opendocument:xmlns:container',
    'xlink': 'http://www.w3.org/1999/xlink',
    'epub': 'http://www.idpf.org/2007/ops',
}


def XPath(expr, namespaces=None, **kwargs):
    ns = dict(NSMAP)
    if namespaces:
        ns.update(namespaces)
    return etree.XPath(expr, namespaces=ns, **kwargs)
