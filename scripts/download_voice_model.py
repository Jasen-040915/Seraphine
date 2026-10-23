"""Fetch the official SenseVoice int8 release and verify its published SHA256."""
import hashlib
from pathlib import Path
import tarfile
import time
import requests

ROOT = Path(__file__).resolve().parents[1]
NAME = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
URL = f'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{NAME}.tar.bz2'
SHA256 = '7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e'


def main():
    archive = ROOT / 'artifacts' / 'voice-model.tar.bz2'
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != SHA256:
        temp = archive.with_suffix('.download')
        digest, total, report = hashlib.sha256(), 0, time.monotonic()
        with requests.get(URL, stream=True, timeout=(20, 60)) as response:
            response.raise_for_status()
            with temp.open('wb') as output:
                for chunk in response.iter_content(1024 * 1024):
                    output.write(chunk); digest.update(chunk); total += len(chunk)
                    if time.monotonic() - report >= 5:
                        print(f'Downloaded {total / 1024 / 1024:.1f} MiB', flush=True)
                        report = time.monotonic()
        if digest.hexdigest() != SHA256:
            raise RuntimeError('Voice model checksum mismatch')
        temp.replace(archive)
    target = ROOT / 'models' / 'sensevoice'
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as bundle:
        for name in ['model.int8.onnx', 'tokens.txt', 'LICENSE', 'README.md', 'export-onnx.py', 'test_wavs/zh.wav']:
            source = bundle.extractfile(f'{NAME}/{name}')
            if source is None:
                raise RuntimeError(f'Missing {name}')
            dest = target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with source, dest.open('wb') as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    licenses = {
        'SenseVoice-LICENSE.txt': 'https://raw.githubusercontent.com/FunAudioLLM/SenseVoice/main/LICENSE',
        'FunASR-MODEL-LICENSE.txt': 'https://raw.githubusercontent.com/modelscope/FunASR/main/MODEL_LICENSE',
        'sherpa-onnx-LICENSE.txt': 'https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/LICENSE',
        'ONNXRuntime-LICENSE.txt': 'https://raw.githubusercontent.com/microsoft/onnxruntime/main/LICENSE',
    }
    for name, url in licenses.items():
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        (target / name).write_bytes(response.content)
    print(f'Verified model ready: {target}', flush=True)


if __name__ == '__main__':
    main()
