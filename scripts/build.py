# -*- coding: utf-8 -*-
"""打包质量体检工具为可直接上传的 zip。

宿主只读归档根目录，因此 zip 里必须是 `manifest.json` + `backend/` + `frontend/` + `icon.png`
（外加 GPL 要求的 `LICENSE`），不能套一层文件夹。

这里用 stdlib 自己打包而不是调 `mytool build`：离线可复现、无需 npm，并且顺带做一轮产物
形状校验（混进字节码、缺文件、manifest 指向不存在的模块都在构建期就报错）。需要脚手架产
物时再跑 `--mytool`。

用法：python scripts/build.py [--out dist] [--mytool] [--keep]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 归档根的允许条目（多一层或少一层都会被宿主判为非法包）
ROOT_ENTRIES = {'manifest.json', 'icon.png', 'LICENSE', 'backend', 'frontend'}
REQUIRED = (
    'manifest.json',
    'icon.png',
    'LICENSE',
    'backend/__init__.py',
    'backend/tool.py',
    # 本仓新增的三种格式的检查（上游没有这些文件）：漏打包要到装完看报告才会发现少了一整组
    'backend/qc/check_pdf.py',
    'backend/qc/check_txt.py',
    'backend/qc/check_azw3.py',
    'frontend/index.html',
    'frontend/app.js',
    'frontend/lib/i18n.js',
    'frontend/lib/theme.css',
    'frontend/lib/quality_check.css',
    'frontend/locales/manifest.json',
    'frontend/locales/zh.json',
    'frontend/locales/en.json',
    'frontend/locales/zh-TW.json',
)
FORBIDDEN_PARTS = ('__pycache__', '.pyc', '.pyo', '.DS_Store', '.porting', '_smoke', '.git')
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def clean_bytecode():
    removed = 0
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        if os.sep + '.git' in dirpath:
            continue
        for name in list(dirnames):
            if name == '__pycache__':
                shutil.rmtree(os.path.join(dirpath, name), ignore_errors=True)
                dirnames.remove(name)
                removed += 1
        for name in filenames:
            if name.endswith(('.pyc', '.pyo')):
                os.remove(os.path.join(dirpath, name))
                removed += 1
    return removed


def collect_files():
    """(zip 内路径, 绝对路径) 列表，按固定顺序，便于产物可复现。"""
    files = []
    for name in ('manifest.json', 'icon.png', 'LICENSE'):
        path = os.path.join(REPO_ROOT, name)
        if os.path.exists(path):
            files.append((name, path))
    for top in ('backend', 'frontend'):
        base = os.path.join(REPO_ROOT, top)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != '__pycache__')
            for name in sorted(filenames):
                if name.endswith(('.pyc', '.pyo')):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, REPO_ROOT).replace(os.sep, '/')
                files.append((rel, full))
    return files


def validate_source():
    errors = []
    manifest_path = os.path.join(REPO_ROOT, 'manifest.json')
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            manifest = json.load(f)
    except (OSError, ValueError) as e:
        return None, ['manifest.json 无法解析：%s' % e]

    if not re.match(r'^[a-z0-9_]+$', manifest.get('tool_id', '')):
        errors.append('tool_id 必须是 ^[a-z0-9_]+$：%r' % manifest.get('tool_id'))
    if not re.match(r'^\d+\.\d+\.\d+$', manifest.get('revision', '')):
        errors.append('revision 必须是 x.y.z：%r' % manifest.get('revision'))
    for key in ('name', 'description', 'author', 'repo_url', 'core_api_version',
                'entry_backend', 'entry_frontend'):
        if not manifest.get(key):
            errors.append('manifest 缺少字段：%s' % key)
    if manifest.get('entry_frontend') != 'index.html':
        errors.append('entry_frontend 必须是 index.html（宿主硬编码 frontend/index.html）')
    if manifest.get('default_locale') not in (manifest.get('locales') or []):
        errors.append('default_locale 必须在 locales 里')

    # entry_backend / api_routes 指向的模块文件必须真的存在
    def module_file(dotted):
        module = dotted.split('.')[0]
        if not module:
            return None
        return os.path.join(REPO_ROOT, 'backend', module + '.py')

    backend = manifest.get('entry_backend', '')
    if backend:
        path = module_file(backend)
        if not path or not os.path.exists(path):
            errors.append('entry_backend 指向的模块不存在：%s' % backend)
    seen_paths = set()
    for route in manifest.get('api_routes') or []:
        path = route.get('path')
        handler = route.get('handler', '')
        if not path:
            errors.append('api_routes 有空 path')
        if path in seen_paths:
            errors.append('api_routes path 重复：%s' % path)
        seen_paths.add(path)
        module = module_file(handler)
        if not module or not os.path.exists(module):
            errors.append('api_routes handler 指向的模块不存在：%s' % handler)

    # 三语 key 必须一致
    locales_dir = os.path.join(REPO_ROOT, 'frontend', 'locales')
    keysets = {}
    for code in manifest.get('locales') or []:
        path = os.path.join(locales_dir, code + '.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                keysets[code] = set(json.load(f).keys())
        except (OSError, ValueError) as e:
            errors.append('locale 读取失败 %s：%s' % (code, e))
    if keysets:
        base_code = sorted(keysets)[0]
        for code, keys in keysets.items():
            missing = keysets[base_code] - keys
            extra = keys - keysets[base_code]
            if missing:
                errors.append('%s 缺少 key：%s' % (code, sorted(missing)[:5]))
            if extra:
                errors.append('%s 多出 key：%s' % (code, sorted(extra)[:5]))

    # 图标必须是 PNG 且是正方形
    icon = os.path.join(REPO_ROOT, 'icon.png')
    if os.path.exists(icon):
        with open(icon, 'rb') as f:
            head = f.read(24)
        if not head.startswith(PNG_MAGIC):
            errors.append('icon.png 不是 PNG（宿主只认 PNG）')
        else:
            width = int.from_bytes(head[16:20], 'big')
            height = int.from_bytes(head[20:24], 'big')
            if width != height:
                errors.append('icon.png 不是正方形：%dx%d' % (width, height))
    return manifest, errors


def build_zip(manifest, out_dir):
    revision = manifest['revision']
    tool_id = manifest['tool_id']
    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, '%s-%s.zip' % (tool_id, revision))

    files = collect_files()
    errors = []
    names = set()
    for rel, _ in files:
        names.add(rel)
        head = rel.split('/')[0]
        if head not in ROOT_ENTRIES:
            errors.append('不该出现在包里的顶层条目：%s' % rel)
        if any(part in rel for part in FORBIDDEN_PARTS):
            errors.append('应排除的文件进了包：%s' % rel)
    for required in REQUIRED:
        if required not in names:
            errors.append('缺少必需文件：%s' % required)
    if errors:
        print('✗ 产物内容校验失败：')
        for line in errors:
            print('  - %s' % line)
        sys.exit(1)

    # 固定时间戳，让相同输入产出相同字节
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel, full in files:
            info = zipfile.ZipInfo(rel, date_time=(2026, 9, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(full, 'rb') as f:
                zf.writestr(info, f.read())

    with open(zip_path, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return zip_path, digest, len(files)


def run_mytool():
    import subprocess
    cmd = ['npx', '--yes', 'mybooks-tools-builder@latest', 'build']
    print('运行：%s' % ' '.join(cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT, shell=(os.name == 'nt'))
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default=os.path.join(REPO_ROOT, 'dist'))
    parser.add_argument('--keep', action='store_true', help='保留 dist 里的旧产物')
    parser.add_argument('--mytool', action='store_true', help='额外调用脚手架 mytool build')
    args = parser.parse_args()

    if not args.keep:
        shutil.rmtree(args.out, ignore_errors=True)
    print('清理字节码：移除 %d 项' % clean_bytecode())

    manifest, errors = validate_source()
    if errors:
        print('✗ 源校验失败：')
        for line in errors:
            print('  - %s' % line)
        sys.exit(1)
    print('源校验通过：%s %s' % (manifest['tool_id'], manifest['revision']))

    zip_path, digest, count = build_zip(manifest, args.out)
    print('✔ 已打包：%s（%d 个条目）' % (os.path.relpath(zip_path, REPO_ROOT), count))
    print('  sha256: %s' % digest)

    if args.mytool:
        run_mytool()


if __name__ == '__main__':
    main()
