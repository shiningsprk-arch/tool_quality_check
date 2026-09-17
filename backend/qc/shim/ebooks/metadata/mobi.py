# -*- coding: utf-8 -*-
"""calibre.ebooks.metadata.mobi.MetadataUpdater -- NOT ported.

Only the ASIN *fix* used this class, and the port is read-only, so it exists purely to
keep mobi6.py importable. Touching it raises.
"""


class MetadataUpdater(object):
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            'the read-only port does not include MOBI metadata writing')
