# -*- coding: utf-8 -*-
"""Non-Qt replacements for the dialogs Quality Check drives its checks with.

This module is the seam that lets the ported check code stay untouched:

* ``QualityProgressDialog`` is where calibre's QProgressDialog ran the per-book loop on
  a QTimer. Here it *is* the loop -- synchronous, cancellable, exception-tolerant -- and
  it is also where each book's log output is attributed to that book, which is how the
  report gets its per-book detail.
* ``ResultsSummaryDialog`` is a no-op sink: Quality Check popped it up at the end of a
  check, the port reports through JSON instead.
* ``CoverOptionsDialog`` emulates just enough of the Qt widget for ``check_covers`` to
  read its criteria back out; the values come from the tool's options.

Anything Quality Check only used from the Fix submenu is intentionally absent.
"""

try:
    load_translations()
except NameError:
    pass  # calibre injects load_translations(); the port runs without it


def truncate_title(title, length=75):
    title = title or ''
    return (title[:length] + '...') if len(title) > length else title


class QualityProgressDialog(object):
    """Runs ``callback_fn(book_id, db)`` for every book, like calibre's dialog did."""

    def __init__(self, gui, book_ids, callback_fn, db, status_msg_type='books',
                 action_type='Checking'):
        self.gui = gui
        self.db = db
        self.book_ids = list(book_ids)
        self.callback_fn = callback_fn
        self.total_count = len(self.book_ids)
        self.status_msg_type = status_msg_type
        self.action_type = action_type
        self.result_ids = []
        self.cancelled = False
        self.errors = []
        self._run()
        # Everything the check writes after the loop (cross-book summaries, for example
        # series_gaps' missing-index lines) is check-level detail, not per-book detail.
        log = getattr(gui, 'current_log', None)
        gui.log_tail_start = len(log.plain_text) if log is not None else 0
        # One check can raise the dialog more than once; the driver resets this per check.
        gui.dialog_errors = list(getattr(gui, 'dialog_errors', None) or []) + self.errors

    def _run(self):
        gui = self.gui
        log = getattr(gui, 'current_log', None)
        cancel_event = getattr(gui, 'cancel_event', None)
        per_book_log = getattr(gui, 'per_book_log', None)

        for index, book_id in enumerate(self.book_ids):
            if cancel_event is not None and cancel_event.is_set():
                self.cancelled = True
                break

            before = len(log.plain_text) if log is not None else 0
            try:
                matched = self.callback_fn(book_id, self.db)
            except Exception as e:  # a bad book must not abort a library-wide check
                self.errors.append({'book_id': book_id, 'error': '%s: %s' % (type(e).__name__, e)})
                if log is not None:
                    log.error('Unhandled error: %s: %s' % (type(e).__name__, e))
                matched = False

            if log is not None and per_book_log is not None:
                delta = log.plain_text[before:]
                if delta:
                    per_book_log.setdefault(book_id, []).extend(delta)

            if matched:
                self.result_ids.append(book_id)

            progress_cb = getattr(gui, 'progress_cb', None)
            if progress_cb is not None:
                title = ''
                try:
                    title = self.db.title(book_id, index_is_id=True)
                except Exception:
                    title = ''
                progress_cb(index + 1, self.total_count, book_id, title)

    def wasCanceled(self):
        return self.cancelled

    # The ported code calls exec_()/hide() the way it did on the Qt dialog.
    def exec_(self):
        return None

    def hide(self):
        return None


class ResultsSummaryDialog(object):
    """Sink for the summary popups Quality Check raised after each check."""

    def __init__(self, parent=None, title='', msg='', log=None, det_msg='', **kwargs):
        self.parent = parent
        self.title = title
        self.msg = msg
        self.log = log
        self.det_msg = det_msg

    def exec_(self):
        return None

    def show(self):
        return None


class _FakeOption(object):
    def __init__(self, checked=False):
        self._checked = bool(checked)

    def isChecked(self):
        return self._checked

    def setChecked(self, value):
        self._checked = bool(value)


class CoverOptionsDialog(object):
    """Enough of the cover-criteria dialog for ``check_covers`` to read back.

    ``check_covers`` inspects the widget directly, so the option values are injected
    here by the tool from its own request payload before the check runs.

    ``mode`` is the tool-side single choice over the four radio buttons:

    ``'no_cover'`` / ``'file_size'`` / ``'dimensions'`` / ``'aspect'`` select one, and
    anything else (``'none'``, empty, a missing or misspelled value) means "no cover
    criteria were asked for". That last case must answer *Rejected*, because
    ``check_covers`` reads ``result()`` first: answering Accepted would walk the whole
    library, materialise and decode every cover, and mark nothing. Criteria that were
    never specified should therefore not run a check at all.
    """

    Accepted = 1
    Rejected = 0

    MODES = ('no_cover', 'file_size', 'dimensions', 'aspect')

    #: {'mode': 'file_size'|'dimensions'|'aspect'|'no_cover'|'none',
    #:  'operator': str, 'file_size': int(KB), 'image_width'/'image_height': int,
    #:  'aspect_x'/'aspect_y'/'aspect_tolerance_pct': number}
    OPTIONS = {}

    def __init__(self, gui=None):
        opts = self.OPTIONS or {}
        mode = opts.get('mode') or 'none'
        if mode not in self.MODES:
            mode = 'none'
        self.mode = mode
        self.opt_no_cover = _FakeOption(mode == 'no_cover')
        self.opt_file_size = _FakeOption(mode == 'file_size')
        self.opt_dimensions = _FakeOption(mode == 'dimensions')
        self.opt_aspect_ratio = _FakeOption(mode == 'aspect')

        self.check_operator = opts.get('operator', 'less than')
        self.file_size = int(opts.get('file_size', 50))
        self.image_width = int(opts.get('image_width', 600))
        self.image_height = int(opts.get('image_height', 800))
        self.aspect_x = float(opts.get('aspect_x', 2))
        self.aspect_y = float(opts.get('aspect_y', 3))
        self.aspect_tolerance_pct = float(opts.get('aspect_tolerance_pct', 10))
        self._result = self.Accepted if mode != 'none' else self.Rejected

    def exec_(self):
        return None

    def result(self):
        return self._result


class SearchEpubDialog(object):
    """Placeholder so check_epub stays importable.

    The port does not offer Quality Check's regex "Search ePubs" feature, so this is
    never instantiated.
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError('the read-only port does not include Search ePubs')
