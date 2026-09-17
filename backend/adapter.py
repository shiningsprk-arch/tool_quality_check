# -*- coding: utf-8 -*-
"""Bridge MyBooks' CoreAPI onto the calibre ``db`` / ``gui`` surface Quality Check uses.

The ported checks call a fairly small, fixed set of calibre APIs (they were enumerated
by grepping the plugin before porting). This module implements exactly those, so the
check modules themselves stay byte-for-byte upstream apart from their imports.

Two behaviours are worth calling out because they are *not* calibre-faithful:

* ``library_path`` / ``path()`` point into the tool's work dir, where covers are
  materialised on demand from ``CoreAPI.calibre.cover()``. That is what lets the stock
  cover check (which opens ``<library>/<book path>/cover.jpg``) run unchanged.
* Fields whose value cannot be established are returned as a unique sentinel rather
  than ``None``, so equality-based checks (``pubdate == timestamp``) do not report the
  entire library when the host simply does not expose a field.
"""

import datetime
import os
import threading

from .qc import menus


class _Unknown(object):
    """A value that equals nothing, not even another instance."""

    __slots__ = ()


def _sentinel():
    return _Unknown()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(',') if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value)]


def _as_joined(value):
    return ','.join(_as_list(value))


def _as_datetime(value):
    if isinstance(value, datetime.datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace('Z', '+00:00')
        for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PrefsStore(object):
    """Stand-in for calibre's ``db.prefs`` namespaced store (per-check exclusions)."""

    def __init__(self):
        self._data = {}

    def get_namespaced(self, namespace, key, default=None):
        if (namespace, key) in self._data:
            return self._data[(namespace, key)]
        return {} if default is None else default

    def set_namespaced(self, namespace, key, value):
        self._data[(namespace, key)] = value


class DataApi(object):
    def __init__(self, db):
        self._db = db

    def has_id(self, book_id):
        return book_id in self._db.books


class NewApiAdapter(object):
    """The handful of ``db.new_api`` calls the metadata checks make."""

    def __init__(self, db):
        self._db = db

    def all_book_ids(self):
        return self._db.api.calibre.all_book_ids()

    def field_for(self, field, book_id, default_value=None):
        return self._db.field(book_id, field, default_value)

    def all_field_for(self, field, ids, default_value=None):
        return dict((book_id, self._db.field(book_id, field, default_value)) for book_id in ids)

    def author_sort_strings_for_books(self, book_ids):
        # calibre's author table carries a sort string per author; MyBooks has no public
        # accessor for it, so the author name itself is used (which is calibre's default
        # when no custom sort was set, i.e. the common case).
        return dict((book_id, list(self._db.author_list(book_id))) for book_id in book_ids)


class DbAdapter(object):
    """Exposes the calibre ``db`` object interface over CoreAPI."""

    def __init__(self, api, cover_root, cancel_event=None):
        self.api = api
        self.books = {}
        self.library_path = cover_root
        self.library_id = 'mybooks'
        self.prefs = PrefsStore()
        self.data = DataApi(self)
        self.new_api = NewApiAdapter(self)
        self.cancel_event = cancel_event or threading.Event()
        self.marks = {}
        self._authors = {}
        self._cover_bytes = {}
        self._scope_ids = []

    # -- loading ---------------------------------------------------------------

    def load(self, book_ids):
        self._scope_ids = list(book_ids)
        for record in self.api.calibre.get_data_as_dict(list(book_ids)):
            if not isinstance(record, dict):
                continue
            book_id = record.get('id')
            if book_id is None:
                continue
            self.books[book_id] = record
            self._authors[book_id] = _as_list(record.get('authors'))

    # -- field access ----------------------------------------------------------

    def field(self, book_id, name, default=None):
        record = self.books.get(book_id)
        if not record:
            return default
        value = record.get(name)
        return default if value is None else value

    def author_list(self, book_id):
        if book_id in self._authors:
            return self._authors[book_id]
        return _as_list(self.field(book_id, 'authors'))

    # -- metadata accessors (calibre legacy db API shapes) ----------------------

    def title(self, book_id, index_is_id=False):
        return self.field(book_id, 'title', '') or ''

    def authors(self, book_id, index_is_id=False):
        # calibre hands back a comma-joined string here, and the author checks split it.
        return _as_joined(self.author_list(book_id))

    def series(self, book_id, index_is_id=False):
        return self.field(book_id, 'series', '') or ''

    def series_index(self, book_id, index_is_id=False):
        return _as_float(self.field(book_id, 'series_index'), 1.0)

    def tags(self, book_id, index_is_id=False):
        return _as_joined(self.field(book_id, 'tags'))

    def languages(self, book_id, index_is_id=False):
        return _as_joined(self.field(book_id, 'languages'))

    def comments(self, book_id, index_is_id=False):
        return self.field(book_id, 'comments', '') or ''

    def isbn(self, book_id, index_is_id=False):
        direct = self.field(book_id, 'isbn')
        if direct:
            return direct
        identifiers = self.get_identifiers(book_id)
        for key, value in (identifiers or {}).items():
            if key.lower() in ('isbn', 'isbn13'):
                return value
        return ''

    def pubdate(self, book_id, index_is_id=False):
        raw = self.field(book_id, 'pubdate')
        if raw is None:
            return _sentinel()
        parsed = _as_datetime(raw)
        return parsed if parsed is not None else _sentinel()

    def timestamp(self, book_id, index_is_id=False):
        raw = self.field(book_id, 'timestamp')
        if raw is None:
            return _sentinel()
        parsed = _as_datetime(raw)
        return parsed if parsed is not None else _sentinel()

    def title_sort(self, book_id, index_is_id=False):
        # calibre's get_data_as_dict exposes the title sort column under its own name
        # ("sort"); MyBooks reads it the same way (handlers/base.py sorts a get_books()
        # result on "sort"). Keep "title_sort" as a fallback for hosts that rename it.
        value = self.field(book_id, 'sort')
        if not value:
            value = self.field(book_id, 'title_sort')
        return value or ''

    def get_identifiers(self, book_id, index_is_id=False):
        value = self.field(book_id, 'identifiers')
        return value if isinstance(value, dict) else {}

    def uuid(self, book_id, index_is_id=False):
        return self.field(book_id, 'uuid', '') or ''

    def formats(self, book_id, verify_formats=False, index_is_id=False):
        return list(self.available_formats(book_id))

    def available_formats(self, book_id):
        value = self.field(book_id, 'available_formats')
        if isinstance(value, str):
            return [f.strip().upper() for f in value.split(',') if f.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(f).upper() for f in value]
        return []

    def has_format(self, book_id, fmt, index_is_id=True):
        return fmt.upper() in self.available_formats(book_id)

    def format_abspath(self, book_id, fmt, index_is_id=True):
        try:
            return self.api.calibre.format_abspath(book_id, fmt.upper())
        except Exception:
            return None

    # -- covers ----------------------------------------------------------------

    def has_cover(self, book_id, index_is_id=False):
        return self.cover_bytes(book_id) is not None

    def cover_bytes(self, book_id):
        if book_id not in self._cover_bytes:
            try:
                self._cover_bytes[book_id] = self.api.calibre.cover(book_id)
            except Exception:
                self._cover_bytes[book_id] = None
        return self._cover_bytes[book_id]

    def path(self, book_id, index_is_id=False):
        """Materialise the cover and return the per-book subdirectory of library_path.

        The stock cover check builds ``os.path.join(library_path, path(id), 'cover.jpg')``,
        so both halves of that join point into our work dir.
        """
        data = self.cover_bytes(book_id)
        subdir = str(book_id)
        target_dir = os.path.join(self.library_path, subdir)
        if data:
            os.makedirs(target_dir, exist_ok=True)
            target = os.path.join(target_dir, 'cover.jpg')
            if not os.path.exists(target):
                with open(target, 'wb') as f:
                    f.write(data)
        return subdir

    # -- library-wide ----------------------------------------------------------

    def all_book_ids(self):
        try:
            return list(self.api.calibre.all_book_ids())
        except Exception:
            return sorted(self.books.keys())

    def search(self, query, return_matches=True):
        """calibre search string -> book ids (used for whole-library scope)."""
        try:
            return list(self.api.calibre.search_ids(query))
        except Exception:
            return sorted(self.books.keys())

    def set_marked_ids(self, marked_ids):
        # Quality Check used calibre's "marked:" virtual column to reveal the results;
        # the port collects them into the report instead.
        self.marks.update(marked_ids or {})


class LibraryView(object):
    def __init__(self, gui):
        self._gui = gui
        self.current = None

    def get_selected_ids(self):
        return list(self._gui.selected_ids)

    def sort_by_named_field(self, *args, **kwargs):
        return None

    def model(self):
        return self


class SearchBox(object):
    def __init__(self, gui):
        self._gui = gui

    def set_search_string(self, text, *args, **kwargs):
        # Set by the "missing data" checks and by the no-cover shortcut: they were pure
        # GUI filters in Quality Check, so the driver evaluates them itself.
        self._gui.pending_search = text

    def clear(self):
        self._gui.pending_search = None


class StatusBar(object):
    def __init__(self):
        self.last_message = ''

    def showMessage(self, message, *args, **kwargs):
        self.last_message = message


class FakeGui(object):
    """The ``gui`` object the ported checks are handed."""

    def __init__(self, db, selected_ids=None):
        self.current_db = db
        self.selected_ids = list(selected_ids or [])
        self.library_view = LibraryView(self)
        self.search = SearchBox(self)
        self.status_bar = StatusBar()
        # driver-owned state
        self.cancel_event = threading.Event()
        self.current_log = None
        self.per_book_log = {}
        self.log_tail_start = 0
        self.progress_cb = None
        self.pending_search = None


def reset_library_config(db):
    """Drop any per-library state carried over from a previous run of the same task."""
    db.prefs.set_namespaced(menus.PREFS_NAMESPACE, menus.PREFS_KEY_SETTINGS,
                            dict(menus.DEFAULT_LIBRARY_VALUES))
