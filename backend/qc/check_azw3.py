# -*- coding: utf-8 -*-
"""AZW3 checks for the MyBooks port.

**本文件是移植仓新增的代码**（上游的 ``check_mobi.py`` 只有 4 项：EBOK / ASIN / 分享开关 /
clipping limit，且它靠 ``mobi6.MinimalMobiReader`` 读 EXTH）。这四项查的是上游完全没碰的
东西：DRM、KF8 层缺失、封面记录缺失、记录表损坏。只读，不改书、不落盘。

只判 **AZW3 文件**：它挂在「MOBI / AZW3」这一组下（``cat = mobi``），而那一组的书单是
MOBI/AZW/AZW3 三种格式的并集，所以对没有 AZW3 格式的书直接跳过——`.mobi` 是 MOBI6 本来就
正常，不该报"没有 KF8 层"。

``azw3_facts()`` 与模块级 ``hit_*()`` 都是纯函数（只吃一个 facts 字典 / 读取器对象），
判定层因此不依赖真实文件也能单测。
"""
from __future__ import unicode_literals, division, absolute_import, print_function

__license__ = 'GPL v3'
__copyright__ = '2026, 黏菌（本仓新增代码，非上游移植部分）'

try:
    load_translations()
except NameError:
    pass  # load_translations() added in calibre 1.9

from .shim import error_dialog

from .check_base import BaseCheck
from .helpers import get_title_authors_text
from .mobi6 import MinimalMobiReader

AZW3_FORMATS = ['AZW3']

#: MOBI 头的 File version：6 = 老 MOBI6，8 = KF8（AZW3 的双层结构）。实测真书是 6 与 8，
#: calibre 转换出的 .azw3 是 8。
KF8_MIN_VERSION = 8
#: PalmDOC 加密类型（记录 0 的 0x0C）的取值含义，用于日志。
_ENCRYPTION_NAMES = {0: 'none', 1: 'old Mobipocket', 2: 'Mobipocket'}


def azw3_facts(reader):
    """把 ``MinimalMobiReader`` 的只读事实压成判定层要的结构（纯函数）。"""
    header = reader.book_header
    exth = header.exth if header is not None else None
    return {
        'version': header.version if header is not None else 0,
        'has_kf8': bool(reader.has_kf8),
        'encryption': reader.palmdoc_encryption,
        'bad_records': list(reader.bad_records),
        'record_count': reader.num_sections,
        'file_size': reader.file_size,
        'has_exth': exth is not None,
        'exth_count': len(exth.records) if exth is not None else 0,
        'cdetype': exth.cdetype if exth is not None else b'',
        'cover_offset': exth.cover_offset if exth is not None else None,
        'thumbnail_offset': exth.thumbnail_offset if exth is not None else None,
        'has_fake_cover': exth.has_fake_cover if exth is not None else None,
    }


# --------------------------------------------------------------------------- 判定（纯）

def hit_drm(facts):
    """MOBI 的 DRM 标在 PalmDOC 头的加密类型上（EXTH 里没有 DRM 字段）。"""
    return facts['encryption'] != 0


def hit_truncated(facts):
    return bool(facts['bad_records'])


def hit_missing_thumb(facts):
    """EXTH 201/202/203 一个都不在：这份 AZW3 里没有任何封面/缩略图记录。"""
    return (facts['cover_offset'] is None and facts['thumbnail_offset'] is None
            and facts['has_fake_cover'] is None)


def hit_mobi6_only(facts):
    """是 AZW3 却只有 MOBI6 头（version 6），没有 KF8 层——多半是 .mobi 被改了扩展名。"""
    return 0 < facts['version'] < KF8_MIN_VERSION


# --------------------------------------------------------------------------- 逐书日志

def _title(db, book_id):
    return get_title_authors_text(db, book_id)


class Azw3Check(BaseCheck):
    '''本仓新增：AZW3 的 DRM / KF8 层 / 封面记录 / 记录表完整性。'''

    AZW3_FORMATS = AZW3_FORMATS

    def __init__(self, gui):
        BaseCheck.__init__(self, gui, 'formats:=azw3')

    def perform_check(self, menu_key):
        handler = {
            'check_azw3_drm': self.check_azw3_drm,
            'check_azw3_truncated': self.check_azw3_truncated,
            'check_azw3_missing_thumb': self.check_azw3_missing_thumb,
            'check_azw3_mobi6_only': self.check_azw3_mobi6_only,
        }.get(menu_key)
        if handler is None:
            return error_dialog(self.gui, _('Quality Check failed'),
                                _('Unknown menu key for %s of \'%s\'') % ('Azw3Check', menu_key),
                                show=True, show_copy_button=False)
        handler()

    def _run(self, predicate, marked_text, status_msg_type, no_match_msg, report):
        def evaluate_book(book_id, db):
            if not db.has_format(book_id, 'AZW3', index_is_id=True):
                # 这一组的书单含 MOBI/AZW，但这四项只判 AZW3 文件
                return False
            path = db.format_abspath(book_id, 'AZW3', index_is_id=True)
            if not path:
                self.log('Unreadable AZW3: <b>%s</b>' % _title(db, book_id))
                self.log.error('\tAZW3 format is missing')
                return True
            try:
                with MinimalMobiReader(path, self.log) as reader:
                    facts = azw3_facts(reader)
            except Exception as e:
                # 读不了就是结构坏了：这里不能只写日志再返回 False——未命中书的逐书日志
                # 会被 driver 丢掉，那样报告里就只剩一片干净。
                self.log('Unreadable AZW3: <b>%s</b>' % _title(db, book_id))
                self.log.error('\t%s: %s' % (type(e).__name__, e))
                return True
            if not predicate(facts):
                return False
            report(self, db, book_id, facts)
            return True

        self.check_all_files(evaluate_book, no_match_msg=no_match_msg,
                             marked_text=marked_text, status_msg_type=status_msg_type)

    # ------------------------------------------------------------------ 4 个检查项

    def check_azw3_drm(self):
        def report(check, db, book_id, facts):
            check.log('AZW3 is DRM protected: <b>%s</b>' % _title(db, book_id))
            check.log('\tPalmDOC encryption type %d (%s)'
                      % (facts['encryption'],
                         _ENCRYPTION_NAMES.get(facts['encryption'], 'unknown')))

        self._run(hit_drm, 'azw3_drm', _('AZW3 books with DRM'),
                  _('No searched AZW3 books are DRM protected'), report)

    def check_azw3_truncated(self):
        def report(check, db, book_id, facts):
            check.log('AZW3 record table is damaged: <b>%s</b>' % _title(db, book_id))
            if -1 in facts['bad_records']:
                check.log.error('\tthe record table itself runs past the end of the file')
            else:
                check.log('\t%d bad record(s) of %d'
                          % (len(facts['bad_records']), facts['record_count']))
                check.log('\tfirst: #%d' % facts['bad_records'][0])

        self._run(hit_truncated, 'azw3_truncated', _('AZW3 books with a damaged record table'),
                  _('All searched AZW3 record tables are intact'), report)

    def check_azw3_missing_thumb(self):
        def report(check, db, book_id, facts):
            check.log('AZW3 has no cover records: <b>%s</b>' % _title(db, book_id))
            check.log('\tEXTH 201/202/203 are all missing (%d EXTH records total)'
                      % facts['exth_count'])
            if not facts['has_exth']:
                check.log.error('\tthere is no EXTH header at all')

        self._run(hit_missing_thumb, 'azw3_missing_thumb',
                  _('AZW3 books with no cover records'),
                  _('All searched AZW3 books carry cover records'), report)

    def check_azw3_mobi6_only(self):
        def report(check, db, book_id, facts):
            check.log('AZW3 has no KF8 layer: <b>%s</b>' % _title(db, book_id))
            check.log('\tMOBI header version %d (%d = MOBI6, %d = KF8)'
                      % (facts['version'], KF8_MIN_VERSION - 2, KF8_MIN_VERSION))
            check.log('\tlooks like a MOBI6 file with an .azw3 extension')

        self._run(hit_mobi6_only, 'azw3_mobi6_only', _('AZW3 books without a KF8 layer'),
                  _('All searched AZW3 books carry a KF8 layer'), report)
