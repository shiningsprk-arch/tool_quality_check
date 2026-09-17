# -*- coding: utf-8 -*-
"""One-off writer for the vendored calibre shim tree.

The shim files are ordinary source files in backend/qc/shim/; this script only exists
because writing ~25 small modules by hand is tedious. Safe to re-run: it overwrites the
shim tree with the canonical content, byte for byte.

It deliberately does **not** write ``qc/dialogs.py``: that file is the hand-written seam
(the synchronous, cancellable stand-in for calibre's QProgressDialog) and there is no
generated copy of it to keep in sync.
"""
import io
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend', 'qc')

FILES = {}


FILES['shim/__init__.py'] = '''# -*- coding: utf-8 -*-
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
'''


FILES['shim/utils/__init__.py'] = ''


FILES['shim/utils/zipfile.py'] = '''# -*- coding: utf-8 -*-
"""calibre.utils.zipfile -> stdlib zipfile.

calibre's fork tolerates a few more malformed archives than the stdlib one; for a
diagnostic tool that is acceptable, since a book we cannot open is itself a finding.
"""
from zipfile import (  # noqa: F401
    ZipFile, BadZipFile, BadZipfile, ZipInfo, LargeZipFile,
    ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA,
)
'''


FILES['shim/utils/logging.py'] = '''# -*- coding: utf-8 -*-
"""calibre.utils.logging -> a tiny logger.

The ported checks build their per-book detail text by writing into the log; the driver
reads ``plain_text`` (list of lines) and attributes the delta of each book's callback to
that book, which is how per-book findings are collected.
"""
from html import escape as _escape

__all__ = ['Log', 'GUILog']


class Log(object):
    def __init__(self):
        self.plain_text = []
        self.html = ''

    # calibre's Log is callable and stringifies+joins any number of positional values;
    # the ported code relies on that (e.g. error('Invalid epub:', exc)).
    def __call__(self, *args, **kwargs):
        self.info(*args, **kwargs)

    @staticmethod
    def _render(args):
        parts = []
        for a in args:
            if isinstance(a, BaseException):
                parts.append('%s: %s' % (type(a).__name__, a))
            else:
                parts.append('%s' % (a,))
        return ' '.join(parts)

    def _append(self, line):
        if not line:
            return
        self.plain_text.append(line)
        self.html += '<p>%s</p>' % _escape(line)

    def info(self, *args, **kwargs):
        self._append(self._render(args))

    def warn(self, *args, **kwargs):
        self._append(self._render(args))

    def error(self, *args, **kwargs):
        self._append(self._render(args))

    def debug(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        self._append(self._render(args))

    def clear(self):
        self.plain_text = []
        self.html = ''


class GUILog(Log):
    pass
'''


FILES['shim/utils/config.py'] = '''# -*- coding: utf-8 -*-
"""calibre.utils.config.JSONConfig -> a JSON file (or memory).

The driver calls ``set_config_dir()`` with the host's tool work dir; until then the
values simply live in memory.
"""
import json
import os

CONFIG_DIR = None


def set_config_dir(path):
    global CONFIG_DIR
    CONFIG_DIR = path
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


class JSONConfig(dict):
    def __init__(self, name):
        dict.__init__(self)
        self.name = name
        self.defaults = {}
        self.file_path = None
        self.load()

    # calibre uses names like 'plugins/Quality Check'; flatten to a safe filename.
    def _path(self):
        if not CONFIG_DIR:
            return None
        slug = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in self.name)
        return os.path.join(CONFIG_DIR, slug + '.json')

    def load(self):
        self.file_path = self._path()
        if self.file_path and os.path.exists(self.file_path):
            try:
                with io_open(self.file_path) as f:
                    self.update(json.load(f))
            except (OSError, ValueError):
                pass

    def commit(self):
        self.file_path = self._path()
        if not self.file_path:
            return
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(dict(self), f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # calibre's JSONConfig persists on assignment; keep that so the ported config code
    # keeps working untouched.
    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self.commit()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self.commit()

    def __getitem__(self, key):
        # calibre's JSONConfig falls back to .defaults on read; the ported config code
        # depends on that (it registers a defaults group, then reads it back by key).
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return self.defaults[key]

    def get(self, key, default=None):
        if key in self:
            return dict.get(self, key)
        return self.defaults.get(key, default)


def io_open(path):
    return open(path, 'r', encoding='utf-8')


# calibre.utils.config.prefs -- only read by the fix module, which the port omits.
prefs = JSONConfig('prefs')
'''


FILES['shim/utils/date.py'] = '''# -*- coding: utf-8 -*-
import datetime

utc_tz = datetime.timezone.utc
'''


FILES['shim/utils/localization.py'] = '''# -*- coding: utf-8 -*-
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
'''


FILES['shim/utils/titlecase.py'] = '''# -*- coding: utf-8 -*-
"""calibre.utils.titlecase.titlecase -> a John Gruber style title-caser.

Deliberately approximate: calibre's version is tuned for latin scripts, and the port
does not offer the "titles for title case" check, so this only exists to keep
check_metadata's module-level import working.
"""
import re

_SMALL_WORDS = set((
    'a an and as at but by en for if in of on or the to v vs via from into over with'
).split())

_WORD = re.compile(r"[A-Za-z][A-Za-z'\\u2019\\-]*")


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
'''


FILES['shim/polyglot_builtins.py'] = '''# -*- coding: utf-8 -*-
"""polyglot.builtins -> Python 3 aliases (this port is py3 only)."""
unicode_type = str
bytes_type = bytes
text_type = str
string_types = (str,)
long_type = int
is_py2 = False
is_py3 = True
range_ = range
zip_ = zip
'''


FILES['shim/sixshim/__init__.py'] = '''# -*- coding: utf-8 -*-
"""six -> the handful of aliases the ported code touches (py3 only)."""
from . import moves  # noqa: F401  (needed for six.moves.urllib.* attribute access)

PY2 = False
PY3 = True
text_type = str
binary_type = bytes
string_types = (str,)
integer_types = (int,)
class_types = (type,)
unichr = chr
long = int
MAXSIZE = 9223372036854775807


def iteritems(d, **kw):
    return iter(d.items(**kw))


def itervalues(d, **kw):
    return iter(d.values(**kw))


def iterkeys(d, **kw):
    return iter(d.keys(**kw))
'''


FILES['shim/sixshim/moves/__init__.py'] = '''# -*- coding: utf-8 -*-
from . import urllib  # noqa: F401

range = range
xrange = range
map = map
filter = filter
zip = zip
input = input
'''


FILES['shim/sixshim/moves/urllib/__init__.py'] = '''# -*- coding: utf-8 -*-
from . import error, parse, request  # noqa: F401
'''


FILES['shim/sixshim/moves/urllib/parse.py'] = '''# -*- coding: utf-8 -*-
from urllib.parse import *  # noqa: F401,F403
from urllib.parse import (  # noqa: F401
    quote, quote_plus, unquote, unquote_plus, urlencode, urljoin, urlparse,
    urlsplit, urlunparse, urlunsplit, parse_qs, parse_qsl,
)
'''


FILES['shim/sixshim/moves/urllib/request.py'] = '''# -*- coding: utf-8 -*-
from urllib.request import *  # noqa: F401,F403
from urllib.request import pathname2url, url2pathname  # noqa: F401
'''


FILES['shim/sixshim/moves/urllib/error.py'] = '''# -*- coding: utf-8 -*-
from urllib.error import *  # noqa: F401,F403
from urllib.error import URLError, HTTPError  # noqa: F401
'''


# ---------------------------------------------------------------------------
# calibre.ebooks.*
# ---------------------------------------------------------------------------

FILES['shim/ebooks/__init__.py'] = ''


FILES['shim/ebooks/chardet.py'] = '''# -*- coding: utf-8 -*-
"""calibre.ebooks.chardet.xml_to_unicode -> BOM/declaration aware decoding.

calibre routes this through chardet; for XML we can be much cheaper: the declaration
(or BOM) tells us the encoding, and XML in an EPUB is required to be UTF-8/UTF-16.
"""
import codecs
import re

_DECL_RE = re.compile(br'encoding\\s*=\\s*["\\\']([A-Za-z0-9_\\-]+)["\\\']')

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
        text = re.sub(r'^\\s*<\\?xml[^>]*\\?>', '', text, count=1)
    return text, used
'''


FILES['shim/ebooks/oeb/__init__.py'] = ''


FILES['shim/ebooks/oeb/base.py'] = '''# -*- coding: utf-8 -*-
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
'''


FILES['shim/ebooks/oeb/parse_utils.py'] = '''# -*- coding: utf-8 -*-
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
'''


FILES['shim/ebooks/metadata/__init__.py'] = '''# -*- coding: utf-8 -*-
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
'''


FILES['shim/ebooks/metadata/epub.py'] = '''# -*- coding: utf-8 -*-
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

_REF_RE = re.compile(br'CipherReference[^>]*URI\\s*=\\s*["\\\']([^"\\\']+)["\\\']', re.IGNORECASE)


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
    text = unquote(str(path)).replace('\\\\', '/')
    text = posixpath.normpath(text)
    return text.lstrip('./').lstrip('/')
'''


FILES['shim/ebooks/metadata/mobi.py'] = '''# -*- coding: utf-8 -*-
"""calibre.ebooks.metadata.mobi.MetadataUpdater -- NOT ported.

Only the ASIN *fix* used this class, and the port is read-only, so it exists purely to
keep mobi6.py importable. Touching it raises.
"""


class MetadataUpdater(object):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'the read-only port does not include MOBI metadata writing')
'''


FILES['shim/ebooks/mobi.py'] = '''# -*- coding: utf-8 -*-
"""calibre.ebooks.mobi -> the exception type mobi6.py raises."""


class MobiError(Exception):
    pass
'''


FILES['shim/ebooks/conversion/__init__.py'] = ''


FILES['shim/ebooks/conversion/preprocess.py'] = '''# -*- coding: utf-8 -*-
"""calibre.ebooks.conversion.preprocess.HTMLPreProcessor.

calibre passes this into its HTML parser to normalise sheets/Word markup. The port's
parse_html (lxml) accepts and ignores it.
"""


class HTMLPreProcessor(object):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
'''


FILES['shim/ebooks/conversion/config.py'] = '''# -*- coding: utf-8 -*-
"""calibre.ebooks.conversion.config.load_defaults -> fixed page-setup margins.

Quality Check's CSS-margin check compares a book against calibre's conversion
page-setup preferences. The port has no conversion settings to read, so it uses
calibre's own defaults (5pt on all four sides) -- see the check's option in the UI.
"""

_UNSET = 5.0


class ConversionConfig(dict):
    pass


def load_defaults(device=None):  # noqa: ARG001 - calibre's signature takes a device
    cfg = ConversionConfig()
    cfg['page_setup'] = {
        'margin_top': _UNSET,
        'margin_right': _UNSET,
        'margin_bottom': _UNSET,
        'margin_left': _UNSET,
    }
    return cfg
'''


def main():
    for rel, text in FILES.items():
        path = os.path.join(ROOT, rel.replace('/', os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        io.open(path, 'w', encoding='utf-8', newline='\n').write(text)
        print('wrote %s' % rel)
    print('%d files' % len(FILES))


if __name__ == '__main__':
    main()
