# -*- coding: utf-8 -*-
"""calibre.utils.logging -> a tiny logger.

The ported checks build their per-book detail text by writing into the log; the driver
reads ``plain_text`` (list of lines) and attributes the delta of each book's callback to
that book, which is how per-book findings are collected.
"""
from html import escape as _escape

__all__ = ['Log', 'GUILog']


class Log(object):
    def __init__(self):
        self.plain_text = []
        self.html = ''

    # calibre's Log is callable and stringifies+joins any number of positional values;
    # the ported code relies on that (e.g. error('Invalid epub:', exc)).
    def __call__(self, *args, **kwargs):
        self.info(*args, **kwargs)

    @staticmethod
    def _render(args):
        parts = []
        for a in args:
            if isinstance(a, BaseException):
                parts.append('%s: %s' % (type(a).__name__, a))
            else:
                parts.append('%s' % (a,))
        return ' '.join(parts)

    def _append(self, line):
        if not line:
            return
        self.plain_text.append(line)
        self.html += '<p>%s</p>' % _escape(line)

    def info(self, *args, **kwargs):
        self._append(self._render(args))

    def warn(self, *args, **kwargs):
        self._append(self._render(args))

    def error(self, *args, **kwargs):
        self._append(self._render(args))

    def debug(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        self._append(self._render(args))

    def clear(self):
        self.plain_text = []
        self.html = ''


class GUILog(Log):
    pass
