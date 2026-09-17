# -*- coding: utf-8 -*-
"""six -> the handful of aliases the ported code touches (py3 only)."""
from . import moves  # noqa: F401  (needed for six.moves.urllib.* attribute access)

PY2 = False
PY3 = True
text_type = str
binary_type = bytes
string_types = (str,)
integer_types = (int,)
class_types = (type,)
unichr = chr
long = int
MAXSIZE = 9223372036854775807


def iteritems(d, **kw):
    return iter(d.items(**kw))


def itervalues(d, **kw):
    return iter(d.values(**kw))


def iterkeys(d, **kw):
    return iter(d.keys(**kw))
