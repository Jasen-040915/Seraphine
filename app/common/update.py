"""GitHub portable releases and staged Windows updates."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit
import zipfile

import requests


MAX_ARCHIVE_BYTES = 2 * 1024 ** 3 - 1
MAX_EXTRACTED_BYTES = 4 * 1024 ** 3


def version_key(value):
    """Support the existing +free.N version and its GitHub -free.N tag."""
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)(?:[+-]free\.(\d+))?', value)
    if not match:
        raise ValueError('不支持的版本号')
    return tuple(int(part or 0) for part in match.groups())


def release_update(info, current_version, repository):
    if info.get('draft') or info.get('prerelease'):
        return None
    tag = info.get('tag_name', '')
    try:
        if version_key(tag) <= version_key(current_version):
            return None
    except (ValueError, TypeError):
        return None
    version = tag.removeprefix('v').replace('-free.', '+free.')
    filenames = (f'{version.replace("+free.", ".")}.zip',
                 f'Seraphine-v{version.replace("+", "-")}-windows-x64.zip')
    for asset in info.get('assets', []):
        filename = asset.get('name')
        url = urlsplit(asset.get('browser_download_url', ''))
        if (filename in filenames and asset.get('state') == 'uploaded'
                and url.scheme == 'https' and url.netloc == 'github.com'
                and unquote(url.path) == f'/{repository}/releases/download/{tag}/{filename}'
                and re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or '')
                and 0 < asset.get('size', 0) <= MAX_ARCHIVE_BYTES):
            return dict(info, version=version, asset=asset, forbidden=False)
    return None


def github_proxy(enabled, address):
    if not enabled:
        return None
    address = address.strip()
    return address if '://' in address else 'http://' + address


def safe_relative(name):
    """Reject traversal, drive names, ADS and Windows path aliases."""
    path = PurePosixPath(name)
    if (not name or '\\' in name or path.is_absolute()
            or path.as_posix() != name or PureWindowsPath(name).is_reserved()
            or any(part in ('.', '..') or part.endswith((' ', '.'))
                   or re.search(r'[<>:"|?*\x00-\x1f]', part)
                   or PureWindowsPath(part).is_reserved() for part in path.parts)):
        raise ValueError('更新包包含不安全的路径')
    return path


def extract_update(archive_path, destination, version):
    """Extract a fresh directory, checking every manifest entry."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('更新临时目录已存在')
    with zipfile.ZipFile(archive_path) as archive:
        members = {}
        total = 0
        for item in archive.infolist():
            name = item.filename.rstrip('/') if item.is_dir() else item.filename
            relative = safe_relative(name)
            if relative.parts[0] != 'Seraphine' or stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError('更新包目录结构不正确')
            if item.is_dir():
                continue
            key = name.casefold()
            if key in members or len(relative.parts) < 2:
                raise ValueError('更新包包含重复文件')
            members[key] = item
            total += item.file_size
        if total > MAX_EXTRACTED_BYTES or len(members) > 20000:
            raise ValueError('更新包大小超过限制')
        manifest_info = members.get('seraphine/package-manifest.json')
        if manifest_info is None or manifest_info.file_size > 4 * 1024 ** 2:
            raise ValueError('更新包缺少文件校验清单')
        manifest = json.loads(archive.read(manifest_info))
        if manifest.get('format') != 1 or version_key(manifest.get('version', '')) != version_key(version):
            raise ValueError('更新包版本与发布版本不一致')
        expected = {}
        for record in manifest['files']:
            name = 'Seraphine/' + safe_relative(record['path']).as_posix()
            key = name.casefold()
            if key in expected or key == 'seraphine/package-manifest.json':
                raise ValueError('文件校验清单包含重复路径')
            if not re.fullmatch(r'[0-9a-f]{64}', record.get('sha256', '')):
                raise ValueError('文件校验清单不完整')
            expected[key] = record
        required = {'seraphine/seraphine.exe', 'seraphine/seraphineinput.exe',
                    'seraphine/python312.dll', 'seraphine/base_library.zip',
                    'seraphine/certifi/cacert.pem', 'seraphine/app/resource/updater.ps1'}
        if not required.issubset(expected) or set(members) != set(expected) | {'seraphine/package-manifest.json'}:
            raise ValueError('更新包文件不完整')
        destination.mkdir()
        for key, item in members.items():
            target = destination.joinpath(*PurePosixPath(item.filename).parts[1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(item) as source, target.open('xb') as output:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    output.write(block)
                    digest.update(block)
                    size += len(block)
            if key in expected and (size != expected[key]['bytes'] or digest.hexdigest() != expected[key]['sha256']):
                raise ValueError('更新包文件校验失败')
    return destination


def prepare_update(info, installation, proxy=None, progress=lambda done, total: None):
    installation = Path(installation).resolve(strict=True)
    if not (installation / 'Seraphine.exe').is_file() or installation.parent == installation:
        raise ValueError('请从完整便携版目录运行更新')
    workspace = Path(tempfile.mkdtemp(prefix=f'.{installation.name}-update-', dir=installation.parent))
    asset = info['asset']
    archive_path = workspace / 'update.zip'
    try:
        digest = hashlib.sha256()
        received = 0
        proxies = {'https': proxy} if proxy else None
        with requests.get(asset['browser_download_url'], stream=True, timeout=(10, 60), proxies=proxies) as response:
            response.raise_for_status()
            with archive_path.open('xb') as output:
                for block in response.iter_content(1024 * 1024):
                    received += len(block)
                    if received > asset['size']:
                        raise ValueError('下载文件大小与发布信息不一致')
                    output.write(block)
                    digest.update(block)
                    progress(received, asset['size'])
        if received != asset['size'] or 'sha256:' + digest.hexdigest() != asset['digest']:
            raise ValueError('下载校验失败，请重试')
        extract_update(archive_path, workspace / 'Seraphine', info['version'])
        archive_path.unlink()
        return workspace
    except BaseException:
        if workspace.resolve().parent == installation.parent:
            shutil.rmtree(workspace, ignore_errors=True)
        raise


def runUpdater(workspace, installation):
    workspace, installation = Path(workspace).resolve(), Path(installation).resolve()
    if (workspace.parent != installation.parent
            or not workspace.name.startswith(f'.{installation.name}-update-')
            or not (workspace / 'Seraphine/Seraphine.exe').is_file()):
        raise ValueError('更新临时目录无效')
    plan = {'target': str(installation), 'parent_pid': os.getpid()}
    (workspace / 'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    script = workspace / 'updater.ps1'
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'resource/updater.ps1', script)
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    process = subprocess.Popen([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                'Bypass', '-File', str(script)], cwd=workspace,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (workspace / 'ready').is_file():
            return
        if process.poll() is not None:
            raise RuntimeError('更新程序启动失败，请手动下载新版')
        time.sleep(.1)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError('更新程序启动超时，请重试')
