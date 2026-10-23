"""Package a validated Windows build without local profiles or diagnostics.

Run with the same virtual environment used for build-plus.ps1 so the dependency
notices correspond to the frozen runtime. This never changes the installation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OMIT_DIRS = {
    '.git', '.venv', '__pycache__', 'log', 'logs', 'diagnostics', 'self-test',
    'screenshots', 'profiles', 'profile', 'config', 'cache', '.pytest_cache',
    '.seller-private', 'secrets', 'state',
}
OMIT_NAMES = {
    'config.json', 'voice.json', 'overlay.json', 'voice-status.json',
    'overlay-status.json', 'keyboard-status.json', '.env',
    'entitlement.json', 'purchase-order.json',
}
SOURCE_OMIT_DIRS = OMIT_DIRS | {'artifacts', 'build', 'dist', 'models', '.vscode', 'ui-preview'}


def sha(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def omit(path: PurePosixPath, *, source: bool = False) -> bool:
    dirs = SOURCE_OMIT_DIRS if source else OMIT_DIRS
    return (any(part.casefold() in dirs for part in path.parts)
            or path.name.casefold() in OMIT_NAMES
            or path.suffix.casefold() in {'.log', '.pyc', '.pyo', '.dmp', '.pem', '.key', '.db', '.sqlite', '.sqlite3'}
            or '.sqlite' in path.name.casefold()
            or (path.name.casefold().startswith('.env.') and path.name.casefold() != '.env.example'))


def load_validated_build(version: str, validation: Path, bundle: Path) -> dict:
    record = json.loads(validation.read_text(encoding='utf-8'))
    if record['version'] != version:
        raise ValueError('Validation version differs from requested version')
    expected = {
        'exe_sha256': bundle / 'Seraphine.exe',
        'keyboard_exe_sha256': bundle / 'SeraphineInput.exe',
        'source_archive_sha256': bundle / 'Seraphine-source.zip',
    }
    for key, path in expected.items():
        if sha(path) != record[key]:
            raise ValueError(f'Build differs from validation: {path.name}')
    matches = record.get('compiled_source_matches', {})
    if not matches or not all(matches.values()) or not record.get('source_archive_matches'):
        raise ValueError('Matching compiled code and source have not been validated')
    packaged = record.get('packaged', {})
    if packaged.get('version') != version or not packaged.get('success'):
        raise ValueError('This version has not passed packaged self-tests')
    required = (
        'python312.dll', 'base_library.zip', 'LICENSE',
        'models/sensevoice/model.int8.onnx', 'models/sensevoice/tokens.txt',
        'models/sensevoice/LICENSE', 'models/sensevoice/FunASR-MODEL-LICENSE.txt',
        'models/sensevoice/SenseVoice-LICENSE.txt',
        'models/sensevoice/sherpa-onnx-LICENSE.txt',
        'models/sensevoice/ONNXRuntime-LICENSE.txt',
    )
    for name in required:
        if not (bundle / name).is_file():
            raise ValueError(f'Missing runtime component: {name}')
    if not list((bundle / 'PyQt5').rglob('qwindows.dll')):
        raise ValueError('Missing Qt Windows platform plugin')
    with zipfile.ZipFile(bundle / 'Seraphine-source.zip') as source:
        if source.testzip() is not None:
            raise ValueError('Source archive failed CRC check')
        for required_source in ('LICENSE', 'readme.md', 'MODIFICATIONS.md',
                                'requirements.txt', 'requirements-plus.txt',
                                'build-plus.ps1', 'main.py', 'keyboard_worker.py'):
            if 'Seraphine/' + required_source not in source.namelist():
                raise ValueError(f'Missing corresponding source or notice: {required_source}')
        if source.read('Seraphine/LICENSE') != (ROOT / 'LICENSE').read_bytes():
            raise ValueError('Source archive does not preserve the project license')
        for name in source.namelist():
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or not name.startswith('Seraphine/'):
                raise ValueError('Unexpected source archive path')
            relative = PurePosixPath(*path.parts[1:])
            if omit(relative, source=True):
                raise ValueError(f'Private or generated data in source archive: {relative}')
        config = source.read('Seraphine/app/common/config.py').decode('utf-8-sig')
        if not re.search(r'^VERSION\s*=\s*[\"\']' + re.escape(version) + r'[\"\']', config, re.M):
            raise ValueError('Source archive version differs from requested version')
    return record


def dependency_notices() -> tuple[dict[str, Path | bytes], list[dict]]:
    files: dict[str, Path | bytes] = {}
    dependencies = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata['Name'].casefold()):
        name, version = dist.metadata['Name'], dist.version
        if name.casefold() == 'pip':
            continue
        component = re.sub(r'[^A-Za-z0-9_.-]+', '-', name) + '-' + version
        notices = []
        for item in dist.files or ():
            path = PurePosixPath(str(item).replace('\\', '/'))
            if '..' in path.parts:
                continue
            # Include wheel-shipped license/attribution files, including vendored
            # components, without collecting arbitrary package source or data.
            if not re.match(r'(?i)^(licen[sc]e|copying|notice|authors)([._-].*)?$', path.name):
                continue
            full = Path(dist.locate_file(item))
            if full.is_file():
                target = f'licenses/dependencies/{component}/{path.as_posix()}'
                files[target] = full
                notices.append(target)
        if name.casefold() == 'pyqt5' and not notices:
            target = f'licenses/dependencies/{component}/LICENSE-GPLv3.txt'
            files[target] = ROOT / 'LICENSE'
            notices.append(target)
        dependencies.append({
            'name': name, 'version': version, 'notice_files': notices,
            'license_expression': dist.metadata.get('License-Expression', ''),
            'homepage': dist.metadata.get('Home-page', ''),
            'project_urls': dist.metadata.get_all('Project-URL') or [],
        })
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_license.is_file():
        raise ValueError('Missing Python runtime license in build environment')
    files['licenses/Python-LICENSE.txt'] = python_license
    files['licenses/Seraphine-LICENSE-GPLv3.txt'] = ROOT / 'LICENSE'
    files['licenses/VOICE-THIRD-PARTY-NOTICES.md'] = ROOT / 'app/resource/voice/THIRD_PARTY_NOTICES.md'
    files['licenses/dependency-inventory.json'] = json.dumps(dependencies, ensure_ascii=False, indent=2).encode('utf-8')
    files['BUILD-DEPENDENCIES.txt'] = ('\n'.join(f"{d['name']}=={d['version']}" for d in dependencies) + '\n').encode('utf-8')
    return files, dependencies


def collect(bundle: Path) -> dict[str, Path | bytes]:
    files: dict[str, Path | bytes] = {}
    for path in sorted(bundle.rglob('*')):
        relative = path.relative_to(bundle)
        if omit(PurePosixPath(relative.as_posix())):
            continue
        if path.is_symlink() or path.is_junction():
            raise ValueError(f'Unexpected linked path in release: {relative}')
        if path.is_file():
            files[relative.as_posix()] = path
    notices, _ = dependency_notices()
    for name, content in notices.items():
        if name in files:
            raise ValueError(f'Notice path collision: {name}')
        files[name] = content
    files['便携版使用说明.md'] = (ROOT / 'document/便携版使用说明.md').read_bytes()
    files['THIRD_PARTY_NOTICES.md'] = (ROOT / 'document/THIRD_PARTY_NOTICES.md').read_bytes()
    files['原项目README.md'] = (ROOT / 'document/UPSTREAM_README.md').read_bytes()
    files['MODIFICATIONS.md'] = (ROOT / 'MODIFICATIONS.md').read_bytes()
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--validation', required=True, type=Path)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'dist/Seraphine')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--audit-only', action='store_true')
    args = parser.parse_args()
    # abspath avoids requesting an extra directory handle on managed Windows
    # hosts. collect() separately rejects symbolic links and junctions.
    bundle = args.bundle.absolute()
    if not bundle.is_dir() or bundle.is_symlink() or bundle.is_junction():
        raise ValueError('Bundle must be an existing ordinary directory')
    record = load_validated_build(args.version, args.validation, bundle)
    files = collect(bundle)
    if args.audit_only:
        print(json.dumps({'version': args.version, 'files': len(files), 'ready': True}))
        return
    if args.output is None:
        parser.error('--output is required unless --audit-only is supplied')
    output = args.output.absolute()
    if output == bundle or bundle in output.parents:
        raise ValueError('Output must be outside the runtime bundle')
    manifest = {
        'format': 1, 'version': args.version,
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'target': 'Windows 10/11 x64',
        'distribution_purpose': 'free_open_source',
        'tests_passed': record.get('tests_passed'),
        'compiled_source_matches': True, 'packaged_self_test_passed': True,
        'other_computer_tested': False, 'commercial_clearance': 'not_assessed',
        'profiles_logs_and_diagnostics_included': False,
        'files': [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents an existing delivery from being replaced.
    with output.open('xb') as stream:
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=5) as archive:
            for name, content in sorted(files.items()):
                if isinstance(content, Path):
                    digest, size = sha(content), content.stat().st_size
                    archive.write(content, 'Seraphine/' + name)
                else:
                    digest, size = hashlib.sha256(content).hexdigest(), len(content)
                    archive.writestr('Seraphine/' + name, content)
                manifest['files'].append({'path': name, 'bytes': size, 'sha256': digest})
            archive.writestr('Seraphine/PACKAGE-MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release archive failed CRC check')
    print(json.dumps({'version': args.version, 'files': len(files), 'bytes': output.stat().st_size,
                      'sha256': sha(output), 'output': str(output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
