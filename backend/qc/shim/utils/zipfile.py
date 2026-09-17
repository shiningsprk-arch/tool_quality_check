# -*- coding: utf-8 -*-
"""calibre.utils.zipfile -> stdlib zipfile.

calibre's fork tolerates a few more malformed archives than the stdlib one; for a
diagnostic tool that is acceptable, since a book we cannot open is itself a finding.
"""
from zipfile import (  # noqa: F401
    ZipFile, BadZipFile, BadZipfile, ZipInfo, LargeZipFile,
    ZIP_STORED, ZIP_DEFLATED, ZIP_BZIP2, ZIP_LZMA,
)
