# -*- coding: utf-8 -*-
"""用本机 calibre 造一个"多格式 + 定向缺陷"的验证书库，给 PDF / AZW3 / TXT 那 16 项检查做回归。

为什么需要它：MyBooks 的参考书库里 PDF / AZW3 / TXT 各只有 1 本，而新检查要验的是
"该命中的命中、该干净的不误报"——那得有一批可控样本。素材全部来自**真书 + calibre 转换 +
定向改造**，不手搓格式；只有一处例外会明确标注：calibre 产不出 DRM 文件，AZW3 的 DRM 样本
是把真文件的 PalmDOC 加密字段改成 2（这正是 DRM 文件标记自己的那 2 个字节）。

产物落在临时目录（体积与版权原因不进仓库）。用法：

    python scripts/make_format_library.py                 # 造库并打印预期命中表
    python scripts/make_format_library.py --verify        # 造完立刻按预期表校验
    python scripts/smoke_offline.py <打印出来的库路径>     # 用它跑全量检查

`--verify` 复用 `smoke_offline.py` 的假宿主（sqlite3 直读 metadata.db），只比对新检查
（其它元数据类检查在转换出来的书上必然大量命中，与新检查无关，故不参与比对）。
"""
from __future__ import unicode_literals, print_function

import argparse
import io
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_SOURCE = os.path.abspath(os.path.join(
    REPO, '..', 'mybooks源码', 'mybooks-v4.2.1', 'tests', 'library'))

# 新加的那 16 项：验证只比对它们（元数据类检查在转换出来的书上必然大量命中）
NEW_CHECKS = (
    'check_pdf_unreadable', 'check_pdf_encrypted', 'check_pdf_no_text_layer',
    'check_pdf_mixed_page_size', 'check_pdf_oversized', 'check_pdf_no_toc',
    'check_txt_empty', 'check_txt_not_utf8', 'check_txt_bom',
    'check_txt_control_chars', 'check_txt_mixed_newlines', 'check_txt_long_lines',
    'check_azw3_drm', 'check_azw3_truncated', 'check_azw3_missing_thumb',
    'check_azw3_mobi6_only',
)

EBOOK_CONVERT = 'ebook-convert'
CALIBREDB = 'calibredb'


# --------------------------------------------------------------------------- 小工具

def _run(cmd, **kwargs):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    out = proc.communicate()[0]
    return proc.returncode, (out or b'').decode('utf-8', 'replace')


def _require(tool):
    path = shutil.which(tool)
    if not path:
        print('找不到 %s：本脚本需要本机 calibre（%s 与 %s）' % (tool, EBOOK_CONVERT, CALIBREDB))
        return None
    return path


def _convert(src, dst, *extra):
    cmd = [EBOOK_CONVERT, os.path.abspath(src), os.path.abspath(dst)] + list(extra)
    code, out = _run(cmd)
    if code != 0 or not os.path.exists(dst):
        raise RuntimeError('转换失败：%s\n%s' % (' '.join(cmd), out[-2000:]))
    return dst


def _fitz():
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


def _read_epub(src):
    """参考书库里的 EPUB 路径（挑几本，够覆盖不同长度就行）。"""
    found = []
    for root, _dirs, files in os.walk(src):
        for name in files:
            if name.lower().endswith('.epub'):
                found.append(os.path.join(root, name))
    return sorted(found)


# --------------------------------------------------------------------------- 缺陷变体

def strip_cover_epub(src, dst):
    """去封面版 EPUB：删掉封面图、去掉 OPF 里的 cover meta 与 guide 引用。

    用来试造"AZW3 里没有封面记录"的样本——calibre 只在书有封面时才写 EXTH 201/202。
    """
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            base = os.path.basename(item.filename).lower()
            if base.startswith('cover') and base.endswith(('.jpg', '.jpeg', '.png', '.gif')):
                continue
            data = zin.read(item.filename)
            if item.filename.lower().endswith('.opf'):
                text = data.decode('utf-8', 'replace')
                text = re.sub(r'<meta[^>]*name="cover"[^>]*/>', '', text)
                text = re.sub(r'<reference[^>]*type="cover"[^>]*/?>', '', text)
                data = text.encode('utf-8')
            zout.writestr(item, data)
    return dst


def pdf_rasterized(src, dst):
    """把真 PDF 每页位图化后重排 → 没有文字层（等价于扫描件）。"""
    fitz = _fitz()
    doc = fitz.open(src)
    out = fitz.open()
    try:
        for page in doc:
            pixmap = page.get_pixmap(dpi=96)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, pixmap=pixmap)
        out.save(dst)
    finally:
        out.close()
        doc.close()
    return dst


def pdf_encrypted(src, dst):
    fitz = _fitz()
    doc = fitz.open(src)
    try:
        doc.save(dst, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='secret')
    finally:
        doc.close()
    return dst


def pdf_mixed_page_size(dst, text='mixed page sizes'):
    fitz = _fitz()
    doc = fitz.open()
    try:
        doc.new_page(width=595, height=842).insert_text((72, 100), text)
        doc.new_page(width=420, height=595).insert_text((72, 100), text)
        doc.save(dst)
    finally:
        doc.close()
    return dst


def pdf_jitter_sizes(dst, sizes=((960.0, 947.2), (954.0, 948.0), (996.0, 945.0), (972.0, 936.0))):
    """逐页尺寸只在几个百分点内抖动（扫描/拍照的常态）→ **不该**报"页面尺寸混用"。

    阈值就是拿参考书库那本扫描绘本定的：它 30 页里有 29 页是这种抖动，只有 1 页真的小 31%。
    """
    fitz = _fitz()
    doc = fitz.open()
    try:
        for width, height in sizes:
            doc.new_page(width=width, height=height).insert_text((72, 100), 'scan jitter')
        doc.save(dst)
    finally:
        doc.close()
    return dst


def pdf_heavy(dst, pages=2):
    """每页塞一张大噪点图 → 单页体积远超阈值（模拟图片没压/DPI 过高）。"""
    from PIL import Image
    import random
    fitz = _fitz()
    width, height = 1500, 2000
    noise = random.Random(20260918).randbytes(width * height)
    image_path = dst + '.png'
    Image.frombytes('L', (width, height), noise).save(image_path)
    doc = fitz.open()
    try:
        for _ in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_image(page.rect, filename=image_path)
        doc.save(dst)
    finally:
        doc.close()
        os.remove(image_path)
    return dst


def truncate(path, keep=0.6):
    with open(path, 'rb') as stream:
        raw = stream.read()
    with open(path, 'wb') as stream:
        stream.write(raw[:int(len(raw) * keep)])
    return path


def scramble_header(path):
    """把 PDF 头部几个字节改成垃圾：结构受损，PyMuPDF 只能靠"修复"打开（is_repaired）。"""
    with open(path, 'rb') as stream:
        raw = bytearray(stream.read())
    raw[:8] = b'GARBAGE!'
    with open(path, 'wb') as stream:
        stream.write(bytes(raw))
    return path


def patch_palmdoc_encryption(path, kind=2):
    """把记录 0 的 PalmDOC 加密类型改成 2（Mobipocket）——DRM 文件就是这样标自己的。

    这是本脚本唯一的"手改字节"：calibre 不产 DRM 文件，而这条检查（以及宿主里的真实
    DRM 书）必须验。偏移的定位方式与 `qc/mobi6.py` 读的一致：PalmDB 记录表第 0 项。
    """
    with open(path, 'rb') as stream:
        raw = bytearray(stream.read())
    record0 = struct.unpack('>L', raw[78:82])[0]
    struct.pack_into('>H', raw, record0 + 0x0C, kind)
    with open(path, 'wb') as stream:
        stream.write(bytes(raw))
    return path


def txt_variants(source_txt, out_dir):
    """从 calibre 产出的真 TXT 派生各种编码/换行畸形（改字节，不手搓文本）。"""
    with open(source_txt, 'rb') as stream:
        raw = stream.read()
    text = raw.decode('utf-8', 'replace')
    made = {}

    made['txt-gbk'] = os.path.join(out_dir, 'txt-gbk.txt')
    with open(made['txt-gbk'], 'wb') as stream:
        stream.write(text.encode('gb18030', 'replace'))

    made['txt-bom'] = os.path.join(out_dir, 'txt-bom.txt')
    with open(made['txt-bom'], 'wb') as stream:
        stream.write(b'\xef\xbb\xbf' + raw)

    made['txt-mixed-newlines'] = os.path.join(out_dir, 'txt-mixed-newlines.txt')
    lines = text.splitlines(True)
    half = max(1, len(lines) // 2)
    mixed = ''.join(lines[:half])
    mixed += ''.join(line.replace('\n', '\r\n') for line in lines[half:])
    with open(made['txt-mixed-newlines'], 'wb') as stream:
        stream.write(mixed.encode('utf-8'))

    made['txt-nul'] = os.path.join(out_dir, 'txt-nul.txt')
    with open(made['txt-nul'], 'wb') as stream:
        stream.write(raw[:len(raw) // 2] + b'\x00' + raw[len(raw) // 2:])

    made['txt-one-line'] = os.path.join(out_dir, 'txt-one-line.txt')
    with open(made['txt-one-line'], 'wb') as stream:
        stream.write(''.join(text.split()).encode('utf-8'))

    made['txt-blank'] = os.path.join(out_dir, 'txt-blank.txt')
    with open(made['txt-blank'], 'wb') as stream:
        stream.write(b'   \n\n\t\n')
    return made


# --------------------------------------------------------------------------- 书库组装

def build(source_library, out_dir):
    if _require(EBOOK_CONVERT) is None or _require(CALIBREDB) is None:
        return None

    epub_sources = _read_epub(source_library)
    if not epub_sources:
        print('参考书库里没有 EPUB：%s' % source_library)
        return None
    primary = epub_sources[0]
    secondary = epub_sources[-1] if len(epub_sources) > 1 else epub_sources[0]
    stage = os.path.join(out_dir, 'stage')
    os.makedirs(stage, exist_ok=True)
    library = os.path.join(out_dir, 'library')

    print('源书：%s' % os.path.basename(primary))
    files = []          # (path, name, hits, note, post)

    def add(path, name, hits, note='', post=None):
        # ext 记住这份 fixture 是哪种格式：入库后 calibre 会在同一个书目录里放一个 .opf
        # 元数据备份，post 必须按扩展名挑准文件（按字典序会挑到 .opf 上）。
        files.append({'path': path, 'name': name, 'hits': set(hits), 'ext':
                      os.path.splitext(path)[1].lower().lstrip('.'),
                      'note': note, 'post': post})

    def out(name):
        return os.path.join(stage, name)

    # EPUB 原样（保证 EPUB 类检查有书可跑）
    shutil.copy2(primary, out('epub-plain.epub'))
    add(out('epub-plain.epub'), 'epub-plain', [])
    # 去封面版：用来试造"AZW3 里没有封面记录"的样本
    strip_cover_epub(secondary, out('epub-coverless.epub'))
    add(out('epub-coverless.epub'), 'epub-coverless', [], note='仅作为去封面 AZW3 的源')

    # MOBI（calibre 9.6 的 .mobi 输出是 MOBI6：上游那 4 项与新检查的格式门控样本）
    _convert(primary, out('mobi-plain.mobi'))
    add(out('mobi-plain.mobi'), 'mobi-plain', [])

    # AZW3：KF8 / MOBI6 改名 / 截断 / DRM / 无封面
    _convert(primary, out('azw3-kf8.azw3'))
    add(out('azw3-kf8.azw3'), 'azw3-kf8', [],
        note='calibre 正常输出（version 8 + EXTH 201/202）')
    shutil.copy2(out('mobi-plain.mobi'), out('azw3-mobi6.azw3'))
    add(out('azw3-mobi6.azw3'), 'azw3-mobi6', ['check_azw3_mobi6_only'],
        note='MOBI6 文件改了扩展名（version 6）')
    shutil.copy2(out('azw3-kf8.azw3'), out('azw3-truncated.azw3'))
    add(out('azw3-truncated.azw3'), 'azw3-truncated', ['check_azw3_truncated'],
        note='进库后截断到 60%（记录表越界）', post=lambda p: truncate(p, 0.6))
    shutil.copy2(out('azw3-kf8.azw3'), out('azw3-drm.azw3'))
    add(out('azw3-drm.azw3'), 'azw3-drm', ['check_azw3_drm'],
        note='手工把 PalmDOC 加密字段改成 2（calibre 不产 DRM）',
        post=lambda p: patch_palmdoc_encryption(p, 2))
    _convert(out('epub-coverless.epub'), out('azw3-nocover.azw3'))
    add(out('azw3-nocover.azw3'), 'azw3-nocover', ['check_azw3_missing_thumb'],
        note='源 EPUB 去掉了封面')

    # PDF
    _convert(primary, out('pdf-normal.pdf'))
    add(out('pdf-normal.pdf'), 'pdf-normal', [])
    shutil.copy2(out('pdf-normal.pdf'), out('pdf-truncated.pdf'))
    add(out('pdf-truncated.pdf'), 'pdf-truncated', ['check_pdf_unreadable'],
        note='进库后截断到 20%：只报"要靠修复才打开"，不再顺带报没有文字层/目录',
        post=lambda p: truncate(p, 0.2))
    shutil.copy2(out('pdf-normal.pdf'), out('pdf-damaged.pdf'))
    add(out('pdf-damaged.pdf'), 'pdf-damaged', ['check_pdf_unreadable'],
        note='进库后把 %PDF 头改成垃圾（要靠修复才打得开）',
        post=lambda p: scramble_header(p))
    pdf_rasterized(out('pdf-normal.pdf'), out('pdf-rasterized.pdf'))
    add(out('pdf-rasterized.pdf'), 'pdf-rasterized',
        ['check_pdf_no_text_layer', 'check_pdf_no_toc', 'check_pdf_oversized'],
        note='每页位图化重排（等价扫描件）：没了 outline，且每页约 2.4MB')
    pdf_encrypted(out('pdf-normal.pdf'), out('pdf-encrypted.pdf'))
    add(out('pdf-encrypted.pdf'), 'pdf-encrypted', ['check_pdf_encrypted'],
        note='要口令：文字层/目录属于"读不到"，不该被说成"没有"')
    pdf_mixed_page_size(out('pdf-mixed-pages.pdf'))
    add(out('pdf-mixed-pages.pdf'), 'pdf-mixed-pages',
        ['check_pdf_mixed_page_size', 'check_pdf_no_toc'],
        note='A4+A5 两页：真混排要报（唯一那条 no_toc 是重建件没有 outline）')
    pdf_jitter_sizes(out('pdf-jitter.pdf'))
    add(out('pdf-jitter.pdf'), 'pdf-jitter', ['check_pdf_no_toc'],
        note='逐页只有几个百分点抖动（真书常态）→ 不该报混用')
    pdf_heavy(out('pdf-heavy.pdf'))
    add(out('pdf-heavy.pdf'), 'pdf-heavy',
        ['check_pdf_oversized', 'check_pdf_no_text_layer', 'check_pdf_no_toc'],
        note='每页一张大噪点图：只有图没有字，也没有 outline')

    # TXT
    _convert(primary, out('txt-plain.txt'))
    plain_has_bom = open(out('txt-plain.txt'), 'rb').read(3) == b'\xef\xbb\xbf'
    add(out('txt-plain.txt'), 'txt-plain', ['check_txt_bom'] if plain_has_bom else [],
        note='calibre 输出的原样 TXT')
    for name, path in sorted(txt_variants(out('txt-plain.txt'), stage).items()):
        hits = {
            'txt-gbk': ['check_txt_not_utf8'],
            'txt-bom': ['check_txt_bom'],
            'txt-mixed-newlines': ['check_txt_mixed_newlines'],
            'txt-nul': ['check_txt_control_chars'],
            'txt-one-line': ['check_txt_long_lines'],
            'txt-blank': ['check_txt_empty'],
        }[name]
        if name == 'txt-bom' and plain_has_bom:
            hits = []       # 源 TXT 本来就带 BOM 的话，这个样本说明不了什么
        add(path, name, hits)

    # 入库：一本一次调用并显式给书名——calibre 默认拿文件**内嵌元数据**当标题，那样
    # 二十个变体会挤在同一个书名下，预期表就没法逐本对照了。
    if os.path.isdir(library):
        shutil.rmtree(library)
    os.makedirs(library, exist_ok=True)
    print('入库（%d 本）…' % len(files))
    for item in files:
        code, output = _run([CALIBREDB, '--with-library', library, 'add',
                             '--title', item['name'], item['path']])
        if code != 0:
            print('calibredb add 失败（%s）：\n%s' % (item['name'], output[-1000:]))
            return None

    # 入库后对号，执行需要"先入库再改"的变体（截断、DRM 标记）
    books = _locate_in_library(library)
    expected = {}
    for item in files:
        record = _match_fixture(books, item['name'])
        if record is None:
            print('警告：书库里找不到 %s，跳过' % item['name'])
            continue
        if item['post']:
            target = record['files'].get(item['ext'])
            if target is None:
                print('警告：%s 的书目录里没有 .%s，post 跳过' % (item['name'], item['ext']))
            else:
                item['post'](target)
        item['formats'] = sorted(record['files'].keys())
        expected[record['title']] = item

    _write_expected(library, expected)
    _print_table(library, expected)
    return library


def _locate_in_library(library):
    """把书库里的书对回 fixture 名字（calibredb 用文件名当标题，可能改大小写）。"""
    conn = sqlite3.connect(os.path.join(library, 'metadata.db'))
    conn.row_factory = sqlite3.Row
    books = []
    for row in conn.execute('SELECT id, title, path FROM books'):
        book_dir = os.path.join(library, row['path'])
        files = {}
        for name in os.listdir(book_dir):
            ext = os.path.splitext(name)[1].lower().lstrip('.')
            files[ext] = os.path.join(book_dir, name)
        books.append({'title': row['title'], 'id': row['id'], 'files': files})
    conn.close()
    return books


def _match_fixture(books, fixture_name):
    """按书名（去分隔符、忽略大小写）找 fixture 对应的那本书。"""
    def norm(text):
        return re.sub(r'[^a-z0-9]+', '', text.lower())

    wanted = norm(fixture_name)
    for book in books:
        if norm(book['title']) == wanted or wanted in norm(book['title']):
            return book
    return None


def _write_expected(library, expected):
    payload = {}
    for title, item in expected.items():
        payload[title] = {'fixture': item['name'], 'expected': sorted(item['hits']),
                          'note': item['note'], 'formats': item.get('formats') or []}
    path = os.path.join(library, '..', 'expected.json')
    with io.open(os.path.abspath(path), 'w', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return os.path.abspath(path)


def _print_table(library, expected):
    print('\n素材库：%s' % library)
    print('%-20s %-10s %s' % ('fixture', 'formats', '预期命中（新检查）'))
    print('-' * 78)
    for title, item in sorted(expected.items(), key=lambda kv: kv[1]['name']):
        print('%-20s %-10s %s' % (item['name'], ','.join(item.get('formats') or []),
                                  ', '.join(sorted(item['hits'])) or '（应当干净）'))
        if item['note']:
            print('%-31s ↳ %s' % ('', item['note']))


# --------------------------------------------------------------------------- 校验

def verify(library):
    """跑全量检查（全支持项），只比对新检查的命中是否与预期逐本一致。"""
    sys.path.insert(0, HERE)
    import smoke_offline

    expected_path = os.path.abspath(os.path.join(library, '..', 'expected.json'))
    with io.open(expected_path, encoding='utf-8') as stream:
        expected = json.loads(stream.read())

    driver = smoke_offline.load_package()
    calibre = smoke_offline.FakeCalibre(library)
    work_root = os.path.abspath(os.path.join(REPO, 'dist', '_format_smoke'))
    api = smoke_offline.FakeApi(calibre, work_root)
    keys = [c['key'] for c in driver.describe_checks() if c['supported']]

    report = driver.run_checks(
        api=api,
        book_ids=calibre.all_book_ids(),
        check_keys=keys,
        options={'qc': {'maxTags': 5},
                 'cover': {'mode': 'dimensions', 'operator': 'less than',
                           'image_width': 600, 'image_height': 800}},
        progress_cb=None,
        cancel_event=threading.Event(),
        cover_root=os.path.join(work_root, 'covers'),
    )

    if report.get('errors'):
        print('\n未处理异常 %d 条：' % len(report['errors']))
        for err in report['errors'][:10]:
            print('  %-24s book=%s %s' % (err.get('check'), err.get('book_id'), err.get('error')))
    skipped = report.get('skipped') or {}
    never_ran = [k for k in keys
                 if k not in report['per_check'] and k not in skipped]
    if never_ran:
        print('\n漏跑的检查项 %d 个：%s' % (len(never_ran), ', '.join(never_ran[:8])))

    actual = {}
    for book in report['books']:
        hits = set(issue['check'] for issue in book['issues'] if issue['check'] in NEW_CHECKS)
        actual[book['title']] = hits

    failures = []
    print('\n%-20s %-28s %s' % ('fixture', '预期', '实际'))
    print('-' * 78)
    for title, item in sorted(expected.items(), key=lambda kv: kv[1]['fixture']):
        want = set(item['expected'])
        got = actual.get(title, set())
        ok = want == got
        if not ok:
            failures.append((item['fixture'], want, got))
        print('%-20s %-28s %s%s' % (
            item['fixture'], ', '.join(sorted(want)) or '（干净）',
            ', '.join(sorted(got)) or '（干净）', '' if ok else '   ← 不一致'))

    extra_books = sorted(set(actual) - set(expected))
    if extra_books:
        print('\n素材库里出现了预期表之外的书：%s' % ', '.join(extra_books))
        failures.append(('(extra books)', set(), set(extra_books)))

    if report.get('errors') or never_ran:
        failures.append(('(smoke)', set(), set()))
    if failures:
        print('\n✗ 校验不通过：%d 处' % len(failures))
        return 1
    print('\n✔ 全部 fixture 的命中与预期一致，且 %d 项检查都跑到（或有 skipped 说明）' % len(keys))
    return 0


def main():
    parser = argparse.ArgumentParser(description='造 PDF/AZW3/TXT 验证素材库')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='参考 calibre 书库（取 EPUB 当源书）')
    parser.add_argument('--out', default=os.path.join(tempfile.gettempdir(), 'qc_format_library'),
                        help='素材库输出目录（默认系统临时目录）')
    parser.add_argument('--verify', action='store_true', help='造完立即按预期表校验')
    parser.add_argument('--only-verify', action='store_true', help='跳过造库，只对现有素材库跑校验')
    args = parser.parse_args()

    source = os.path.abspath(args.source)
    out_dir = os.path.abspath(args.out)
    library = os.path.join(out_dir, 'library')

    if not args.only_verify:
        if not os.path.isdir(source):
            print('参考书库不存在：%s' % source)
            return 2
        os.makedirs(out_dir, exist_ok=True)
        library = build(source, out_dir)
        if library is None:
            return 2
    elif not os.path.isdir(library):
        print('素材库不存在：%s（先跑一次不带 --only-verify 的）' % library)
        return 2

    if args.verify or args.only_verify:
        return verify(library)
    return 0


if __name__ == '__main__':
    sys.exit(main())
