"""Verify a free Windows build, run isolated diagnostics, and include its source.

Run after staging the intended public files with Git. The installation and real
user profile are never changed by this script.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import types
import zipfile

from PyInstaller.archive.readers import CArchiveReader
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.package_portable import omit

def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    if isinstance(value, types.CodeType):
        return (value.co_code, value.co_argcount, value.co_posonlyargcount,
                value.co_kwonlyargcount, value.co_flags, value.co_names,
                value.co_varnames, value.co_freevars, value.co_cellvars,
                tuple(canonical(item) for item in value.co_consts))
    if isinstance(value, tuple):
        return tuple(canonical(item) for item in value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'dist/Seraphine')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tests-passed', type=int)
    args = parser.parse_args()
    bundle = args.bundle.absolute()
    version = re.search(r'^VERSION = "([^"]+)"', (ROOT / 'app/common/config.py').read_text(encoding='utf-8'), re.M)[1]
    main_archive = CArchiveReader(bundle / 'Seraphine.exe')
    pyz = main_archive.open_embedded_archive('PYZ.pyz')
    removed = ('app.common.entitlement', 'app.common.license_public_key',
               'app.common.shop_config', 'app.overlay.purchase')
    if any(name in pyz.toc for name in removed):
        raise ValueError('Paid-feature code remains in frozen program')
    matches = {}
    for name in sorted(pyz.toc):
        if not name.startswith('app.'):
            continue
        relative = Path(*name.split('.'))
        source = (ROOT / relative).with_suffix('.py')
        if not source.is_file():
            source = ROOT / relative / '__init__.py'
        if not source.is_file():
            continue
        matches[name] = canonical(compile(source.read_bytes(), str(source), 'exec', dont_inherit=True)) == canonical(pyz.extract(name))
    for name, executable in (('main', 'Seraphine.exe'), ('keyboard_worker', 'SeraphineInput.exe')):
        frozen = CArchiveReader(bundle / executable)
        matches[name] = canonical(compile((ROOT / (name + '.py')).read_bytes(), name + '.py', 'exec', dont_inherit=True)) == canonical(marshal.loads(frozen.extract(name)))
    required = {'app.overlay.panels', 'app.overlay.voice', 'app.overlay.feature_pages', 'app.common.config', 'main', 'keyboard_worker'}
    if not required.issubset(matches) or not all(matches.values()):
        raise ValueError('Frozen source mismatch: ' + ', '.join(name for name, ok in matches.items() if not ok))

    git = ['git', '-c', 'safe.directory=' + ROOT.as_posix(), '-C', str(ROOT)]
    sources = subprocess.check_output(git + ['ls-files', '-z']).decode('utf-8').strip('\0').split('\0')
    if not sources or 'scripts/validate_free_build.py' not in sources:
        raise ValueError('Stage the full intended public source first')
    source_zip = bundle / 'Seraphine-source.zip'
    with zipfile.ZipFile(source_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(sources):
            relative = PurePosixPath(name)
            path = ROOT / name
            if omit(relative, source=True) or path.is_symlink() or path.is_junction() or not path.is_file():
                raise ValueError('Private, generated, linked or missing source: ' + name)
            archive.write(path, 'Seraphine/' + name)
    with zipfile.ZipFile(source_zip) as archive:
        if archive.testzip():
            raise ValueError('Source archive CRC failure')
        if any(archive.read('Seraphine/' + name) != (ROOT / name).read_bytes() for name in sources):
            raise ValueError('Source archive differs from working source')

    profile = args.output.absolute().parent / 'packaged-profile'
    env = dict(os.environ, APPDATA=str(profile), QT_QPA_PLATFORM='offscreen')
    result_path = profile / 'SeraphinePlus/self-test/result.json'
    if result_path.exists():
        result_path.unlink()
    process = subprocess.run([str(bundle / 'Seraphine.exe'), '--self-test-full', '--self-test-voice'],
                             cwd=bundle, env=env, timeout=240, capture_output=True)
    if process.returncode or not result_path.is_file():
        raise ValueError('Packaged diagnostics did not complete: ' + str(process.returncode))
    packaged = json.loads(result_path.read_text(encoding='utf-8'))
    failed = [key for key, value in packaged.items() if type(value) is bool and not value]
    if failed or packaged['version'] != version:
        raise ValueError('Packaged diagnostics failed: ' + ', '.join(failed))
    record = {'version': version, 'tests_passed': args.tests_passed,
              'compiled_source_matches': matches, 'source_archive_matches': True,
              'removed_payment_modules_absent': True, 'packaged': packaged,
              'exe_sha256': sha(bundle / 'Seraphine.exe'),
              'keyboard_exe_sha256': sha(bundle / 'SeraphineInput.exe'),
              'source_archive_sha256': sha(source_zip)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'version': version, 'compiled_modules_verified': len(matches),
                      'source_files': len(sources), 'packaged_success': True,
                      'payment_modules_absent': True}))


if __name__ == '__main__':
    main()

