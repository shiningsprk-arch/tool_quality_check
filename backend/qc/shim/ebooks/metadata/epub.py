# -*- coding: utf-8 -*-
"""calibre.ebooks.metadata.epub.Encryption -> an encryption.xml reader.

Only ``is_encrypted(path)`` is used (by the replaceable-cover check), so we collect the
percent-decoded CipherReference URIs and test membership. Adobe's resource obfuscation
(``.../enc#RC``) is deliberately treated as encryption here, exactly like calibre --
the caller separately distinguishes it from real DRM.
"""
import posixpath
import re
from urllib.parse import unquote

from lxml import etree

_REF_RE = re.compile(br'CipherReference[^>]*URI\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


class Encryption(object):
    def __init__(self, raw):
        self.encrypted = set()
        if raw is None:
            return
        if isinstance(raw, str):
            raw = raw.encode('utf-8', 'replace')
        try:
            root = etree.fromstring(raw, parser=etree.XMLParser(recover=True, resolve_entities=False))
        except Exception:
            root = None
        if root is not None:
            for ref in root.xpath('//*[local-name()="CipherReference"]'):
                uri = ref.get('URI')
                if uri:
                    self.encrypted.add(_normalize(uri))
        for m in _REF_RE.finditer(raw):
            try:
                self.encrypted.add(_normalize(m.group(1).decode('utf-8')))
            except UnicodeDecodeError:
                pass

    def is_encrypted(self, path):
        if not path:
            return False
        return _normalize(path) in self.encrypted


def _normalize(path):
    text = unquote(str(path)).replace('\\', '/')
    text = posixpath.normpath(text)
    return text.lstrip('./').lstrip('/')
