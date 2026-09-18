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
    assert len(supported) == 75
    assert len(recommended) == 65


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
    assert len(checks) == 77
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
