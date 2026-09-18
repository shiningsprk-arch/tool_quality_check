# -*- coding: utf-8 -*-
"""PDF checks for the MyBooks port.

**本文件是移植仓新增的代码，不是上游 Quality Check 的移植部分**（上游一个 PDF 检查都没有）。
只读：打开 PDF 取事实，不改书、不落盘（PyMuPDF 全程在内存里解）。

PyMuPDF 由宿主镜像提供（`requirements.txt` 里有 PyMuPDF，宿主的 `minify_pdf` 就在用）。
它不在时**不导入失败**：``_load_fitz()`` 返回 None，driver 会把 PDF 这一组记进 skipped，
报告里会写明原因——不会假装"PDF 全干净"。

与 ``check_txt`` 一样，判定与提取分开：``pdf_digest()`` 负责用 fitz 取事实，模块级的
``hit_*()`` 都是纯函数，只吃 digest，因此不依赖 fitz 也能单测。
"""
from __future__ import unicode_literals, division, absolute_import, print_function

__license__ = 'GPL v3'
__copyright__ = '2026, 黏菌（本仓新增代码，非上游移植部分）'

try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

import os

from .shim import error_dialog

from .check_base import BaseCheck
from .helpers import get_title_authors_text

PDF_FORMATS = ['PDF']

#: 平均每页超过这个体积就算"图片没压/DPI 过高"（阈值是常量，不给前端选项）。
BYTES_PER_PAGE_WARN = 1500000
#: 取文字的抽样页数（扫描件判定不需要读整本）。
TEXT_SAMPLE_PAGES = 5
#: 页尺寸的抽样页数（"混用尺寸"要跨整本看，所以多抽一些）。
SIZE_SAMPLE_PAGES = 30
#: 页尺寸容差：5% 以内算同一种尺寸。
#:
#: 阈值是拿真书定的：参考书库那本扫描绘本逐页尺寸在几个百分点内抖动（960×947 / 954×948 /
#: 996×945…），那是扫描/拍照的正常误差，不是缺陷——2% 容差会把整本书判成"混用"。而真正
#: 值得报的混排是两种页面格式：A4/A5 差 29%、A4/Letter 的高差 5.9%，都还在网内。
SIZE_TOLERANCE = 0.05


def _load_fitz():
    """PyMuPDF 的入口。新版把包名改成了 ``pymupdf``，``fitz`` 只是兼容别名。"""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        pass
    try:
        import fitz
        return fitz
    except ImportError:
        return None


def available():
    """PDF 检查能不能跑（driver 用它决定是否把这一组记进 skipped）。"""
    return _load_fitz() is not None


# --------------------------------------------------------------------------- 提取

def _sample_indices(pages, count):
    if pages <= count:
        return list(range(pages))
    step = (pages - 1) / float(count - 1) if count > 1 else 0
    picked = []
    for i in range(count):
        index = int(round(i * step))
        if index not in picked:
            picked.append(index)
    return picked


def pdf_digest(path):
    """用 PyMuPDF 把一个 PDF 压成判定层要的事实 → ``(digest, problem)``。

    ``problem`` 非空表示这份文件读不了（打不开、损坏、被截断），由调用方当命中处理：
    driver 只为命中的书保留逐书日志，未命中书的日志会被丢掉。
    """
    fitz = _load_fitz()
    if fitz is None:
        return None, 'PyMuPDF (pymupdf/fitz) is not available'

    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, 'cannot stat file: %s' % e

    try:
        doc = fitz.open(path)
    except Exception as e:
        return None, 'cannot open PDF: %s: %s' % (type(e).__name__, e)

    try:
        encrypted = bool(getattr(doc, 'is_encrypted', False))
        needs_pass = bool(getattr(doc, 'needs_pass', False))
        pages = int(getattr(doc, 'page_count', 0) or 0)
        digest = {
            'size': size,
            'pages': pages,
            'encrypted': encrypted,
            'needs_pass': needs_pass,
            # PyMuPDF 会尽力修复损坏的 PDF：修好了也能打开，这时它自己会置这个标志。
            # 实测：把 %PDF 头改成垃圾字节 → is_repaired=True；截断的文件不一定置位。
            'repaired': bool(getattr(doc, 'is_repaired', False)),
            # 下面几个在"读不到"时是 None（哨兵），判定层据此区分"没有"与"不知道"
            'toc_entries': 0,
            'text_chars': 0,
            'text_sampled': 0,
            'page_sizes': [],
            'size_sampled': 0,
            'partial': False,   # 取样/取目录时出错，结论要打折
        }
        if needs_pass:
            # 需要口令：页级事实**读不到**（不是"空"）。用 None 当哨兵，否则 0 会被
            # "无文字层/无目录"当成结论——加密件因此报出一堆它根本不知道的事。
            digest['toc_entries'] = None
            digest['text_chars'] = None
            return digest, None

        try:
            digest['toc_entries'] = len(doc.get_toc() or [])
        except Exception:
            digest['toc_entries'] = None   # 取目录失败同样是"不知道"，不是"没有"
            digest['partial'] = True

        for index in _sample_indices(pages, TEXT_SAMPLE_PAGES):
            try:
                digest['text_chars'] += len((doc[index].get_text() or '').strip())
                digest['text_sampled'] += 1
            except Exception:
                digest['partial'] = True

        for index in _sample_indices(pages, SIZE_SAMPLE_PAGES):
            try:
                rect = doc[index].rect
                digest['page_sizes'].append((round(rect.width, 1), round(rect.height, 1)))
                digest['size_sampled'] += 1
            except Exception:
                digest['partial'] = True
        return digest, None
    finally:
        try:
            doc.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- 判定（纯）

def _dominant_size(sizes):
    counts = {}
    for size in sizes:
        counts[size] = counts.get(size, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _size_differs(a, b):
    return any(abs(x - y) > max(x, y) * SIZE_TOLERANCE for x, y in zip(a, b))


def _page_facts_usable(digest):
    """页级结论（文字层 / 目录 / 页尺寸）能不能信。

    加固过的文件与加密件都不能：前者的页面数据是 PyMuPDF 尽力抢救出来的碎片，据此说
    "没有文字层""没有目录"只会把"文件坏了"这一个结论稀释成三条噪声。
    """
    return digest['pages'] > 0 and not digest.get('repaired')


def hit_unreadable(digest):
    """打不开、零页、或**靠修复才打开**（文件结构已经坏了）。

    排除"加密打不开"那种（那是加密检查的事）。
    """
    if digest['encrypted']:
        return False
    return digest['pages'] <= 0 or bool(digest.get('repaired'))


def hit_encrypted(digest):
    return digest['encrypted']


def hit_no_text_layer(digest):
    """真抽过页、且抽到的全没有文字才算——加密件一页都读不到，不能说它"没有文字层"。"""
    if not _page_facts_usable(digest):
        return False
    return (digest['text_sampled'] or 0) > 0 and digest['text_chars'] == 0


def hit_oversized(digest):
    return digest['pages'] > 0 and digest['size'] / float(digest['pages']) > BYTES_PER_PAGE_WARN


def hit_no_toc(digest):
    return _page_facts_usable(digest) and digest['toc_entries'] == 0


def hit_mixed_page_size(digest):
    if not _page_facts_usable(digest):
        return False
    sizes = digest['page_sizes']
    if len(sizes) < 2:
        return False
    base = _dominant_size(sizes)
    return any(_size_differs(size, base) for size in sizes)


# --------------------------------------------------------------------------- 逐书日志

def _title(db, book_id):
    return get_title_authors_text(db, book_id)


class PdfCheck(BaseCheck):
    '''本仓新增：PDF 的常见缺陷检查（打不开、加密、无文字层、页尺寸混用、体积、无目录）。'''

    PDF_FORMATS = PDF_FORMATS

    def __init__(self, gui):
        BaseCheck.__init__(self, gui, 'formats:=pdf')

    def perform_check(self, menu_key):
        handler = {
            'check_pdf_unreadable': self.check_pdf_unreadable,
            'check_pdf_encrypted': self.check_pdf_encrypted,
            'check_pdf_no_text_layer': self.check_pdf_no_text_layer,
            'check_pdf_mixed_page_size': self.check_pdf_mixed_page_size,
            'check_pdf_oversized': self.check_pdf_oversized,
            'check_pdf_no_toc': self.check_pdf_no_toc,
        }.get(menu_key)
        if handler is None:
            return error_dialog(self.gui, _('Quality Check failed'),
                                _('Unknown menu key for %s of \'%s\'') % ('PdfCheck', menu_key),
                                show=True, show_copy_button=False)
        handler()

    def _run(self, predicate, marked_text, status_msg_type, no_match_msg, report):
        def evaluate_book(book_id, db):
            path = db.format_abspath(book_id, 'PDF', index_is_id=True)
            if not path:
                self.log('Unreadable PDF: <b>%s</b>' % _title(db, book_id))
                self.log.error('\tPDF format is missing')
                return True
            digest, problem = pdf_digest(path)
            if problem is not None:
                self.log('Unreadable PDF: <b>%s</b>' % _title(db, book_id))
                self.log.error('\t%s' % problem)
                return True
            if not predicate(digest):
                return False
            report(self, db, book_id, digest)
            return True

        self.check_all_files(evaluate_book, no_match_msg=no_match_msg,
                             marked_text=marked_text, status_msg_type=status_msg_type)

    # ------------------------------------------------------------------ 6 个检查项

    def check_pdf_unreadable(self):
        def report(check, db, book_id, digest):
            check.log('Cannot read PDF: <b>%s</b>' % _title(db, book_id))
            if digest.get('repaired'):
                check.log('\tthe file is damaged: it only opened after PyMuPDF repaired it')
            check.log('\t%d page(s) reported, %d bytes on disk' % (digest['pages'], digest['size']))

        self._run(hit_unreadable, 'pdf_unreadable', _('PDF books that cannot be read'),
                  _('All searched PDF books opened fine'), report)

    def check_pdf_encrypted(self):
        def report(check, db, book_id, digest):
            check.log('PDF is encrypted: <b>%s</b>' % _title(db, book_id))
            if digest['needs_pass']:
                check.log.error('\ta password is required to open it')
            else:
                check.log('\tno password needed, but permissions are restricted')

        self._run(hit_encrypted, 'pdf_encrypted', _('PDF books that are encrypted'),
                  _('No searched PDF books are encrypted'), report)

    def check_pdf_no_text_layer(self):
        def report(check, db, book_id, digest):
            check.log('PDF has no text layer: <b>%s</b>' % _title(db, book_id))
            check.log('\tno text in the %d sampled page(s) — scanned images only?'
                      % digest['text_sampled'])
            if digest['partial']:
                check.log('\tsome pages could not be read, so this may be a false positive')

        self._run(hit_no_text_layer, 'pdf_no_text_layer', _('PDF books without a text layer'),
                  _('All searched PDF books have a text layer'), report)

    def check_pdf_mixed_page_size(self):
        def report(check, db, book_id, digest):
            base = _dominant_size(digest['page_sizes'])
            others = [s for s in digest['page_sizes'] if _size_differs(s, base)]
            check.log('PDF mixes page sizes: <b>%s</b>' % _title(db, book_id))
            check.log('\tmostly %.1f x %.1f pt, but also %s'
                      % (base[0], base[1], ', '.join('%.1f x %.1f' % s for s in others[:4])))

        self._run(hit_mixed_page_size, 'pdf_mixed_page_size', _('PDF books with mixed page sizes'),
                  _('All searched PDF books keep one page size'), report)

    def check_pdf_oversized(self):
        def report(check, db, book_id, digest):
            per_page = digest['size'] / float(digest['pages'])
            check.log('PDF is heavy per page: <b>%s</b>' % _title(db, book_id))
            check.log('\t%.1f MB / %d page(s) = %.2f MB per page (limit %.2f)'
                      % (digest['size'] / 1048576.0, digest['pages'], per_page / 1048576.0,
                         BYTES_PER_PAGE_WARN / 1048576.0))

        self._run(hit_oversized, 'pdf_oversized', _('PDF books that are heavy per page'),
                  _('All searched PDF books are a sane size per page'), report)

    def check_pdf_no_toc(self):
        def report(check, db, book_id, digest):
            check.log('PDF has no table of contents: <b>%s</b>' % _title(db, book_id))
            check.log('\t%d page(s), no outline entries' % digest['pages'])

        self._run(hit_no_toc, 'pdf_no_toc', _('PDF books without a table of contents'),
                  _('All searched PDF books have a table of contents'), report)
