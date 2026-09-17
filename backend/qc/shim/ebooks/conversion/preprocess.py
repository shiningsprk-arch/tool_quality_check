# -*- coding: utf-8 -*-
"""calibre.ebooks.conversion.preprocess.HTMLPreProcessor.

calibre passes this into its HTML parser to normalise sheets/Word markup. The port's
parse_html (lxml) accepts and ignores it.
"""


class HTMLPreProcessor(object):
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
