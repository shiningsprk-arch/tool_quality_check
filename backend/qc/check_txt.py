# -*- coding: utf-8 -*-
"""TXT checks for the MyBooks port.

**本文件是移植仓新增的代码，不是上游 Quality Check 的移植部分**（上游一个 TXT 检查都没有）。
它只读 TXT 文件的字节，不改书、不落盘。

结构与上游一致：每个检查项一个方法，方法里用 ``self.check_all_files(evaluate_book, ...)``
逐书跑，``self.log(...)`` 写在当前书名下（``qc/dialogs.py`` 会把回调期间的日志归属到那本书）。
判定与提取分开：模块级的 ``txt_digest()`` 与 ``hit_*()`` 都是纯函数，因此不依赖宿主也能单测。
"""
from __future__ import unicode_literals, division, absolute_import, print_function

__license__ = 'GPL v3'
__copyright__ = '2026, 黏菌（本仓新增代码，非上游移植部分）'

try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

import re

from .shim import error_dialog

from .check_base import BaseCheck
from .helpers import get_title_authors_text

TXT_FORMATS = ['TXT']

# 判定阈值。都是"畸形文本"的界，不是取向偏好，所以不给前端选项。
MAX_LINE_CHARS = 5000

#: BOM 表：前缀 → 名字。UTF-16/32 的 BOM 也一并当"有 BOM"，它们同样会被阅读器显示成乱码。
_BOMS = (
    (b'\xef\xbb\xbf', 'UTF-8'),
    (b'\xff\xfe\x00\x00', 'UTF-32LE'),
    (b'\x00\x00\xfe\xff', 'UTF-32BE'),
    (b'\xff\xfe', 'UTF-16LE'),
    (b'\xfe\xff', 'UTF-16BE'),
)

#: 控制字符（不含 \t\n\r\f 之外的常见格式字符；NUL 到 0x08、0x0B、0x0C、0x0E-0x1F、0x7F）。
#: 0x00 是最要紧的一个——它几乎总意味着"这不是文本，是二进制被人改了扩展名"。
_CONTROL_RE = re.compile('[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


# --------------------------------------------------------------------------- 提取（纯）

def _bom_kind(data):
    for prefix, name in _BOMS:
        if data.startswith(prefix):
            return name
    return ''


def _count_newlines(data):
    crlf = data.count(b'\r\n')
    lf = data.count(b'\n') - crlf
    cr = data.count(b'\r') - crlf
    return {'crlf': crlf, 'lf': lf, 'cr': cr}


def _encoding_hint(data):
    """非 UTF-8 时给一个人能看懂的猜测（chardet 在宿主镜像里有，但缺失也要能跑）。"""
    try:
        import chardet
    except ImportError:
        chardet = None
    if chardet is not None:
        try:
            guess = chardet.detect(data[:65536]) or {}
            name = guess.get('encoding')
            if name:
                return '%s（置信度 %.0f%%）' % (name, (guess.get('confidence') or 0) * 100)
        except Exception:
            pass
    # 离线回退：能严格解出来的中文编码里最常见的那两个
    for candidate in ('gb18030', 'big5'):
        try:
            data.decode(candidate)
            return candidate
        except (UnicodeDecodeError, LookupError):
            continue
    return '未知（不是 UTF-8）'


def txt_digest(data):
    """把一个 TXT 文件的字节压成判定层要的事实（纯函数）。"""
    bom = _bom_kind(data)
    # 去掉 BOM 再判编码：带 BOM 的文件严格解码时前缀会报错，那不是"编码不对"
    stripped = data
    for prefix, _name in _BOMS:
        if data.startswith(prefix):
            stripped = data[len(prefix):]
            break

    decoded = None
    decode_error = None
    try:
        decoded = stripped.decode('utf-8')
    except UnicodeDecodeError as e:
        decode_error = e

    text_lines = (decoded if decoded is not None else stripped).splitlines()

    # 控制字符只数一遍，顺带记住第一个的位置与码点（NUL 最能说明"这不是文本"）
    probe = decoded if decoded is not None else stripped.decode('latin-1', 'replace')
    control_count = 0
    first_control = None
    for match in _CONTROL_RE.finditer(probe):
        control_count += 1
        if first_control is None:
            first_control = (match.start(), ord(match.group(0)))

    return {
        'size': len(data),
        'bom': bom,
        'decoded_ok': decoded is not None,
        'decode_error_offset': decode_error.start if decode_error is not None else None,
        'encoding_hint': '' if decoded is not None else _encoding_hint(stripped),
        'control_count': control_count,
        'control_first': first_control,
        'newlines': _count_newlines(data),
        'line_count': len(text_lines),
        'longest_line': max([len(line) for line in text_lines] or [0]),
        'blank': not (decoded if decoded is not None else stripped).strip(),
    }


# --------------------------------------------------------------------------- 判定（纯）

def hit_empty(digest):
    return digest['size'] == 0 or digest['blank']


def hit_not_utf8(digest):
    return not digest['decoded_ok']


def hit_bom(digest):
    return bool(digest['bom'])


def hit_control_chars(digest):
    return digest['control_count'] > 0


def hit_mixed_newlines(digest):
    counts = digest['newlines']
    kinds = len([n for n in counts.values() if n > 0])
    if kinds <= 1:
        return False
    # CRLF+LF 混用是最常见的一种（Windows 编辑器改过一部分）；单独只出现 CR 也算异常，
    # 上面 kinds<=1 已经把"整本统一 CR"排除在外了，这里只抓真正的混用。
    return True


def hit_long_lines(digest):
    if digest['line_count'] <= 1 and digest['size'] > MAX_LINE_CHARS:
        return True  # 整本没有换行
    return digest['longest_line'] > MAX_LINE_CHARS


# --------------------------------------------------------------------------- 逐书日志

def _read_digest(check, db, book_id):
    """读一本书的 TXT → ``(digest, problem)``。读不了返回 problem，由调用方当命中处理。

    这里不能只 ``log.error`` 然后返回 False：driver 只为**命中**的书保留逐书日志，
    未命中书的日志会被丢掉——那样"读不了"就变成报告里的一片干净。
    """
    path = db.format_abspath(book_id, 'TXT', index_is_id=True)
    if not path:
        return None, 'TXT format is missing'
    try:
        with open(path, 'rb') as stream:
            data = stream.read()
    except (IOError, OSError) as e:
        return None, 'cannot read TXT: %s' % e
    return txt_digest(data), None


def _title(db, book_id):
    return get_title_authors_text(db, book_id)


class TxtCheck(BaseCheck):
    '''本仓新增：TXT 的常见畸形检查（空文件、编码、BOM、控制字符、换行、超长行）。'''

    TXT_FORMATS = TXT_FORMATS

    def __init__(self, gui):
        BaseCheck.__init__(self, gui, 'formats:=txt')

    def perform_check(self, menu_key):
        handler = {
            'check_txt_empty': self.check_txt_empty,
            'check_txt_not_utf8': self.check_txt_not_utf8,
            'check_txt_bom': self.check_txt_bom,
            'check_txt_control_chars': self.check_txt_control_chars,
            'check_txt_mixed_newlines': self.check_txt_mixed_newlines,
            'check_txt_long_lines': self.check_txt_long_lines,
        }.get(menu_key)
        if handler is None:
            return error_dialog(self.gui, _('Quality Check failed'),
                                _('Unknown menu key for %s of \'%s\'') % ('TxtCheck', menu_key),
                                show=True, show_copy_button=False)
        handler()

    def _run(self, predicate, marked_text, status_msg_type, no_match_msg, report):
        def evaluate_book(book_id, db):
            digest, problem = _read_digest(self, db, book_id)
            if problem is not None:
                self.log('Unreadable TXT: <b>%s</b>' % _title(db, book_id))
                self.log.error('\t%s' % problem)
                return True
            if not predicate(digest):
                return False
            report(self, db, book_id, digest)
            return True

        self.check_all_files(evaluate_book, no_match_msg=no_match_msg,
                             marked_text=marked_text, status_msg_type=status_msg_type)

    # ------------------------------------------------------------------ 6 个检查项

    def check_txt_empty(self):
        def report(check, db, book_id, digest):
            check.log('Empty TXT: <b>%s</b>' % _title(db, book_id))
            if digest['size'] == 0:
                check.log('\t0 bytes')
            else:
                check.log('\t%d bytes, but nothing but whitespace' % digest['size'])

        self._run(hit_empty, 'txt_empty', _('TXT books with no content'),
                  _('All searched TXT books have content'), report)

    def check_txt_not_utf8(self):
        def report(check, db, book_id, digest):
            check.log('TXT is not valid UTF-8: <b>%s</b>' % _title(db, book_id))
            check.log('\tlooks like: %s' % (digest['encoding_hint'] or 'unknown'))
            if digest['decode_error_offset'] is not None:
                check.log('\tfirst invalid byte at offset %d' % digest['decode_error_offset'])

        self._run(hit_not_utf8, 'txt_not_utf8', _('TXT books that are not UTF-8'),
                  _('All searched TXT books are valid UTF-8'), report)

    def check_txt_bom(self):
        def report(check, db, book_id, digest):
            check.log('TXT starts with a byte order mark: <b>%s</b>' % _title(db, book_id))
            check.log('\t%s BOM' % digest['bom'])

        self._run(hit_bom, 'txt_bom', _('TXT books with a BOM'),
                  _('No searched TXT books start with a BOM'), report)

    def check_txt_control_chars(self):
        def report(check, db, book_id, digest):
            check.log('TXT contains control characters: <b>%s</b>' % _title(db, book_id))
            check.log('\t%d occurrence(s)' % digest['control_count'])
            if digest['control_first']:
                offset, code = digest['control_first']
                check.log('\tfirst at offset %d: 0x%02x' % (offset, code))
                if code == 0:
                    check.log.error('\tNUL bytes usually mean this is not a text file at all')

        self._run(hit_control_chars, 'txt_control_chars', _('TXT books with control characters'),
                  _('No searched TXT books contain control characters'), report)

    def check_txt_mixed_newlines(self):
        def report(check, db, book_id, digest):
            counts = digest['newlines']
            check.log('TXT mixes line endings: <b>%s</b>' % _title(db, book_id))
            check.log('\tCRLF %d / LF %d / CR %d' % (counts['crlf'], counts['lf'], counts['cr']))

        self._run(hit_mixed_newlines, 'txt_mixed_newlines', _('TXT books with mixed line endings'),
                  _('All searched TXT books use one kind of line ending'), report)

    def check_txt_long_lines(self):
        def report(check, db, book_id, digest):
            if digest['line_count'] <= 1 and digest['size'] > MAX_LINE_CHARS:
                check.log('TXT has no line break at all: <b>%s</b>' % _title(db, book_id))
                check.log('\t%d characters in one line' % digest['size'])
            else:
                check.log('TXT has an over-long line: <b>%s</b>' % _title(db, book_id))
                check.log('\tlongest line %d characters (limit %d)'
                          % (digest['longest_line'], MAX_LINE_CHARS))

        self._run(hit_long_lines, 'txt_long_lines', _('TXT books with over-long lines'),
                  _('All searched TXT books wrap at a sane line length'), report)
