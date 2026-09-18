# -*- coding: utf-8 -*-
"""质量体检的离线回归测试：不依赖 MyBooks，也不依赖 calibre。

`scripts/smoke_offline.py` 拿真实书库跑全部检查，但它替换掉的是宿主，所以**宿主这一侧的
约定它验证不了**——那些正是这里要守的东西：

* 宿主 `CoreAPI.calibre.get_data_as_dict()` 的键名（标题排序列叫 ``sort``，曾经按
  ``title_sort`` 去读，于是 `check_title_sort` 把整库都判成有问题）；
* 封面判定方式（mode）到上游 `CoverOptionsDialog` 四个选项的映射，尤其是"用户没要求
  封面检查"必须回答 Rejected，否则会白遍历全库并解码每一张封面；
* 报告筛选的计数口径；
* 元数据缺字段时的哨兵语义（不能让 ``pubdate == timestamp`` 这类判等把整库误报）。

用法：``python -m pytest tests/test_quality_check_core.py``
"""
import ast
import datetime
import importlib
import importlib.util
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, '..', 'backend'))
PKG_NAME = 'quality_check_backend_under_test'


@pytest.fixture(scope='session')
def backend_pkg():
    """按宿主 toolbox_manager 的方式把 backend/ 加载成一个包。"""
    spec = importlib.util.spec_from_file_location(
        PKG_NAME, os.path.join(BACKEND, '__init__.py'),
        submodule_search_locations=[BACKEND])
    package = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = package
    spec.loader.exec_module(package)
    return package


def submodule(name):
    return importlib.import_module(PKG_NAME + '.' + name)


# --------------------------------------------------------------------------- 宿主替身

class FakeCalibre(object):
    """只提供 adapter/driver 真正用到的那几个 CoreAPI.calibre 方法。"""

    def __init__(self, records, covers=None, paths=None):
        self._records = records
        self._covers = covers or {}
        self._paths = paths or {}
        self.searched = []

    def all_book_ids(self):
        return sorted(self._records)

    def get_data_as_dict(self, ids):
        return [self._records[i] for i in ids if i in self._records]

    def cover(self, book_id):
        return self._covers.get(book_id)

    def search_ids(self, query):
        self.searched.append(query)
        return []

    def format_abspath(self, book_id, fmt):
        return self._paths.get((book_id, str(fmt).upper()))


class FakeApi(object):
    def __init__(self, records, covers=None, paths=None):
        self.calibre = FakeCalibre(records, covers, paths)


@pytest.fixture
def adapter_factory(backend_pkg, tmp_path):
    adapter = submodule('adapter')

    def make(records, covers=None):
        api = FakeApi(records, covers)
        db = adapter.DbAdapter(api, str(tmp_path / 'covers'))
        db.load(sorted(records))
        return db

    return make


# --------------------------------------------------------------------------- 键名约定

def test_title_sort_reads_the_host_key(adapter_factory):
    """宿主把标题排序列叫 ``sort``（calibre 的列名），不是 ``title_sort``。"""
    db = adapter_factory({1: {'id': 1, 'title': '盗墓笔记', 'sort': 'daomubiji'}})
    assert db.title_sort(1) == 'daomubiji'


def test_title_sort_keeps_old_key_as_fallback(adapter_factory):
    db = adapter_factory({1: {'id': 1, 'title': 'X', 'title_sort': 'x'}})
    assert db.title_sort(1) == 'x'


def test_title_sort_missing_is_empty(adapter_factory):
    db = adapter_factory({1: {'id': 1, 'title': 'X'}})
    assert db.title_sort(1) == ''


def test_shim_title_sort_delegates_to_host(backend_pkg, monkeypatch):
    """宿主在时用宿主自己的算法：MyBooks 存的是拼音，用 calibre 的冠词规则对不上。"""
    metadata = submodule('qc.shim.ebooks.metadata')
    fake_utils = types.ModuleType('webserver.utils')
    fake_utils.get_title_sort = lambda title: 'host:' + str(title).lower()
    fake_pkg = types.ModuleType('webserver')
    fake_pkg.utils = fake_utils
    monkeypatch.setitem(sys.modules, 'webserver', fake_pkg)
    monkeypatch.setitem(sys.modules, 'webserver.utils', fake_utils)
    assert metadata.title_sort('盗墓笔记') == 'host:盗墓笔记'


def test_shim_title_sort_fallback_without_host(backend_pkg, monkeypatch):
    """离线（没有宿主）时退回近似实现：英文冠词后置，其余原样。"""
    metadata = submodule('qc.shim.ebooks.metadata')
    monkeypatch.setattr(metadata, '_host_title_sort', lambda: None)
    assert metadata.title_sort('The Hobbit', lang='en') == 'Hobbit, The'
    assert metadata.title_sort('盗墓笔记') == '盗墓笔记'


# --------------------------------------------------------------------------- 哨兵语义

def test_missing_date_fields_never_compare_equal(adapter_factory):
    """字段缺失时必须返回互不相等的哨兵，否则 pubdate == timestamp 会把整库标成问题。"""
    db = adapter_factory({1: {'id': 1, 'title': 'X'}})
    assert db.pubdate(1) != db.pubdate(1)
    assert db.timestamp(1) != db.timestamp(1)
    assert db.pubdate(1) != datetime.datetime(2020, 1, 1)


def test_present_date_fields_are_datetimes(adapter_factory):
    db = adapter_factory({1: {'id': 1, 'title': 'X', 'pubdate': '2020-01-02T03:04:05'}})
    assert db.pubdate(1) == datetime.datetime(2020, 1, 2, 3, 4, 5)


# --------------------------------------------------------------------------- 缺失项兜底

def test_missing_field_not_exposed_is_skipped_not_flagged(adapter_factory, backend_pkg):
    """宿主不暴露该字段时报"跳过"，而不是把整库都报成命中。"""
    driver = submodule('driver')
    db = adapter_factory({1: {'id': 1, 'title': 'X'}})
    matched, note = driver._native_missing(db, 'rating:False', [1])
    assert matched == []
    assert 'rating' in note


def test_unrated_rating_is_matched(adapter_factory, backend_pkg):
    """宿主的 rating 是数值列：未评分是 0，不是缺键。"""
    driver = submodule('driver')
    db = adapter_factory({1: {'id': 1, 'title': 'X', 'rating': 0},
                          2: {'id': 2, 'title': 'Y', 'rating': 8}})
    matched, note = driver._native_missing(db, 'rating:False', [1, 2])
    assert matched == [1]
    assert note == ''


def test_missing_cover_and_formats_use_accessors(adapter_factory, backend_pkg):
    driver = submodule('driver')
    db = adapter_factory({1: {'id': 1, 'title': 'X', 'available_formats': ['EPUB']},
                          2: {'id': 2, 'title': 'Y', 'available_formats': []}},
                         covers={1: b'\xff\xd8\xffcover'})
    assert driver._native_missing(db, 'cover:False', [1, 2])[0] == [2]
    assert driver._native_missing(db, 'formats:False', [1, 2])[0] == [2]


# --------------------------------------------------------------------------- 封面判定方式

@pytest.mark.parametrize('mode,flag', [
    ('no_cover', 'opt_no_cover'),
    ('file_size', 'opt_file_size'),
    ('dimensions', 'opt_dimensions'),
    ('aspect', 'opt_aspect_ratio'),
])
def test_cover_mode_selects_one_criterion(backend_pkg, mode, flag):
    dialogs = submodule('qc.dialogs')
    dialogs.CoverOptionsDialog.OPTIONS = {'mode': mode}
    dialog = dialogs.CoverOptionsDialog()
    assert dialog.result() == dialog.Accepted
    for name in ('opt_no_cover', 'opt_file_size', 'opt_dimensions', 'opt_aspect_ratio'):
        expected = (name == flag)
        assert getattr(dialog, name).isChecked() is expected, name


@pytest.mark.parametrize('mode', ['none', '', None, 'dimensions_typo'])
def test_cover_mode_not_chosen_is_rejected(backend_pkg, mode):
    """没选判定方式（或值不合法）必须 Rejected：check_covers 会照 Accepted 遍历全库。"""
    dialogs = submodule('qc.dialogs')
    dialogs.CoverOptionsDialog.OPTIONS = {'mode': mode}
    assert dialogs.CoverOptionsDialog().result() == dialogs.CoverOptionsDialog.Rejected


def test_cover_options_pass_through(backend_pkg):
    dialogs = submodule('qc.dialogs')
    dialogs.CoverOptionsDialog.OPTIONS = {
        'mode': 'aspect', 'operator': 'greater than',
        'aspect_x': 3, 'aspect_y': 4, 'aspect_tolerance_pct': 5,
    }
    dialog = dialogs.CoverOptionsDialog()
    assert dialog.check_operator == 'greater than'
    assert (dialog.aspect_x, dialog.aspect_y, dialog.aspect_tolerance_pct) == (3, 4, 5)


# --------------------------------------------------------------------------- 报告筛选

def test_filter_report_books_counts_match_rows(backend_pkg):
    driver = submodule('driver')
    books = [
        {'book_id': 1, 'title': 'A', 'issues': [
            {'check': 'check_x', 'severity': 'error'},
            {'check': 'check_y', 'severity': 'info'}]},
        {'book_id': 2, 'title': 'B', 'issues': [
            {'check': 'check_x', 'severity': 'info'}]},
    ]
    everything, total = driver.filter_report_books(books)
    assert (len(everything), total) == (2, 3)

    only_errors, total = driver.filter_report_books(books, severity='error')
    assert [b['book_id'] for b in only_errors] == [1]
    assert total == 1

    only_x, total = driver.filter_report_books(books, check_key='check_x')
    assert [b['book_id'] for b in only_x] == [1, 2]
    assert total == 2

    # 过滤不能改到原报告（/report 每次请求都从磁盘重新读，但同一份数据也可能被复用）
    assert len(books[0]['issues']) == 2


def test_filter_report_books_intersects_filters(backend_pkg):
    driver = submodule('driver')
    books = [{'book_id': 1, 'title': 'A', 'issues': [
        {'check': 'check_x', 'severity': 'error'},
        {'check': 'check_x', 'severity': 'info'}]}]
    kept, total = driver.filter_report_books(books, severity='info', check_key='check_x')
    assert (len(kept), total) == (1, 1)
    assert [i['severity'] for i in kept[0]['issues']] == ['info']


# --------------------------------------------------------------------------- 按检查项汇总

def _sample_report():
    return {
        'total_books': 3, 'books_with_issues': 2, 'issues_total': 3,
        'severity_counts': {'error': 2, 'warn': 1, 'info': 0},
        'checks': ['check_epub_corrupt_zip', 'check_missing_isbn', 'check_missing_tags'],
        'per_check': {'check_epub_corrupt_zip': [1, 2], 'check_missing_isbn': [1],
                      'check_missing_tags': []},
        'books': [
            {'book_id': 1, 'title': 'A', 'issues': [
                {'check': 'check_epub_corrupt_zip', 'severity': 'error', 'detail': ['x', 'y']},
                {'check': 'check_missing_isbn', 'severity': 'warn', 'detail': []}]},
            {'book_id': 2, 'title': 'B', 'issues': [
                {'check': 'check_epub_corrupt_zip', 'severity': 'error', 'detail': ['z']}]},
        ],
    }


def test_summarize_report_aggregates_by_check(backend_pkg):
    driver = submodule('driver')
    summary = driver.summarize_report(_sample_report())
    assert summary['checks_total'] == 3
    assert summary['checks_run'] == 3
    assert summary['checks_with_hits'] == 2
    # 命中书数多的排前面
    assert [row['check'] for row in summary['checks']] == \
        ['check_epub_corrupt_zip', 'check_missing_isbn']
    top = summary['checks'][0]
    assert (top['books'], top['detail_lines']) == (2, 3)
    assert [b['book_id'] for b in top['sample']] == [1, 2]
    # report 里没带 name 时回退到注册表
    assert top['name'] == submodule('qc.menus').PLUGIN_MENUS['check_epub_corrupt_zip']['name']
    assert top['severity'] == 'error'


def test_summarize_report_caps_sample(backend_pkg):
    driver = submodule('driver')
    summary = driver.summarize_report(_sample_report(), sample=1)
    assert all(len(row['sample']) <= 1 for row in summary['checks'])


def test_summarize_report_handles_empty_report(backend_pkg):
    driver = submodule('driver')
    summary = driver.summarize_report({'books': []})
    assert summary['checks'] == []
    assert summary['checks_with_hits'] == 0

# --------------------------------------------------------------------------- 逐书明细

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
    <dc:title>Test Book</dc:title>
    <dc:language>zh</dc:language>
    <dc:identifier id="bookid">urn:uuid:12345678-1234-1234-1234-123456789012</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
  </spine>
</package>
"""

NCX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:12345678-1234-1234-1234-123456789012"/></head>
  <docTitle><text>Test Book</text></docTitle>
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>Ch1</text></navLabel>
      <content src="c1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""

XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch1</title></head>
<body><p>hello</p></body></html>
"""


def write_minimal_epub(path, extra_files=()):
    """写一本结构完整的最小 EPUB（可选塞几个没登记进 manifest 的文件）。"""
    import zipfile
    with zipfile.ZipFile(str(path), 'w') as zf:
        zf.writestr('mimetype', 'application/epub+zip')
        zf.writestr('META-INF/container.xml', CONTAINER_XML)
        zf.writestr('OEBPS/content.opf', OPF_XML)
        zf.writestr('OEBPS/toc.ncx', NCX_XML)
        zf.writestr('OEBPS/c1.xhtml', XHTML)
        for name in extra_files:
            zf.writestr(name, 'junk')


def test_report_collects_per_book_log_lines(backend_pkg, tmp_path):
    """回归：检查项写进 BaseCheck.log 的日志必须进报告。

    曾经 driver 收集的是另一个 gui.current_log 对象，于是 28 个命中的检查项一条逐书明细都
    收不到，报告里每条问题都显示"该检查项上游不输出逐条明细"。
    """
    import threading
    driver = submodule('driver')
    epub = tmp_path / 'book.epub'
    write_minimal_epub(epub, extra_files=['OEBPS/stray.txt'])

    records = {1: {'id': 1, 'title': 'T', 'authors': ['A'],
                   'available_formats': ['EPUB'], 'sort': 't'}}
    api = FakeApi(records, paths={(1, 'EPUB'): str(epub)})
    report = driver.run_checks(
        api=api, book_ids=[1], check_keys=['check_epub_unman_files'], options={},
        progress_cb=None, cancel_event=threading.Event(),
        cover_root=str(tmp_path / 'covers'))

    assert report['books'], '这一项应当命中那本带多余文件的 EPUB'
    issue = report['books'][0]['issues'][0]
    assert issue['check'] == 'check_epub_unman_files'
    assert issue['detail'], '逐书明细为空 —— check.log 又没有接到 gui.current_log 上'
    assert any('stray' in line for line in issue['detail']), issue['detail']


def test_clean_log_line_strips_dialog_markup(backend_pkg):
    """上游日志是给富文本对话框看的，进报告前要把标签/实体/缩进清掉。"""
    driver = submodule('driver')
    assert driver.clean_log_line('		<b>Margins</b> are &amp; defined') == 'Margins are & defined'
    assert driver.clean_log_line('First match in book: <b>盗墓笔记</b>') == 'First match in book: 盗墓笔记'
    assert driver.clean_log_line(None) == ''
    assert driver.clean_log_line('   ') == ''


# --------------------------------------------------------------------------- 噪声项与分级

def test_noisy_checks_are_supported_and_explained(backend_pkg):
    """噪声项是"正确但对 MyBooks 库必然大范围命中"的检查：仍可用，但必须带原因。"""
    driver = submodule('driver')
    checks = dict((c['key'], c) for c in driver.describe_checks())
    noisy = sorted(k for k, c in checks.items() if c['noisy'])
    assert len(noisy) == 10
    for key in noisy:
        assert checks[key]['supported'], key
        assert checks[key]['noisy_reason'], key
    # 用户报过"所有书都报错"的那两个：calibre 痕迹类必须在噪声集合里
    assert 'check_epub_no_svg_cover' in noisy
    assert 'check_epub_not_converted' in noisy
    assert 'check_authors_case' in noisy
    assert driver.is_noisy('check_authors_case') is True
    assert driver.is_noisy('check_epub_corrupt_zip') is False


def test_error_severity_means_structural_breakage(backend_pkg):
    """error 只留给"书本身可能坏了"的结构性问题；反面检查与取向类一律 info。"""
    driver = submodule('driver')
    errors = set(c['key'] for c in driver.describe_checks()
                 if c['supported'] and driver.severity_for(c['key']) == 'error')
    assert errors == {
        'check_epub_corrupt_zip', 'check_epub_no_container', 'check_epub_files_missing',
        'check_epub_broken_images', 'check_epub_toc_broken', 'check_epub_guide_broken',
        'check_epub_drm',
        # 本仓新增的三种格式里，只有"真的读不了"才算结构性损坏
        'check_pdf_unreadable', 'check_pdf_encrypted', 'check_txt_empty',
        'check_azw3_drm', 'check_azw3_truncated',
    }
    # 这两个是 svg_cover / converted（info）的反面，曾被落到 EPUB 类默认的 error
    assert driver.severity_for('check_epub_no_svg_cover') == 'info'
    assert driver.severity_for('check_epub_not_converted') == 'info'


def test_recommended_preset_scope(backend_pkg):
    """推荐预设 = 支持的检查项 - 噪声项；前端默认勾的就是它。"""
    driver = submodule('driver')
    checks = driver.describe_checks()
    supported = [c for c in checks if c['supported']]
    recommended = [c for c in supported if not c['noisy']]
    assert len(supported) == 91
    assert len(recommended) == 81


def test_summary_marks_noisy_rows(backend_pkg):
    driver = submodule('driver')
    report = {'books': [{'book_id': 1, 'title': 'A', 'issues': [
        {'check': 'check_epub_no_svg_cover', 'severity': 'info', 'detail': []}]}]}
    summary = driver.summarize_report(report)
    assert summary['checks'][0]['noisy'] is True


def test_registry_shape(backend_pkg):
    driver = submodule('driver')
    checks = driver.describe_checks()
    keys = [c['key'] for c in checks]
    assert len(keys) == len(set(keys))
    # 93 = 上游 77 + 本仓新增的 PDF/TXT/AZW3 16 项
    assert len(checks) == 93
    assert sorted(c['key'] for c in checks if not c['supported']) == \
        ['check_title_case', 'search_epub']
    for check in checks:
        assert check['severity'] in ('error', 'warn', 'info')
        if not check['supported']:
            assert check['unsupported_reason']


def test_epub_format_scoping(adapter_factory, backend_pkg):
    """EPUB 类检查只跑有 EPUB 格式的书。"""
    driver = submodule('driver')
    menus = submodule('qc.menus')
    db = adapter_factory({1: {'id': 1, 'title': 'X', 'available_formats': ['EPUB']},
                          2: {'id': 2, 'title': 'Y', 'available_formats': ['MOBI']}})
    menu = menus.PLUGIN_MENUS['check_epub_corrupt_zip']
    assert driver._scope_for(db, menu, [1, 2]) == [1]
    assert driver._scope_for(db, menus.PLUGIN_MENUS['check_missing_isbn'], [1, 2]) == [1, 2]


# --------------------------------------------------------------------------- 跨进程找回上次报告

def _finished_report():
    report = _sample_report()
    report.update({
        'task_id': 7,
        'generated_at': '2026-09-18 20:02:20',
        'scope_label': '全部图书',
        'errors': [{'check': 'check_epub_corrupt_zip', 'error': 'boom'}],
        'cancelled': True,
    })
    return report


def test_latest_marker_round_trip(backend_pkg, tmp_path):
    driver = submodule('driver')
    path = os.path.join(str(tmp_path), driver.LATEST_MARKER)
    assert driver.write_latest_marker(path, _finished_report()) is True
    assert driver.read_latest_marker(path) == {
        'task_id': 7, 'generated_at': '2026-09-18 20:02:20'}


def test_read_latest_marker_tolerates_missing_and_torn(backend_pkg, tmp_path):
    """读不出来的标记一律当作"没有标记"：退回空态，绝不指向一份来路不明的报告。"""
    driver = submodule('driver')
    assert driver.read_latest_marker(os.path.join(str(tmp_path), 'nope.json')) is None
    torn = os.path.join(str(tmp_path), 'torn.json')
    with open(torn, 'w', encoding='utf-8') as f:
        f.write('{"task_id": 7, ')
    assert driver.read_latest_marker(torn) is None
    wrong_kind = os.path.join(str(tmp_path), 'list.json')
    with open(wrong_kind, 'w', encoding='utf-8') as f:
        f.write('[1, 2]')
    assert driver.read_latest_marker(wrong_kind) is None


def test_marker_needs_the_same_run(backend_pkg):
    """task_id 在新进程里会从 1 重来，所以只有 task_id 与 generated_at 都对上才算那份报告。"""
    driver = submodule('driver')
    report = _finished_report()
    marker = driver.latest_marker(report)
    assert driver.marker_matches(marker, report) is True
    assert driver.marker_matches(marker, dict(report, generated_at='2026-09-17 08:00:00')) is False
    assert driver.marker_matches(marker, dict(report, task_id=8)) is False
    assert driver.marker_matches(None, report) is False
    assert driver.marker_matches(marker, None) is False


def test_restored_progress_shape(backend_pkg):
    """/progress 在任务对象消失后要照旧回答"已完成"，计数与正常收尾时一致。"""
    driver = submodule('driver')
    data = driver.restored_progress(_finished_report())
    assert data['status'] == 'completed'
    assert data['progress'] == 100
    assert data['stage'] == 'done'
    assert data['restored'] is True
    assert data['scope_label'] == '全部图书'
    assert (data['done'], data['total']) == (3, 3)
    assert data['check_index'] == data['check_total'] == 3
    assert data['books_with_issues'] == 2
    assert data['issues_total'] == 3
    assert data['severity_counts'] == {'error': 2, 'warn': 1, 'info': 0}
    assert data['errors_count'] == 1
    assert data['cancelled'] is True


# tool.py 依赖 webserver.*，离线导不进来（这也是它此前零覆盖的原因），所以下面这条链路
# 只能按源码守：报告落盘后写下标记、/progress 在内存任务消失时回退读它、/report 的缺省
# task_id 同样回退。缺任何一处，用户重建工具页时看到的就是一片空白。

TOOL_SRC = os.path.join(BACKEND, 'tool.py')


def _called_names(node):
    """`node` 子树里所有被调用的函数名（`a.b(c)` 记作 `b`）。"""
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def _tool_def(name, kind):
    with open(TOOL_SRC, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, kind) and node.name == name:
            return node
    raise AssertionError('%s 里找不到 %s' % (TOOL_SRC, name))


def test_run_writes_the_latest_pointer():
    assert 'write_latest_marker' in _called_names(_tool_def('run', ast.FunctionDef))


def test_progress_falls_back_to_the_report_on_disk():
    names = _called_names(_tool_def('ProgressHandler', ast.ClassDef))
    assert 'restored_progress' in names
    # 任务已受理、后台线程还没开跑的窗口里 is_running() 为真，这时的旧报告不能冒充结果
    assert 'is_running' in names


def test_report_task_id_falls_back_after_a_restart():
    names = _called_names(_tool_def('_report_task_id', ast.FunctionDef))
    assert 'last_completed_task_id' in names


# --------------------------------------------------------------------------- PDF / TXT / AZW3

def test_format_scoping_covers_the_new_formats(adapter_factory, backend_pkg):
    """PDF / TXT 各自的检查只跑有那种格式的书（cat→格式的门控必须登记）。"""
    driver = submodule('driver')
    menus = submodule('qc.menus')
    db = adapter_factory({
        1: {'id': 1, 'title': 'P', 'available_formats': ['PDF']},
        2: {'id': 2, 'title': 'T', 'available_formats': ['TXT']},
        3: {'id': 3, 'title': 'E', 'available_formats': ['EPUB']},
    })
    assert driver._scope_for(db, menus.PLUGIN_MENUS['check_pdf_encrypted'], [1, 2, 3]) == [1]
    assert driver._scope_for(db, menus.PLUGIN_MENUS['check_txt_not_utf8'], [1, 2, 3]) == [2]
    # 元数据/缺失项检查不受格式限制
    assert driver._scope_for(db, menus.PLUGIN_MENUS['check_missing_isbn'], [1, 2, 3]) == [1, 2, 3]


def test_azw3_checks_have_their_own_class(backend_pkg):
    """AZW3 那四项与 MOBI 共用分组（cat=mobi），但必须由 Azw3Check 接手。

    按 cat 取类会落到 MobiCheck，而它对陌生 key 只会弹一个 shim dialog：不标记、不报错，
    报告里就变成"这一项很干净"。这条断言守的就是那根线。
    """
    driver = submodule('driver')
    menus = submodule('qc.menus')
    for key in ('check_azw3_drm', 'check_azw3_truncated',
                'check_azw3_missing_thumb', 'check_azw3_mobi6_only'):
        assert menus.PLUGIN_MENUS[key]['cat'] == 'mobi'
        resolved = driver.CHECK_CLASSES_BY_KEY.get(key) or \
            driver.CHECK_CLASSES.get(menus.PLUGIN_MENUS[key]['cat'])
        assert resolved.__name__ == 'Azw3Check', key
    # 上游那四项仍然走 MobiCheck
    assert driver.CHECK_CLASSES_BY_KEY.get('check_mobi_missing_asin') is None
    assert driver.CHECK_CLASSES['mobi'].__name__ == 'MobiCheck'


def test_missing_dependency_semantics(backend_pkg, monkeypatch):
    """缺依赖要能如实说出来（缺了整组记 skipped），有依赖时返回空串。"""
    driver = submodule('driver')
    assert driver.missing_dependency('epub') == ''
    assert isinstance(driver.missing_dependency('pdf'), str)
    monkeypatch.setitem(driver.REQUIRES_MODULE, 'pretendcat', ('no_such_module_xyz',))
    monkeypatch.setitem(driver.REQUIRES_MODULE, 'satisfied', ('json',))
    assert driver.missing_dependency('pretendcat') == 'needs no_such_module_xyz in the host Python'
    assert driver.missing_dependency('satisfied') == ''


def test_txt_digest_flags_common_problems(backend_pkg):
    """TXT 的提取层是纯函数：给字节就得事实，六个判定各测正反两面。"""
    txt = submodule('qc.check_txt')
    ok = txt.txt_digest('第一章\n正文\n第二章\n'.encode('utf-8'))
    assert ok['size'] > 0 and ok['decoded_ok'] and ok['bom'] == ''
    assert not (txt.hit_empty(ok) or txt.hit_not_utf8(ok) or txt.hit_bom(ok)
                or txt.hit_control_chars(ok) or txt.hit_mixed_newlines(ok)
                or txt.hit_long_lines(ok))

    assert txt.hit_empty(txt.txt_digest(b''))
    assert txt.hit_empty(txt.txt_digest('  \r\n\t\n'.encode('utf-8')))
    assert not txt.hit_empty(ok)

    gbk = '第一章\n正文\n'.encode('gb18030')
    bad = txt.txt_digest(gbk)
    assert txt.hit_not_utf8(bad)
    assert bad['encoding_hint']          # 能给出一个猜测（chardet 或 gb18030 回退）

    with_bom = txt.txt_digest(b'\xef\xbb\xbf' + '第一章\n'.encode('utf-8'))
    assert txt.hit_bom(with_bom)
    assert not txt.hit_not_utf8(with_bom)   # 去掉 BOM 后是合法 UTF-8，不该同时报编码
    assert with_bom['bom'] == 'UTF-8'

    nul = txt.txt_digest(b'ab\x00cd\n')
    assert txt.hit_control_chars(nul)
    assert nul['control_count'] == 1
    assert nul['control_first'] == (2, 0)

    mixed = txt.txt_digest(b'a\r\nb\nc\rd')
    assert txt.hit_mixed_newlines(mixed)
    assert mixed['newlines'] == {'crlf': 1, 'lf': 1, 'cr': 1}
    assert not txt.hit_mixed_newlines(txt.txt_digest(b'a\r\nb\r\n'))
    assert not txt.hit_mixed_newlines(txt.txt_digest(b'a\nb\n'))
    # 整本统一的 CR（老 Mac 文本）不算"混用"，虽然也确实一个 LF 都没有
    assert not txt.hit_mixed_newlines(txt.txt_digest(b'a\rb\r'))

    one_line = txt.txt_digest(('x' * (txt.MAX_LINE_CHARS + 10)).encode('utf-8'))
    assert txt.hit_long_lines(one_line)
    assert one_line['line_count'] == 1
    long_line = txt.txt_digest(('y' * (txt.MAX_LINE_CHARS + 1) + '\n短\n').encode('utf-8'))
    assert txt.hit_long_lines(long_line)
    assert not txt.hit_long_lines(ok)


def test_pdf_predicates(backend_pkg):
    """PDF 的判定层只吃 digest，所以不装 PyMuPDF 也能测。"""
    pdf = submodule('qc.check_pdf')
    base = {'size': 1000000, 'pages': 100, 'encrypted': False, 'needs_pass': False,
            'repaired': False, 'toc_entries': 5, 'text_chars': 2000, 'text_sampled': 5,
            'page_sizes': [(595.0, 842.0)] * 5, 'size_sampled': 5, 'partial': False}
    assert not pdf.hit_unreadable(base)
    assert not pdf.hit_encrypted(base)
    assert not pdf.hit_no_text_layer(base)
    assert not pdf.hit_oversized(base)
    assert not pdf.hit_no_toc(base)
    assert not pdf.hit_mixed_page_size(base)

    assert pdf.hit_unreadable(dict(base, pages=0))
    # 结构坏了、只能靠 PyMuPDF 修复打开，也算"读不了"（素材库的 pdf-damaged 就是这个）
    assert pdf.hit_unreadable(dict(base, repaired=True))
    assert not pdf.hit_unreadable(dict(base, repaired=True, encrypted=True))
    # 加密打不开的不能同时被报成"打不开"
    assert not pdf.hit_unreadable(dict(base, pages=0, encrypted=True, needs_pass=True))
    assert pdf.hit_encrypted(dict(base, encrypted=True))

    assert pdf.hit_no_text_layer(dict(base, text_chars=0))
    assert not pdf.hit_no_text_layer(dict(base, pages=0, text_chars=0))
    # 修复态下页级结论不可信：只留"读不了"那一条，别再顺带说没有文字层/目录
    damaged = dict(base, repaired=True, text_chars=0, toc_entries=0)
    assert pdf.hit_unreadable(damaged)
    assert not pdf.hit_no_text_layer(damaged)
    assert not pdf.hit_no_toc(damaged)
    assert not pdf.hit_mixed_page_size(dict(damaged, page_sizes=[(595.0, 842.0), (420.0, 595.0)]))
    # 加密打不开时"文字层/目录"是**未知**（哨兵 None）而不是空：不能反过来说它没有。
    # 素材库里的 pdf-encrypted 就是这个场景抓出来的误报。
    locked = dict(base, encrypted=True, needs_pass=True, toc_entries=None,
                  text_chars=None, text_sampled=0, page_sizes=[])
    assert not pdf.hit_no_text_layer(locked)
    assert not pdf.hit_no_toc(locked)
    # 一页都没抽样到（text_sampled == 0）时同样不能下"没有文字层"的结论
    assert not pdf.hit_no_text_layer(dict(base, text_chars=0, text_sampled=0))
    assert pdf.hit_oversized(dict(base, size=pdf.BYTES_PER_PAGE_WARN * 100 + 1))
    assert pdf.hit_no_toc(dict(base, toc_entries=0))
    assert pdf.hit_mixed_page_size(dict(base, page_sizes=[(595.0, 842.0), (420.0, 595.0)]))
    # A4 与 Letter 的高差 5.9% 也算混排（真书里那种"两种页面格式"的混）
    assert pdf.hit_mixed_page_size(dict(base, page_sizes=[(595.0, 842.0), (612.0, 792.0)]))
    # 扫描件的逐页抖动（几个百分点）不算：容差 5%，拿真书那本扫描绘本定的
    assert not pdf.hit_mixed_page_size(dict(base, page_sizes=[
        (960.0, 947.2), (954.0, 948.0), (996.0, 945.0)]))
    assert not pdf.hit_mixed_page_size(dict(base, page_sizes=[(595.0, 842.0), (595.5, 842.0)]))


def test_pdf_digest_on_real_pdfs(backend_pkg, tmp_path):
    """真开一次 PDF（本机没装 PyMuPDF 就跳过）：正常件、纯图形件、尺寸混用、加密件。"""
    pdf = submodule('qc.check_pdf')
    fitz = pdf._load_fitz()
    if fitz is None:
        pytest.skip('本机没有 PyMuPDF，提取层由素材库 smoke 覆盖')

    normal = os.path.join(str(tmp_path), 'normal.pdf')
    doc = fitz.open()
    for index in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), 'chapter %d' % index)
    doc.set_toc([[1, 'Chapter 1', 1], [1, 'Chapter 2', 2]])
    doc.save(normal)
    doc.close()

    digest, problem = pdf.pdf_digest(normal)
    assert problem is None and digest['pages'] == 2
    assert digest['text_chars'] > 0 and not pdf.hit_no_text_layer(digest)
    assert digest['toc_entries'] == 2 and not pdf.hit_no_toc(digest)
    assert not pdf.hit_mixed_page_size(digest) and not pdf.hit_encrypted(digest)
    assert not pdf.hit_unreadable(digest)

    drawn = os.path.join(str(tmp_path), 'drawn.pdf')
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(50, 50, 400, 600))
    doc.save(drawn)
    doc.close()
    digest, problem = pdf.pdf_digest(drawn)
    assert problem is None and pdf.hit_no_text_layer(digest)   # 只有图形 → 像扫描件

    mixed = os.path.join(str(tmp_path), 'mixed.pdf')
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=420, height=595)
    doc.save(mixed)
    doc.close()
    digest, _ = pdf.pdf_digest(mixed)
    assert pdf.hit_mixed_page_size(digest)

    locked = os.path.join(str(tmp_path), 'locked.pdf')
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(locked, encryption=fitz.PDF_ENCRYPT_AES_256, user_pw='secret', owner_pw='secret')
    doc.close()
    digest, problem = pdf.pdf_digest(locked)
    assert problem is None and digest['encrypted'] and digest['needs_pass']
    assert pdf.hit_encrypted(digest)
    assert not pdf.hit_unreadable(digest)

    broken = os.path.join(str(tmp_path), 'broken.pdf')
    with open(broken, 'wb') as stream:
        stream.write(b'%PDF-1.4\nthis is not a real pdf at all\n')
    digest, problem = pdf.pdf_digest(broken)
    assert problem is not None or pdf.hit_unreadable(digest)


# ---- 现造一颗最小 MOBI 容器：真书都"健康"，DRM 与截断只能这样造 ----

def _exth_record(rec_id, payload):
    import struct
    return struct.pack('>LL', rec_id, len(payload) + 8) + payload


def _minimal_mobi(version=8, encryption=0, exth_ids=(201, 202)):
    """拼一颗结构合法的最小 MOBI：PalmDB 头 + 记录表 + 记录 0（PalmDOC + MOBI + EXTH）。"""
    import struct
    records = [_exth_record(rec_id, b'\x00\x00\x00\x01') for rec_id in exth_ids]
    if version >= 8:
        records.append(_exth_record(121, b'\x00\x00\x00\x4e'))   # KF8 边界
    exth = b'EXTH' + struct.pack('>LL', 12 + sum(len(r) for r in records), len(records)) \
        + b''.join(records)

    palmdoc = struct.pack('>HHLHHHH', 2, 0, 1000, 8, 4096, encryption, 0)
    mobi = bytearray(232)
    mobi[0:4] = b'MOBI'
    struct.pack_into('>L', mobi, 4, 232)          # header length
    struct.pack_into('>L', mobi, 8, 2)            # type = book
    struct.pack_into('>L', mobi, 12, 65001)       # codepage
    struct.pack_into('>L', mobi, 16, 7)           # unique id
    struct.pack_into('>L', mobi, 20, version)     # file version
    struct.pack_into('>L', mobi, 0x70, 0x50)      # EXTH flags（0x40 = 有 EXTH）
    section0 = palmdoc + bytes(mobi) + exth
    section1 = b'<html><body>text</body></html>'

    count = 2
    start = 78 + count * 8 + 2
    header = bytearray(start)
    header[0:32] = b'Fixture Book'.ljust(32, b'\x00')
    header[0x3C:0x40] = b'BOOK'
    header[0x40:0x44] = b'MOBI'
    struct.pack_into('>H', header, 76, count)
    struct.pack_into('>L', header, 78, start)
    struct.pack_into('>L', header, 86, start + len(section0))
    return bytes(header) + section0 + section1


class _SilentLog(object):
    def __call__(self, *args):
        pass
    error = exception = __call__


def _read_facts(azw3, mobi6, path):
    with mobi6.MinimalMobiReader(path, _SilentLog()) as reader:
        return azw3.azw3_facts(reader)


def test_minimal_mobi_container_parses(backend_pkg, tmp_path):
    """mobi6 的扩展（version/KF8/加密/EXTH 记录表）在现造容器上按预期读出来。"""
    azw3 = submodule('qc.check_azw3')
    mobi6 = submodule('qc.mobi6')

    def write(name, payload):
        path = os.path.join(str(tmp_path), name)
        with open(path, 'wb') as stream:
            stream.write(payload)
        return path

    kf8 = write('kf8.azw3', _minimal_mobi(version=8))
    facts = _read_facts(azw3, mobi6, kf8)
    assert facts['version'] == 8 and facts['has_kf8']
    assert facts['encryption'] == 0 and not azw3.hit_drm(facts)
    assert not azw3.hit_mobi6_only(facts)
    assert not azw3.hit_missing_thumb(facts)     # 有 EXTH 201/202
    assert not azw3.hit_truncated(facts)
    assert facts['exth_count'] == 3              # 121 / 201 / 202 都留在完整记录表里

    legacy = write('legacy.azw3', _minimal_mobi(version=6, exth_ids=(202,)))
    facts = _read_facts(azw3, mobi6, legacy)
    assert facts['version'] == 6 and not facts['has_kf8']
    assert azw3.hit_mobi6_only(facts)
    assert not azw3.hit_missing_thumb(facts)     # 只有 202 也算有封面记录

    bare = write('bare.azw3', _minimal_mobi(version=8, exth_ids=()))
    facts = _read_facts(azw3, mobi6, bare)
    assert azw3.hit_missing_thumb(facts)

    drm = write('drm.azw3', _minimal_mobi(version=8, encryption=2))
    facts = _read_facts(azw3, mobi6, drm)
    assert facts['encryption'] == 2 and azw3.hit_drm(facts)

    # 记录表还在、数据被截断：越界记录要能被抓出来
    truncated = write('cut.azw3', _minimal_mobi(version=8)[:100])
    facts = _read_facts(azw3, mobi6, truncated)
    assert azw3.hit_truncated(facts)
