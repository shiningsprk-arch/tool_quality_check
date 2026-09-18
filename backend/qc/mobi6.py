from __future__ import absolute_import, print_function

__license__   = 'GPL v3'
__copyright__ = '2012, Kovid Goyal <kovid@kovidgoyal.net>'

from .shim.sixshim.moves import range

try:
    load_translations()
except NameError:
    pass # load_translations() added in calibre 1.9

import struct
from io import BytesIO

from .shim.ebooks.metadata.mobi import MetadataUpdater
from .shim.ebooks.mobi import MobiError

class TopazError(ValueError):
    pass

class FireEXTHHeader(object):
    '''
    This is an extension of the calibre EXTHHeader class just for the
    purposes of getting the cdetype field

    移植说明（本仓改动，非上游原样）：上游只认下面四个 id、其余记录读完即丢。这里额外
    保留一份完整记录表 `records`，并把 AZW3 检查要用到的几个 id 解析成具名字段；同时给
    循环加了一道保护——上游遇到损坏的 num_items/size 会空转或越界切片。**只加读，不写**。
    '''
    def __init__(self, raw):
        self.doctype = raw[:4]
        self.length, self.num_items = struct.unpack('>LL', raw[4:12])
        raw = raw[12:]
        pos = 0
        left = self.num_items
        self.cdetype = ''
        self.asin = ''
        self.asin2 = ''
        self.clipping_limit = None
        # ---- 本仓新增：完整记录表 + 具名字段 ----
        # 100/101 是作者/出版社，**不是** DRM；MOBI 的 DRM 标在 PalmDOC 头的加密类型上。
        self.records = {}
        self.kf8_boundary = None      # 121 KF8 边界记录
        self.resource_count = None    # 125 资源记录数
        self.cover_offset = None      # 201 封面记录号
        self.thumbnail_offset = None  # 202 缩略图记录号
        self.has_fake_cover = None    # 203 是否有占位封面

        while left > 0:
            left -= 1
            if pos + 8 > len(raw):
                break
            idx, size = struct.unpack('>LL', raw[pos:pos + 8])
            # 本仓新增：size 明显不合法或越过缓冲区时停住，不按垃圾值继续走
            if size < 8 or pos + size > len(raw):
                break
            content = raw[pos + 8:pos + size]
            pos += size
            self.records[idx] = content
            if idx == 113:
                # asin
                self.asin = content
            elif idx == 121:
                self.kf8_boundary = self._be_int(content)
            elif idx == 125:
                self.resource_count = self._be_int(content)
            elif idx == 201:
                self.cover_offset = self._be_int(content)
            elif idx == 202:
                self.thumbnail_offset = self._be_int(content)
            elif idx == 203:
                self.has_fake_cover = self._be_int(content)
            elif idx == 401:
                # clippinglimit
                self.clipping_limit = ord(content)
            elif idx == 501:
                # cdetype
                self.cdetype = content
            elif idx == 504:
                # cdetype
                self.asin2 = content

    @staticmethod
    def _be_int(content):
        """EXTH 里的数值字段是大端 4 字节；长度不足时按高位补零读。"""
        return struct.unpack('>L', (content + b'\x00' * 4)[:4])[0]


class MinimalMobiHeader(object):

    def __init__(self, raw, log):
        self.log = log
        if len(raw) <= 16:
            self.exth_flag, self.exth = 0, None
            # 本仓新增：短头时给出安全默认值，免得读 version 的地方直接 AttributeError
            self.length = self.type = self.codepage = self.unique_id = self.version = 0
        else:
            self.exth_flag, = struct.unpack('>L', raw[0x80:0x84])
            self.length, self.type, self.codepage, self.unique_id, \
                self.version = struct.unpack('>LLLLL', raw[20:40])
            self.exth = None
            if self.exth_flag & 0x40:
                try:
                    self.exth = FireEXTHHeader(raw[16 + self.length:])
                except:
                    self.log.exception('Invalid EXTH header')
                    self.exth_flag = 0


class MinimalMobiReader(object):

    def __init__(self, filename, log):
        self.log = log

        stream = open(filename, 'rb')
        self.stream = stream

        raw = stream.read()
        if raw.startswith(b'TPZ'):
            raise TopazError(_('This is an Amazon Topaz book. It cannot be processed.'))

        self.header   = raw[0:72]
        self.name     = self.header[:32].replace(b'\x00', b'')
        self.num_sections, = struct.unpack('>H', raw[76:78])

        self.ident = self.header[0x3C:0x3C + 8].upper()
        if self.ident not in [b'BOOKMOBI', b'TEXTREAD']:
            raise MobiError(_('Unknown book type: %s') % repr(self.ident))

        self.sections = []
        self.section_headers = []
        for i in range(self.num_sections):
            offset, a1, a2, a3, a4 = struct.unpack('>LBBBB', raw[78 + i * 8:78 + i * 8 + 8])
            flags, val = a1, a2 << 16 | a3 << 8 | a4
            self.section_headers.append((offset, flags, val))

        def section(section_number):
            if section_number == self.num_sections - 1:
                end_off = len(raw)
            else:
                end_off = self.section_headers[section_number + 1][0]
            off = self.section_headers[section_number][0]
            return raw[off:end_off]

        for i in range(self.num_sections):
            self.sections.append((section(i), self.section_headers[i]))

        self.book_header = MinimalMobiHeader(self.sections[0][0], self.log) if self.sections else None

        # ---- 本仓新增的只读容器事实（check_azw3.py 用）----
        # 记录 0 的 PalmDOC 头 0x0C 是加密类型：0=未加密、1=旧 Mobipocket、2=Mobipocket。
        # MOBI 的 DRM 只体现在这里——EXTH 里没有 DRM 字段，100/101 是作者/出版社。
        head0 = self.sections[0][0] if self.sections else b''
        self.file_size = len(raw)
        self.palmdoc_encryption = struct.unpack('>H', head0[0x0C:0x0E])[0] if len(head0) >= 0x0E else 0
        self.encrypted = self.palmdoc_encryption != 0
        # MOBI 头的 File version：6 = 老 MOBI6，8 = KF8（AZW3 的双层结构）。实测两本真书
        # 正好是 6 与 8；calibre 转换出的 .azw3/.mobi 都是 8。
        self.has_kf8 = bool(self.book_header and self.book_header.version >= 8)
        self.bad_records = self._bad_records(raw)

    def _bad_records(self, raw):
        """记录表里越界或倒挂的记录号；``-1`` 表示记录表本身已超出文件（只读诊断）。"""
        bad = []
        if 78 + self.num_sections * 8 + 2 > len(raw):
            bad.append(-1)
        prev = -1
        for i, header in enumerate(self.section_headers):
            offset = header[0]
            if offset < prev or offset >= len(raw):
                bad.append(i)
            prev = offset
        return bad

    def __enter__(self):
        return self

    def __exit__(self, _type, value, traceback):
        if self.stream:
            self.stream.close()
            self.stream = None


class MinimalMobiUpdater(MetadataUpdater):

    def update(self, asin=None, cdetype=None):
        def update_exth_record(rec):
            recs.append(rec)
            if rec[0] in self.original_exth_records:
                self.original_exth_records.pop(rec[0])

        if self.type != b"BOOKMOBI":
                raise MobiError(_("Setting ASIN only supported for MOBI files of type 'BOOK'.")+"\n"
                                "\tThis is a '%s' file of type '%s'" % (self.type[0:4], self.type[4:8]))
        recs = []
        if asin is not None:
            update_exth_record((113, asin.encode(self.codec, 'replace')))
            update_exth_record((504, asin.encode(self.codec, 'replace')))
        if cdetype is not None:
            update_exth_record((501, cdetype))

        # Include remaining original EXTH fields
        for id_ in sorted(self.original_exth_records):
            recs.append((id_, self.original_exth_records[id_]))
        recs = sorted(recs, key=lambda x:(x[0],x[0]))

        exth = BytesIO()
        for code, data in recs:
            exth.write(struct.pack('>II', int(code), len(data) + 8))
            exth.write(data)
        exth = exth.getvalue()
        trail = len(exth) % 4
        pad = b'\0' * (4 - trail) # Always pad w/ at least 1 byte
        exth = [b'EXTH', struct.pack(b'>II', len(exth) + 12, len(recs)), exth, pad]
        exth = b''.join(exth)

        if getattr(self, 'exth', None) is None:
            raise MobiError(_('No existing EXTH record. Cannot update ASIN.'))

        self.create_exth(exth=exth)

