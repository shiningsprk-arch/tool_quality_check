# -*- coding: utf-8 -*-
"""Offline smoke run: execute every supported check against a real calibre library.

Deliberately avoids MyBooks and calibre: the library is read straight out of a calibre
`metadata.db` with sqlite3, and CoreAPI is faked. That exercises the adapter, the calibre
shim and all 75 check implementations against real EPUBs -- the pre-flight before the
package is built.

Usage:
    python scripts/smoke_offline.py [path/to/library] [--checks k1,k2] [--verbose]

`path/to/library` defaults to the MyBooks clone's test library.
"""
import importlib.util
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, '..', 'backend')
DEFAULT_LIBRARY = os.path.join(HERE, '..', '..', 'mybooks源码', 'mybooks-v4.2.1', 'tests', 'library')


def load_package():
    """Load backend/ the way the host's toolbox_manager does."""
    backend = os.path.abspath(BACKEND)
    init = os.path.join(backend, '__init__.py')
    name = 'mybooks_tool_quality_check_backend'
    spec = importlib.util.spec_from_file_location(
        name, init, submodule_search_locations=[backend])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(name + '.driver')


class FakeCalibre(object):
    """The slice of CoreAPI.calibre the checks touch, backed by calibre's metadata.db."""

    def __init__(self, library):
        self.library = library
        self.conn = sqlite3.connect(os.path.join(library, 'metadata.db'))
        self.conn.row_factory = sqlite3.Row
        self._books = self._load_books()

    def _table(self, sql, args=()):
        try:
            return self.conn.execute(sql, args).fetchall()
        except sqlite3.Error:
            return []

    def _load_books(self):
        # NOTE: the key names below must mirror what MyBooks' CoreAPI.calibre
        # .get_data_as_dict() really returns -- this fake is the only thing standing in for
        # the host, so a made-up key hides a query-key bug instead of catching it (the
        # title sort column, for instance, is "sort" there, not "title_sort").
        books = {}
        rows = self._table('SELECT * FROM books')
        for row in rows:
            keys = set(row.keys())
            book_id = row['id']
            record = {
                'id': book_id,
                'title': row['title'],
                'sort': row['sort'],
                'author_sort': row['author_sort'],
                'series_index': row['series_index'],
                'pubdate': row['pubdate'],
                'timestamp': row['timestamp'],
                'uuid': row['uuid'],
                'path': row['path'],
                'cover': row['has_cover'] if 'has_cover' in keys else 0,
            }
            if 'isbn' in keys:
                record['isbn'] = row['isbn']
            authors = [r['name'] for r in self._table(
                'SELECT a.name FROM authors a JOIN books_authors_link l ON l.author=a.id '
                'WHERE l.book=? ORDER BY l.id', (book_id,))]
            record['authors'] = authors
            record['author'] = ' & '.join(authors)
            series = self._table(
                'SELECT s.name FROM series s JOIN books_series_link l ON l.series=s.id '
                'WHERE l.book=?', (book_id,))
            if series:
                record['series'] = series[0]['name']
            record['tags'] = [r['name'] for r in self._table(
                'SELECT t.name FROM tags t JOIN books_tags_link l ON l.tag=t.id WHERE l.book=?',
                (book_id,))]
            langs = [r['lang_code'] for r in self._table(
                'SELECT g.lang_code FROM languages g JOIN books_languages_link l '
                'ON l.lang_code=g.id WHERE l.book=?', (book_id,))]
            if langs:
                record['languages'] = langs
            comments = self._table('SELECT text FROM comments WHERE book=?', (book_id,))
            if comments:
                record['comments'] = comments[0]['text']
            record['identifiers'] = {r['type']: r['val'] for r in self._table(
                'SELECT type, val FROM identifiers WHERE book=?', (book_id,))}
            publishers = self._table(
                'SELECT p.name FROM publishers p JOIN books_publishers_link l '
                'ON l.publisher=p.id WHERE l.book=?', (book_id,))
            if publishers:
                record['publisher'] = publishers[0]['name']
            ratings = self._table(
                'SELECT r.rating FROM ratings r JOIN books_ratings_link l '
                'ON l.rating=r.id WHERE l.book=?', (book_id,))
            # calibre exposes the column, not its emptiness: 0 means "unrated", and the
            # missing-data check relies on that.
            record['rating'] = ratings[0]['rating'] if ratings else 0
            formats = [r['format'] for r in self._table(
                'SELECT format FROM data WHERE book=?', (book_id,))]
            record['available_formats'] = formats
            books[book_id] = record
        return books

    # -- CoreAPI.calibre surface ------------------------------------------------

    def all_book_ids(self):
        return sorted(self._books.keys())

    def get_data_as_dict(self, ids):
        return [self._books[i] for i in ids if i in self._books]

    def search_ids(self, query):
        text = (query or '').strip().lower()
        if text in ('formats:epub', 'formats:=epub'):
            return [i for i, b in self._books.items() if 'EPUB' in b.get('available_formats', [])]
        if text == 'formats:false':
            return [i for i, b in self._books.items() if not b.get('available_formats')]
        if text == 'cover:false':
            return [i for i, b in self._books.items() if not b.get('cover')]
        if text == 'isbn:false':
            return [i for i, b in self._books.items() if not b.get('isbn')]
        if text == 'tags:false':
            return [i for i, b in self._books.items() if not b.get('tags')]
        if text == 'comments:false':
            return [i for i, b in self._books.items() if not b.get('comments')]
        if text == 'pubdate:false':
            return [i for i, b in self._books.items() if not b.get('pubdate')]
        if text == 'rating:false':
            return [i for i, b in self._books.items() if not b.get('rating')]
        if text == 'publisher:false':
            return [i for i, b in self._books.items() if not b.get('publisher')]
        if text == 'languages:false':
            return [i for i, b in self._books.items() if not b.get('languages')]
        raise ValueError('unsupported query in the offline fake: %s' % query)

    def search_books(self, query, max_results=20):
        return self.get_data_as_dict(self.all_book_ids()[:max_results])

    def format_abspath(self, book_id, fmt):
        record = self._books.get(book_id)
        if not record:
            return None
        book_dir = os.path.join(self.library, record['path'])
        suffix = '.' + fmt.lower()
        if not os.path.isdir(book_dir):
            return None
        for name in sorted(os.listdir(book_dir)):
            if name.lower().endswith(suffix):
                return os.path.join(book_dir, name)
        return None

    def cover(self, book_id):
        record = self._books.get(book_id)
        if not record or not record.get('cover'):
            return None
        path = os.path.join(self.library, record['path'], 'cover.jpg')
        if os.path.exists(path):
            with open(path, 'rb') as f:
                return f.read()
        return None


class FakeApi(object):
    def __init__(self, calibre, work_root):
        self.calibre = calibre
        self.storage = _Storage(work_root)


class _Storage(object):
    def __init__(self, root):
        self.root = root

    def get_work_dir(self, unique_key=None):
        path = os.path.join(self.root, 'shared' if not unique_key else str(unique_key))
        os.makedirs(path, exist_ok=True)
        return path


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    verbose = '--verbose' in sys.argv
    only_errors = '--errors-only' in sys.argv
    allow_errors = '--allow-errors' in sys.argv
    selected = None
    for a in sys.argv[1:]:
        if a.startswith('--checks='):
            selected = [k for k in a.split('=', 1)[1].split(',') if k]

    library = os.path.abspath(args[0] if args else DEFAULT_LIBRARY)
    if not os.path.exists(os.path.join(library, 'metadata.db')):
        print('no metadata.db under %s' % library)
        return 2

    driver = load_package()
    calibre = FakeCalibre(library)
    work_root = os.path.abspath(os.path.join(HERE, '..', 'dist', '_smoke'))
    api = FakeApi(calibre, work_root)

    keys = selected or [c['key'] for c in driver.describe_checks() if c['supported']]
    ids = calibre.all_book_ids()
    print('library: %s' % library)
    print('books: %d, checks: %d' % (len(ids), len(keys)))

    import threading
    import collections

    seen = collections.OrderedDict()
    state = {'n': 0}

    def progress_cb(check_index, check_total, check_key, done, total, book_id, title):
        state['n'] += 1
        if state['n'] % 200 == 0 or done == total:
            print('  [%d/%d] %-28s %d/%d' % (check_index + 1, check_total, check_key, done, total))
        seen[check_key] = (check_index, check_total)

    started = time.time()
    report = driver.run_checks(
        api=api,
        book_ids=ids,
        check_keys=keys,
        # 封面判定方式必须显式给：没给就等于"用户不要封面检查"，check_covers 会直接返回，
        # 那样预检就漏掉了这一项。
        options={'qc': {'maxTags': 5},
                 'cover': {'mode': 'dimensions', 'operator': 'less than',
                           'image_width': 600, 'image_height': 800}},
        progress_cb=progress_cb,
        cancel_event=threading.Event(),
        cover_root=os.path.join(work_root, 'covers'),
    )
    elapsed = time.time() - started

    print('\n--- %.1fs, %d checks run ---' % (elapsed, len(report['per_check'])))
    print('books with issues: %d / %d, issues: %d' % (
        report['books_with_issues'], report['total_books'], report['issues_total']))
    print('severity: %s' % report['severity_counts'])

    if report.get('skipped'):
        print('\nskipped:')
        for k, why in report['skipped'].items():
            print('  %-30s %s' % (k, why))
    if report.get('notes'):
        print('\ncheck notes:')
        for k, lines in report['notes'].items():
            print('  %-30s %s' % (k, ' | '.join(lines[:2])[:160]))
    if report.get('errors'):
        print('\nunhandled errors (%d):' % len(report['errors']))
        for err in report['errors'][:20]:
            print('  %-28s book=%s %s' % (err.get('check'), err.get('book_id'), err.get('error')))

    print('\nper check (non-zero only):')
    for key, count in sorted(report['per_check'].items(), key=lambda kv: -kv[1]):
        if count:
            print('  %-32s %d' % (key, count))

    if verbose:
        print('\n--- per book ---')
        for book in report['books']:
            if only_errors and not any(i['severity'] == 'error' for i in book['issues']):
                continue
            print('%s. %s [%s]' % (book['book_id'], book['title'], ','.join(book['formats'])))
            for issue in book['issues']:
                print('    %-6s %s' % (issue['severity'], issue['check']))
                for line in issue['detail'][:3]:
                    print('           %s' % line[:150])

    # 这是打包前的预检，所以要有结论：漏跑的检查项和未处理异常都算失败。
    ran = set(report['per_check'])
    missing = [k for k in keys if k not in ran and k not in (report.get('skipped') or {})]
    failures = []
    if report.get('errors'):
        failures.append('%d unhandled error(s)' % len(report['errors']))
    if missing:
        failures.append('%d check(s) never ran: %s' % (len(missing), ', '.join(missing[:5])))
    if failures and not allow_errors:
        print('\n✗ smoke failed: %s' % '; '.join(failures))
        return 1

    print('\n✔ %d checks ran, %d books with issues, %d unhandled errors'
          % (len(ran), report['books_with_issues'], len(report.get('errors') or [])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
