# -*- coding: utf-8 -*-
"""Runs a selected set of Quality Check checks over a book scope and builds a report.

Quality Check's own design is "one check at a time, over the whole library, then mark
the matching books". Its books are the calibre GUI's marks and its report is a log
dialog. The port keeps the checks exactly as they are and changes only the two ends:

* scope: the driver pre-seeds each check's book list, so a check never has to search;
* results: marks and log lines are collected per book instead of being shown in the GUI.
"""

import html
import json
import os
import re
from collections import OrderedDict, defaultdict

from .qc import menus
from .qc.check_base import GUILog
from .qc.check_covers import CoverCheck
from .qc.check_epub import EpubCheck
from .qc.check_metadata import MetadataCheck
from .qc.check_missing import MissingDataCheck
from .qc.check_mobi import MobiCheck
from .qc.dialogs import CoverOptionsDialog

CHECK_CLASSES = OrderedDict((
    ('epub', EpubCheck),
    ('mobi', MobiCheck),
    ('covers', CoverCheck),
    ('metadata', MetadataCheck),
    ('missing', MissingDataCheck),
))

# What a check needs on disk before it can run at all.
FORMAT_REQUIREMENTS = {
    'epub': ('EPUB',),
    'mobi': ('MOBI', 'AZW', 'AZW3'),
}

# Quality Check gives every check one icon and one mark string; the port grades them so
# the report can be triaged. Defaults are per category, with per-check overrides below.
_DEFAULT_SEVERITY = {'epub': 'error', 'mobi': 'warn', 'covers': 'warn',
                     'metadata': 'warn', 'missing': 'warn'}

_SEVERITY_OVERRIDES = {
    # Structural breakage: the book is likely unreadable or malformed.
    'check_epub_corrupt_zip': 'error',
    'check_epub_no_container': 'error',
    'check_epub_namespaces': 'warn',
    'check_epub_non_dc_meta': 'info',
    'check_epub_files_missing': 'error',
    'check_epub_guide_broken': 'error',
    'check_epub_toc_broken': 'error',
    'check_epub_broken_images': 'error',
    'check_epub_inside_epub': 'warn',
    'check_epub_drm': 'error',
    'check_epub_drm_meta': 'warn',
    'check_epub_unman_files': 'warn',
    # Cruft and cosmetics.
    'check_epub_itunes': 'warn',
    'check_epub_bookmark': 'warn',
    'check_epub_os_artifacts': 'warn',
    'check_epub_unused_css': 'warn',
    'check_epub_unused_images': 'warn',
    'check_epub_html_size': 'warn',
    'check_epub_fonts': 'warn',
    'check_epub_font_faces': 'warn',
    'check_epub_javascript': 'warn',
    'check_epub_address': 'warn',
    'check_epub_xpgt': 'warn',
    'check_epub_inline_xpgt': 'warn',
    'check_epub_css_justify': 'info',
    'check_epub_css_margins': 'info',
    'check_epub_css_no_margins': 'info',
    'check_epub_inline_margins': 'info',
    'check_epub_smarten_punc': 'info',
    'check_epub_converted': 'info',
    'check_epub_svg_cover': 'info',
    'check_epub_no_svg_cover': 'info',
    'check_epub_converted': 'info',
    'check_epub_not_converted': 'info',
    'check_epub_repl_cover': 'info',
    'check_epub_no_repl_cover': 'info',
    'check_epub_jacket': 'info',
    'check_epub_legacy_jacket': 'warn',
    'check_epub_multi_jacket': 'warn',
    'check_epub_no_jacket': 'info',
    'check_epub_toc_hierarchy': 'info',
    'check_epub_toc_size': 'warn',
    # Metadata: the softer author/title opinions are informational.
    'check_authors_case': 'info',
    'check_authors_initials': 'info',
    'check_authors_non_ascii': 'info',
    'check_authors_commas': 'info',
    'check_authors_no_commas': 'info',
    'check_titles_series': 'info',
    'check_title_case': 'info',
    'check_html_comments': 'info',
    'check_no_html_comments': 'info',
    'check_excess_tags': 'info',
    'check_dup_isbn': 'warn',
    'check_dup_series': 'warn',
    'check_pubdate': 'warn',
    'check_series_gaps': 'info',
    'check_series_pubdate': 'info',
    # Checks excluded from the port (kept in the registry so the UI can explain why).
}

# The port does not offer these: either the check needs calibre's own conversion
# settings, or it is a Qt dialog driven feature rather than a check.
UNSUPPORTED = {
    'search_epub': 'the port has no regular-expression "Search ePubs" dialog',
    'check_title_case': 'needs calibre\'s titlecase word lists; too noisy for a report',
}

# Checks that are *correct* but match most of a MyBooks library, so a report that runs
# them by default reads as "everything is broken" and hides the real findings. They stay
# available (each one answers a real question) but are left out of the 推荐 preset and
# marked in the checklist, and the report explains what a hit means:
#
# * "has calibre NOT done X" -- a library whose books were imported directly answers
#   "no" for every book;
# * calibre's Western author conventions -- a CJK name has no comma, is non-ascii, and
#   compares equal to its own upper()/lower(), so all three match every Chinese author;
# * one half of a preference pair -- whichever side you pick, the other one matches;
# * checks that need a preference the host does not expose.
NOISY_REASONS = {
    'check_epub_no_svg_cover':
        'asks which books calibre has not inserted an SVG cover into: books imported '
        'directly into MyBooks all match',
    'check_epub_not_converted':
        'asks which books calibre has not converted: books imported directly into '
        'MyBooks all match',
    'check_epub_no_jacket':
        'asks which books have no calibre jacket: a book never processed by calibre '
        'has none',
    'check_epub_repl_cover':
        'asks which covers can be replaced by calibre: most books qualify, it is a '
        'capability query rather than a fault',
    'check_authors_no_commas':
        "calibre's author convention is \"Family, Given\"; no CJK author name has a "
        'comma, so every book matches',
    'check_authors_case':
        'flags names equal to their own upper()/lower(); a CJK name always is, so every '
        'book matches',
    'check_authors_non_ascii':
        'flags names that transliterate to ASCII; every CJK author name does',
    'check_no_html_comments':
        'the opposite of check_html_comments: one side of that pair always matches most '
        'of the library',
    'check_epub_css_margins':
        'any body/@page margin counts as "conflicts with calibre preferences", and the '
        'host has no calibre margin preference to compare against',
    'check_epub_non_dc_meta':
        'any non-dc: metadata element counts (EPUB3 refines/accessibility, maker <meta>); '
        'structural information rather than a fault',
}


def is_noisy(check_key):
    return check_key in NOISY_REASONS



def severity_for(check_key):
    if check_key in _SEVERITY_OVERRIDES:
        return _SEVERITY_OVERRIDES[check_key]
    menu = menus.PLUGIN_MENUS.get(check_key) or {}
    return _DEFAULT_SEVERITY.get(menu.get('cat'), 'warn')


def describe_checks():
    """The check registry, as the frontend's checklist."""
    out = []
    for key, menu in menus.PLUGIN_MENUS.items():
        out.append({
            'key': key,
            'name': menu.get('name', key),
            'tooltip': menu.get('tooltip', ''),
            'cat': menu.get('cat', ''),
            'sub_menu': menu.get('sub_menu', ''),
            'group': menu.get('group', 0),
            'excludable': menu.get('excludable', False),
            'severity': severity_for(key),
            'supported': key not in UNSUPPORTED,
            'unsupported_reason': UNSUPPORTED.get(key, ''),
            'noisy': key in NOISY_REASONS,
            'noisy_reason': NOISY_REASONS.get(key, ''),
        })
    return out


def _scope_for(db, menu, book_ids):
    required = FORMAT_REQUIREMENTS.get(menu.get('cat'))
    if not required:
        return list(book_ids)
    return [book_id for book_id in book_ids
            if any(db.has_format(book_id, fmt) for fmt in required)]


# --- "missing data" checks --------------------------------------------------
# Quality Check implements these by handing calibre a search string for the GUI to
# apply. The port forwards that string to the host's own Calibre-query search, and
# falls back to evaluating the predicate itself.

_MISSING_FIELDS = {
    'title:"=Unknown"': ('title', 'equals_unknown'),
    'authors:"=Unknown"': ('authors', 'equals_unknown'),
    'isbn:False': ('isbn', 'empty'),
    'pubdate:False': ('pubdate', 'empty'),
    'publisher:False': ('publisher', 'empty'),
    'tags:False': ('tags', 'empty'),
    'rating:False': ('rating', 'empty'),
    'comments:False': ('comments', 'empty'),
    'languages:False': ('languages', 'empty'),
    'cover:False': ('cover', 'empty'),
    'formats:False': ('formats', 'empty'),
}


def _native_missing(db, query, book_ids):
    """Evaluate a missing-data predicate without the host's search engine.

    Returns ``(matched_ids, note)``. When the host does not expose the underlying field
    at all we report *nothing* for that check and explain why, rather than flagging the
    whole library, which is what a naive reading of an absent field would do.
    """
    spec = _MISSING_FIELDS.get(query)
    if spec is None:
        return [], 'unsupported search: %s' % query
    field, mode = spec

    if field in ('cover', 'formats', 'isbn'):
        getter = {
            'cover': lambda i: db.has_cover(i),
            'formats': lambda i: bool(db.available_formats(i)),
            'isbn': lambda i: bool(db.isbn(i, index_is_id=True)),
        }[field]
        matched = [i for i in book_ids if not getter(i)] if mode == 'empty' else []
        return matched, ''

    present = [i for i in book_ids if field in (db.books.get(i) or {})]
    if book_ids and not present:
        return [], 'host metadata does not expose "%s"; check skipped' % field

    matched = []
    for book_id in book_ids:
        record = db.books.get(book_id) or {}
        if field not in record:
            continue
        if field == 'authors':
            value = db.authors(book_id, index_is_id=True)
        else:
            value = record.get(field)
        if mode == 'empty':
            # The host reports "unset" numerically for rating (0) as well as with empty
            # strings/collections for the text fields, so 0 counts as empty here.
            empty = value is None or value == '' or value == [] or value == {} or value == 0
        else:
            empty = value is None or str(value).strip().lower() in ('', 'unknown')
        if empty:
            matched.append(book_id)
    return matched, ''


def _apply_search_check(db, api, query, book_ids):
    """Resolve a search-string check through the host, falling back to local evaluation."""
    try:
        found = set(api.calibre.search_ids(query))
        return sorted(set(book_ids) & found), ''
    except Exception:
        return _native_missing(db, query, book_ids)


# --- the runner -------------------------------------------------------------

def run_checks(api, book_ids, check_keys, options, progress_cb, cancel_event, cover_root):
    """Run every selected check and return the report structure.

    ``progress_cb(check_index, check_total, check_key, done, total, book_id, title)``
    is called per book so the host task can show progress.
    """
    from .adapter import DbAdapter, FakeGui

    options = options or {}
    db = DbAdapter(api, cover_root, cancel_event)
    db.load(book_ids)
    gui = FakeGui(db, book_ids)
    gui.cancel_event = cancel_event

    # The ported config code reads its options out of this plugin-wide store.
    store_defaults = dict(menus.DEFAULT_STORE_VALUES)
    store_defaults.update(options.get('qc') or {})
    menus.plugin_prefs[menus.STORE_OPTIONS] = store_defaults
    CoverOptionsDialog.OPTIONS = options.get('cover') or {}

    findings = defaultdict(lambda: defaultdict(list))
    matched_by_check = OrderedDict()
    notes = OrderedDict()
    skipped = OrderedDict()
    errors = []

    total_checks = len(check_keys)
    for check_index, check_key in enumerate(check_keys):
        if cancel_event.is_set():
            break
        menu = menus.PLUGIN_MENUS.get(check_key)
        if menu is None or check_key in UNSUPPORTED:
            continue
        check_class = CHECK_CLASSES.get(menu.get('cat'))
        if check_class is None:
            continue

        scoped = _scope_for(db, menu, book_ids)
        if not scoped:
            skipped[check_key] = 'no books in scope have the required format'
            matched_by_check[check_key] = []
            continue

        gui.current_log = GUILog()
        gui.per_book_log = {}
        gui.log_tail_start = 0
        gui.pending_search = None
        gui.dialog_errors = []
        db.marks = {}

        def _progress(done, total, book_id, title, _ci=check_index, _key=check_key):
            if progress_cb is not None:
                progress_cb(_ci, total_checks, _key, done, total, book_id, title)

        gui.progress_cb = _progress

        check = check_class(gui)
        check.menu_key = check_key
        # 检查项把日志写进 BaseCheck.log（上游用它弹结果对话框、判 plain_text），而逐书明细
        # 的归属与"检查项级说明"都在 gui.current_log 上做——必须让两者是**同一个对象**，
        # 否则报告里每条问题都只剩"该检查项上游不输出逐条明细"（曾经就是这样）。
        check.log = gui.current_log
        check.set_search_scope('ids', scoped)
        try:
            check.perform_check(check_key)
        except Exception as e:
            errors.append({'book_id': None, 'check': check_key,
                           'error': '%s: %s' % (type(e).__name__, e)})

        log = gui.current_log
        matched = sorted(db.marks.keys())
        if not matched and gui.pending_search:
            matched, problem = _apply_search_check(db, api, gui.pending_search, scoped)
            if problem:
                notes[check_key] = [problem]
            if not matched:
                pass
        tail = _clean_lines(log.plain_text[gui.log_tail_start:]) if gui.log_tail_start else []
        if tail:
            notes.setdefault(check_key, []).extend(tail)

        for book_id in matched:
            detail = _clean_lines(gui.per_book_log.get(book_id) or [])
            findings[book_id][check_key].extend(detail)
        # Books the check flagged but never logged a line for still need an entry.
        for book_id in matched:
            findings[book_id].setdefault(check_key, [])

        matched_by_check[check_key] = matched
        for err in (gui.dialog_errors or []):
            errors.append({'book_id': err.get('book_id'), 'check': check_key,
                           'error': err.get('error')})

    return _build_report(db, book_ids, check_keys, findings, matched_by_check,
                         notes, skipped, errors, cancel_event)


# --- report filtering -------------------------------------------------------

def filter_report_books(books, severity='', check_key=''):
    """Filter report book entries by severity and/or check key (pure).

    Returns ``(books, issues_total)``. Each returned book carries only the issues that
    survived the filter, and ``issues_total`` counts those, so a caller can report
    numbers that match the rows it is about to send instead of the whole report's.
    """
    if not severity and not check_key:
        return list(books), sum(len(book.get('issues') or []) for book in books)

    out = []
    issues_total = 0
    for book in books:
        issues = book.get('issues') or []
        if severity:
            issues = [i for i in issues if i.get('severity') == severity]
        if check_key:
            issues = [i for i in issues if i.get('check') == check_key]
        if not issues:
            continue
        out.append(dict(book, issues=issues))
        issues_total += len(issues)
    return out, issues_total


# --- log lines --------------------------------------------------------------

_TAG_RE = re.compile(r'</?[a-zA-Z][^>]*>')


def clean_log_line(line):
    """Turn one upstream log line into plain report text (pure).

    Upstream writes its log for a rich-text dialog: ``<b>``/``<span>`` markup, ``&amp;``
    entities, and tab indentation for nesting. The report shows text, so strip those
    rather than printing raw markup at the user.
    """
    text = _TAG_RE.sub('', str(line if line is not None else ''))
    return html.unescape(text).strip()


def _clean_lines(lines):
    return [cleaned for cleaned in (clean_log_line(line) for line in lines) if cleaned]


# --- report views -----------------------------------------------------------

def summarize_report(report, sample=5):
    """Aggregate a finished report **by check** instead of by book (pure).

    The per-book view pages through the whole report; this is the other axis -- "which
    check is the dirtiest" -- so the frontend can render it from a single small response.
    Each row counts the books a check flagged, how many detail lines came with them, and
    keeps up to ``sample`` book references so the frontend can link into them.
    """
    per_check = OrderedDict()
    for book in report.get('books') or []:
        for issue in book.get('issues') or []:
            key = issue.get('check')
            entry = per_check.get(key)
            if entry is None:
                entry = per_check[key] = {
                    'check': key,
                    'name': issue.get('name')
                    or (menus.PLUGIN_MENUS.get(key) or {}).get('name', key),
                    'severity': issue.get('severity') or severity_for(key),
                    'noisy': key in NOISY_REASONS,
                    'books': 0,
                    'detail_lines': 0,
                    'sample': [],
                }
            entry['books'] += 1
            entry['detail_lines'] += len(issue.get('detail') or [])
            if len(entry['sample']) < sample:
                entry['sample'].append({
                    'book_id': book.get('book_id'),
                    'title': book.get('title') or '',
                })

    rows = sorted(per_check.values(), key=lambda entry: (-entry['books'], entry['check']))
    return {
        'total_books': report.get('total_books', 0),
        'books_with_issues': report.get('books_with_issues', 0),
        'issues_total': report.get('issues_total', 0),
        'severity_counts': report.get('severity_counts') or {},
        'checks_total': len(report.get('checks') or []),
        'checks_run': len(report.get('per_check') or {}),
        'checks_with_hits': len(rows),
        'checks': rows,
    }


# --- the last report, as seen from the next process -------------------------

LATEST_MARKER = 'latest.json'


def latest_marker(report):
    """The pointer written next to the reports so the next process can find the last one.

    MyBooks keeps background tasks in memory only, so "which task did I just run" dies
    with the process (installing/updating the tool or restarting MyBooks is enough) even
    though the report file itself stays on disk. The marker names that one report, and
    carries its ``generated_at``: task ids restart from 1 in a new process and can point
    at a directory an older run already wrote.
    """
    return {
        'task_id': int(report.get('task_id') or 0),
        'generated_at': report.get('generated_at') or '',
    }


def marker_matches(marker, report):
    """True when ``report`` is the very report ``marker`` was written for (pure)."""
    if not marker or not report:
        return False
    if int(marker.get('task_id') or 0) != int(report.get('task_id') or 0):
        return False
    return (marker.get('generated_at') or '') == (report.get('generated_at') or '')


def read_latest_marker(path):
    """Read the marker file, answering None for a missing, unreadable or torn one.

    A marker that cannot be trusted reads as "no marker": the caller falls back to the
    behaviour it had before there was one (an empty page), never to a wrong report.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_latest_marker(path, report):
    """Best-effort marker write: failing here must not fail the run (returns success).

    The report is already on disk by the time this is called, so the user's results are
    safe; all a failure costs is "the next visit reopens the last report by itself", and
    a torn file reads back as no marker.
    """
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(latest_marker(report), f, ensure_ascii=False)
    except OSError:
        return False
    return True


def restored_progress(report):
    """Shape a report read back from disk like the ``progress_data`` /progress serves (pure).

    Only used once the in-memory task is gone. ``status`` is the host's completed state
    (``BackgroundTask.STATUS_COMPLETED``); the driver stays host-free, hence the literal.
    ``restored`` tells the frontend this is the previous run's result, not one it just
    watched finish.
    """
    check_total = len(report.get('checks') or [])
    return {
        'status': 'completed',
        'progress': 100,
        'stage': 'done',
        'scope_label': report.get('scope_label', ''),
        'check_index': check_total,
        'check_total': check_total,
        'done': report.get('total_books', 0),
        'total': report.get('total_books', 0),
        'issues_total': report.get('issues_total', 0),
        'severity_counts': report.get('severity_counts') or {},
        'errors_count': len(report.get('errors') or []),
        'books_with_issues': report.get('books_with_issues', 0),
        'cancelled': report.get('cancelled', False),
        'restored': True,
    }


def _build_report(db, book_ids, check_keys, findings, matched_by_check, notes,
                  skipped, errors, cancel_event):
    books = []
    summary = OrderedDict()
    severity_counts = {'error': 0, 'warn': 0, 'info': 0}

    for book_id in book_ids:
        book_findings = findings.get(book_id)
        if not book_findings:
            continue
        issues = []
        for check_key in book_findings:
            severity = severity_for(check_key)
            detail = book_findings[check_key]
            if severity in severity_counts:
                severity_counts[severity] += 1
            summary[check_key] = summary.get(check_key, 0) + 1
            issues.append({
                'check': check_key,
                'name': (menus.PLUGIN_MENUS.get(check_key) or {}).get('name', check_key),
                'severity': severity,
                'noisy': check_key in NOISY_REASONS,
                'noisy_reason': NOISY_REASONS.get(check_key, ''),
                'detail': detail,
            })
        issues.sort(key=lambda i: ({'error': 0, 'warn': 1, 'info': 2}.get(i['severity'], 3), i['check']))
        books.append({
            'book_id': book_id,
            'title': db.title(book_id, index_is_id=True),
            'author': db.authors(book_id, index_is_id=True),
            'formats': db.available_formats(book_id),
            'issues': issues,
        })

    books.sort(key=lambda b: (-len(b['issues']), b['title'] or ''))

    return {
        'scope': {'total': len(book_ids)},
        'checked': len(book_ids),
        'checks': list(check_keys),
        'total_books': len(book_ids),
        'books_with_issues': len(books),
        'issues_total': sum(len(b['issues']) for b in books),
        'severity_counts': severity_counts,
        'summary': summary,
        'per_check': dict((k, len(v)) for k, v in matched_by_check.items()),
        'notes': dict(notes),
        'skipped': dict(skipped),
        'errors': errors,
        'cancelled': cancel_event.is_set(),
        'books': books,
    }
