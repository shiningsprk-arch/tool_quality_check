# -*- coding: utf-8 -*-
"""calibre.utils.config.JSONConfig -> a JSON file (or memory).

The driver calls ``set_config_dir()`` with the host's tool work dir; until then the
values simply live in memory.
"""
import json
import os

CONFIG_DIR = None


def set_config_dir(path):
    global CONFIG_DIR
    CONFIG_DIR = path
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass


class JSONConfig(dict):
    def __init__(self, name):
        dict.__init__(self)
        self.name = name
        self.defaults = {}
        self.file_path = None
        self.load()

    # calibre uses names like 'plugins/Quality Check'; flatten to a safe filename.
    def _path(self):
        if not CONFIG_DIR:
            return None
        slug = ''.join(c if (c.isalnum() or c in '-_') else '_' for c in self.name)
        return os.path.join(CONFIG_DIR, slug + '.json')

    def load(self):
        self.file_path = self._path()
        if self.file_path and os.path.exists(self.file_path):
            try:
                with io_open(self.file_path) as f:
                    self.update(json.load(f))
            except (OSError, ValueError):
                pass

    def commit(self):
        self.file_path = self._path()
        if not self.file_path:
            return
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(dict(self), f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # calibre's JSONConfig persists on assignment; keep that so the ported config code
    # keeps working untouched.
    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self.commit()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self.commit()

    def __getitem__(self, key):
        # calibre's JSONConfig falls back to .defaults on read; the ported config code
        # depends on that (it registers a defaults group, then reads it back by key).
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        return self.defaults[key]

    def get(self, key, default=None):
        if key in self:
            return dict.get(self, key)
        return self.defaults.get(key, default)


def io_open(path):
    return open(path, 'r', encoding='utf-8')


# calibre.utils.config.prefs -- only read by the fix module, which the port omits.
prefs = JSONConfig('prefs')
