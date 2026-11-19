"""Regression coverage for version ordering, downloads and real Windows replacement."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import zipfile

import requests

from app.common.update import extract_update, prepare_update, release_update, version_key


ROOT = Path(__file__).resolve().parents[1]
REPO = 'Jasen-040915/Seraphine'


def release(version='1.1.4+free.8'):
    tag = 'v' + version.replace('+', '-')
    filename = f'Seraphine-{tag}-windows-x64.zip'
    asset = dict(name=filename, state='uploaded', size=123, digest='sha256:' + '0' * 64,
                 browser_download_url=f'https://github.com/{REPO}/releases/download/{tag}/{filename}')
    return dict(tag_name=tag, draft=False, prerelease=False, assets=[asset], body='Test')


def make_package(path, version='1.1.4+free.8', extra=None, wrong_hash=False):
    files = dict.fromkeys(('Seraphine.exe', 'SeraphineInput.exe', 'python312.dll',
                          'base_library.zip', 'certifi/cacert.pem',
                          'app/resource/updater.ps1'), b'test-content')
    manifest = {'format': 1, 'version': version, 'files': [
        {'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        for name, data in files.items()]}
    if wrong_hash:
        manifest['files'][0]['sha256'] = 'f' * 64
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr('Seraphine/' + name, data)
        archive.writestr('Seraphine/PACKAGE-MANIFEST.json', json.dumps(manifest))
        if extra:
            archive.writestr(extra, b'bad')


class UpdateTests(unittest.TestCase):
    def test_version_order_and_release_selection(self):
        self.assertGreater(version_key('v1.1.4-free.10'), version_key('1.1.4+free.9'))
        self.assertGreater(version_key('1.1.5'), version_key('1.1.4+free.100'))
        info = release()
        info['assets'].insert(0, dict(name='source.zip'))
        selected = release_update(info, '1.1.4+free.7', REPO)
        self.assertEqual(selected['asset']['name'], 'Seraphine-v1.1.4-free.8-windows-x64.zip')
        for version in ('1.1.4+free.8', '1.1.4+free.9', '1.1.5'):
            self.assertIsNone(release_update(info, version, REPO))

    def test_unfinished_or_untrusted_releases_are_ignored(self):
        for changes in ({'draft': True}, {'prerelease': True}, {'assets': []},
                        {'tag_name': 'v1.1.4-gold.31'}):
            self.assertIsNone(release_update(dict(release(), **changes), '1.1.4+free.7', REPO))
        for changes in ({'digest': None}, {'state': 'new'}, {'size': 0},
                        {'browser_download_url': 'https://example.com/update.zip'}):
            info = release()
            info['assets'][0].update(changes)
            self.assertIsNone(release_update(info, '1.1.4+free.7', REPO))

    def test_numeric_asset_names_match_the_release_version(self):
        for version in ('1.1.4+free.8', '1.1.5'):
            info = release(version)
            filename = version.replace('+free.', '.') + '.zip'
            info['assets'][0].update(
                name=filename,
                browser_download_url=f'https://github.com/{REPO}/releases/download/{info["tag_name"]}/{filename}')
            selected = release_update(info, '1.1.4+free.7', REPO)
            self.assertEqual(selected['asset']['name'], filename)
            self.assertEqual(selected['version'], version)
        for filename in ('1.1.4.9.zip', '1.1.4.8-source.zip'):
            info = release()
            info['assets'][0].update(
                name=filename,
                browser_download_url=f'https://github.com/{REPO}/releases/download/{info["tag_name"]}/{filename}')
            self.assertIsNone(release_update(info, '1.1.4+free.7', REPO))

    def test_manifest_and_windows_path_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / 'new.zip'
            make_package(package)
            extracted = extract_update(package, root / 'good', '1.1.4+free.8')
            self.assertTrue((extracted / 'Seraphine.exe').is_file())
            for index, name in enumerate(('Seraphine/../../outside', 'Seraphine/C:/evil',
                                         'Seraphine/a:stream', 'Seraphine/CON.txt',
                                         'Seraphine/seraphine.exe', 'Seraphine/a./b')):
                make_package(package, extra=name)
                with self.assertRaises(ValueError, msg=name):
                    extract_update(package, root / str(index), '1.1.4+free.8')
            make_package(package, wrong_hash=True)
            with self.assertRaises(ValueError):
                extract_update(package, root / 'bad-hash', '1.1.4+free.8')

    def test_failed_download_preserves_installation_and_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installation = root / '中文 空格'
            installation.mkdir()
            (installation / 'Seraphine.exe').write_bytes(b'old')
            package = root / 'new.zip'
            make_package(package)
            data = package.read_bytes()
            info = release_update(release(), '1.1.4+free.7', REPO)
            info['asset'].update(size=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            for chunks in ([b'broken'], requests.ConnectionError('offline')):
                response.iter_content.side_effect = chunks if isinstance(chunks, Exception) else None
                response.iter_content.return_value = iter(chunks) if isinstance(chunks, list) else None
                with patch('app.common.update.requests.get', return_value=response):
                    with self.assertRaises((ValueError, requests.RequestException)):
                        prepare_update(info, installation)
                self.assertEqual((installation / 'Seraphine.exe').read_bytes(), b'old')
                self.assertEqual(list(root.glob('.*-update-*')), [])
            response.iter_content.side_effect = None
            response.iter_content.return_value = [data]
            with patch('app.common.update.requests.get', return_value=response):
                workspace = prepare_update(info, installation)
            self.assertEqual((workspace / 'Seraphine/Seraphine.exe').read_bytes(), b'test-content')
            self.assertEqual((installation / 'Seraphine.exe').read_bytes(), b'old')

    def test_small_download_chunks_do_not_flood_progress_events(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installation = root / 'Seraphine'
            installation.mkdir()
            (installation / 'Seraphine.exe').touch()
            package = root / 'new.zip'
            make_package(package)
            data = package.read_bytes()
            info = release_update(release(), '1.1.4+free.7', REPO)
            info['asset'].update(size=len(data), digest='sha256:' + hashlib.sha256(data).hexdigest())
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.iter_content.return_value = (data[i:i+1] for i in range(len(data)))
            progress = Mock()
            with patch('app.common.update.requests.get', return_value=response):
                prepare_update(info, installation, progress=progress)
            response.iter_content.assert_called_once_with(64 * 1024)
            self.assertLessEqual(progress.call_count, 101)
            self.assertEqual(progress.call_args.args, (len(data), len(data)))

    def test_legacy_update_setting_migrates_once(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'SeraphinePlus'
            profile.mkdir()
            config = profile / 'config.json'
            config.write_text(json.dumps({'General': {'EnableCheckUpdate': False}}))
            env = dict(os.environ, APPDATA=folder, QT_QPA_PLATFORM='offscreen')
            code = ('from app.common.config import cfg; assert cfg.get(cfg.enableCheckUpdate); '
                    'cfg.set(cfg.enableCheckUpdate, False)')
            subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, check=True, capture_output=True)
            self.assertEqual(json.loads(config.read_text())['General']['UpdateSetupVersion'], 1)
            subprocess.run([sys.executable, '-c', 'from app.common.config import cfg'],
                           cwd=ROOT, env=env, check=True, capture_output=True)
            self.assertFalse(json.loads(config.read_text())['General']['EnableCheckUpdate'])


@unittest.skipUnless(sys.platform == 'win32', 'Windows updater')
class WindowsUpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binary_temp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.binary_temp.name) / 'Seraphine.exe'
        compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
        if not compiler.exists():
            cls.binary_temp.cleanup()
            raise unittest.SkipTest('C# compiler unavailable')
        source = cls.binary.with_suffix('.cs')
        source.write_text('using System.IO; class Program { static int Main() { '
                          'File.WriteAllText("started.txt", "ok"); '
                          'return File.Exists("fail-start") ? 12 : 0; } }')
        subprocess.run([str(compiler), '/nologo', '/target:winexe', '/out:' + str(cls.binary), str(source)],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.binary_temp.cleanup()

    def run_update(self, root, target, version, fail=False):
        work = Path(tempfile.mkdtemp(prefix=f'.{target.name}-update-', dir=root))
        stage = work / 'Seraphine'
        stage.mkdir()
        shutil.copyfile(self.binary, stage / 'Seraphine.exe')
        (stage / 'version.txt').write_text(version)
        if fail:
            (stage / 'fail-start').touch()
        (work / 'plan.json').write_text(json.dumps({'target': str(target), 'parent_pid': 2147483647}))
        shutil.copyfile(ROOT / 'app/resource/updater.ps1', work / 'updater.ps1')
        process = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                  'Bypass', '-File', str(work / 'updater.ps1')], cwd=work,
                                 capture_output=True, timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
        return work, process

    def test_helper_waits_for_parent_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / 'Seraphine'
            target.mkdir()
            shutil.copyfile(self.binary, target / 'Seraphine.exe')
            (target / 'old.txt').touch()
            work = Path(tempfile.mkdtemp(prefix='.Seraphine-update-', dir=root))
            stage = work / 'Seraphine'
            stage.mkdir()
            shutil.copyfile(self.binary, stage / 'Seraphine.exe')
            code = ('from app.common.update import runUpdater; import sys; '
                    'runUpdater(sys.argv[1], sys.argv[2]); print("ready", flush=True); input()')
            parent = subprocess.Popen([sys.executable, '-c', code, str(work), str(target)],
                                      cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 20
                while not (work / 'ready').exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue((work / 'ready').exists())
                self.assertTrue((target / 'old.txt').exists())
                output, error = parent.communicate('\n', timeout=10)
                self.assertEqual(parent.returncode, 0, error)
                self.assertIn('ready', output)
                while not (work / 'result.txt').exists() and not (work / 'error.txt').exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue((work / 'result.txt').exists())
                self.assertFalse((target / 'old.txt').exists())
                self.assertTrue((root / '.Seraphine-previous/old.txt').exists())
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.communicate()

    def test_repeat_update_and_start_failure_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / '萨勒芬妮 [测试]'
            target.mkdir()
            shutil.copyfile(self.binary, target / 'Seraphine.exe')
            (target / 'version.txt').write_text('old')
            for version in ('new1', 'new2'):
                work, process = self.run_update(root, target, version)
                error = (work / 'error.txt').read_text(encoding='utf-8-sig') if (work / 'error.txt').exists() else ''
                self.assertEqual(process.returncode, 0, error + repr(process.stderr))
                self.assertEqual((target / 'version.txt').read_text(), version)
                self.assertTrue((target / 'started.txt').exists())
            backup = root / f'.{target.name}-previous'
            self.assertEqual((backup / 'version.txt').read_text(), 'new1')
            work, process = self.run_update(root, target, 'broken', fail=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual((target / 'version.txt').read_text(), 'new2')
            self.assertEqual((backup / 'version.txt').read_text(), 'new1')
            self.assertTrue((work / 'error.txt').exists())


class UpdateDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from app.common.qt_runtime import configure_qt_plugins
        configure_qt_plugins()
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_failed_download_allows_retry_and_only_success_exits(self):
        from PyQt5.QtWidgets import QWidget
        from app.components.message_box import UpdateMessageBox
        info = release_update(release(), '1.1.4+free.7', REPO)
        parent = QWidget()
        with patch.object(sys, 'frozen', True, create=True):
            dialog = UpdateMessageBox(info, parent)

        async def update():
            await dialog._UpdateMessageBox__onYesButtonClicked()

        with patch('app.components.message_box.prepare_update', side_effect=OSError('offline')), \
                patch('app.components.message_box.runUpdater') as run, \
                patch('app.components.message_box.QApplication') as application:
            asyncio.run(update())
            self.assertFalse(dialog._updating)
            self.assertTrue(dialog.myYesButton.isEnabled())
            self.assertTrue(dialog.myCancelButton.isEnabled())
            self.assertTrue(dialog.manuallyButton.isEnabled())
            run.assert_not_called()
            application.instance.return_value.quit.assert_not_called()
        with patch('app.components.message_box.prepare_update', return_value=Path('stage')), \
                patch('app.components.message_box.runUpdater') as run, \
                patch('app.components.message_box.QApplication') as application, \
                patch('app.components.message_box.signalBus') as signals:
            asyncio.run(update())
            run.assert_called_once()
            signals.terminateListeners.emit.assert_called_once()
            application.instance.return_value.quit.assert_called_once()
        dialog.deleteLater()
        parent.deleteLater()


if __name__ == '__main__':
    unittest.main()
