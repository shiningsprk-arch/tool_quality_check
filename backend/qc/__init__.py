# -*- coding: utf-8 -*-
# GPL v3 — ported from Grant Drake's calibre "Quality Check" plugin
# (https://github.com/kiwidude68/calibre_plugins), Copyright 2011 Grant Drake.
#
# The ported check modules rely on calibre injecting the gettext alias ``_`` into
# builtins (they call it at module level). Install an identity fallback ONLY when
# nothing is there yet, so a host that already provides a translator keeps its own.
import builtins


def _identity_gettext(text):
    return text


if not hasattr(builtins, '_'):
    builtins._ = _identity_gettext
