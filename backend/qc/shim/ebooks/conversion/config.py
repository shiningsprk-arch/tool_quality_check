# -*- coding: utf-8 -*-
"""calibre.ebooks.conversion.config.load_defaults -> fixed page-setup margins.

Quality Check's CSS-margin check compares a book against calibre's conversion
page-setup preferences. The port has no conversion settings to read, so it uses
calibre's own defaults (5pt on all four sides) -- see the check's option in the UI.
"""

_UNSET = 5.0


class ConversionConfig(dict):
    pass


def load_defaults(device=None):  # noqa: ARG001 - calibre's signature takes a device
    cfg = ConversionConfig()
    cfg['page_setup'] = {
        'margin_top': _UNSET,
        'margin_right': _UNSET,
        'margin_bottom': _UNSET,
        'margin_left': _UNSET,
    }
    return cfg
