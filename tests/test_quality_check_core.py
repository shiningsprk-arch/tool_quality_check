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

    def __init__(self, records, covers=None):
        self._records = records
        self._covers = covers or {}
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
        return None


class FakeApi(object):
    def __init__(self, records, covers=None):
        self.calibre = FakeCalibre(records, covers)


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
