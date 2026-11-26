"""Ensure Windows Pillow RAQM/FriBiDi support is installed from official MSYS2 binaries and persists in .venv."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import site
import sys
import tarfile
from typing import Optional
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
MSYS2_UCRT64_URL = 'https://repo.msys2.org/mingw/ucrt64/'
FRIBIDI_URL_DEFAULT = 'https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-fribidi-1.0.17-1-any.pkg.tar.zst'
FRIBIDI_SHA256_DEFAULT = '3581ee03cf83a9bb0e98bf688c4f532a29553500e317532d90746de295b0f8a8'


def _write_venv_pth() -> Optional[Path]:
    """Install a .pth hook inside active site-packages so every Python command finds .venv/Scripts FriBiDi."""
    if os.name != 'nt':
        return None
    candidates = [Path(p) for p in site.getsitepackages() if Path(p).is_dir()]
    target_dir = next((p for p in candidates if 'site-packages' in p.name.lower()), candidates[0] if candidates else None)
    if target_dir is None:
        return None
    pth_file = target_dir / 'prodxmart_fribidi.pth'
    pth_code = (
        "import os, sys; "
        "d = os.path.dirname(sys.executable); "
        "os.path.isdir(d) and (os.add_dll_directory(d) if hasattr(os, 'add_dll_directory') else None, "
        "os.environ.__setitem__('PATH', d + os.pathsep + os.environ.get('PATH', '')) "
        "if d.lower() not in os.environ.get('PATH', '').lower() else None)\n"
    )
    pth_file.write_text(pth_code, encoding='utf-8')
    return pth_file


def _resolve_fribidi_package_url() -> tuple[str, str]:
    try:
        html = urllib.request.urlopen(MSYS2_UCRT64_URL, timeout=15).read().decode('utf-8', 'ignore')
        matches = sorted(set(re.findall(r'mingw-w64-ucrt-x86_64-fribidi-[0-9A-Za-z._-]+\.pkg\.tar\.zst', html)))
        if matches:
            latest = matches[-1]
            url = MSYS2_UCRT64_URL + latest
            expected = FRIBIDI_SHA256_DEFAULT if url == FRIBIDI_URL_DEFAULT else ''
            return url, expected
    except Exception:
        pass
    return FRIBIDI_URL_DEFAULT, FRIBIDI_SHA256_DEFAULT


def _install_from_msys2() -> Path:
    import compression.zstd as zstd

    url, expected_sha = _resolve_fribidi_package_url()
    print(f'Downloading official MSYS2 UCRT64 FriBiDi package: {url}')
    raw = urllib.request.urlopen(url, timeout=30).read()
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha and digest != expected_sha:
        raise RuntimeError(f'SHA-256 verification failed for {url}: got {digest}, expected {expected_sha}')

    tar_bytes = zstd.decompress(raw)
    dll_bytes = None
    copying_bytes = None
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tf:
        for member in tf.getmembers():
            if member.name.endswith('bin/libfribidi-0.dll'):
                extracted = tf.extractfile(member)
                dll_bytes = extracted.read() if extracted else None
            elif member.name.endswith('licenses/fribidi/COPYING'):
                extracted = tf.extractfile(member)
                copying_bytes = extracted.read() if extracted else None
    if not dll_bytes:
        raise RuntimeError('libfribidi-0.dll was not found inside the downloaded MSYS2 package.')

    scripts_dir = Path(sys.executable).parent
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dll_path = scripts_dir / 'libfribidi-0.dll'
    dll_path.write_bytes(dll_bytes)
    if copying_bytes:
        (scripts_dir / 'fribidi-COPYING.txt').write_bytes(copying_bytes)
    return dll_path


def main() -> int:
    parser = argparse.ArgumentParser(description='Verify or install Windows FriBiDi support for Pillow RAQM.')
    parser.add_argument('--check-only', action='store_true', help='Only report status without downloading FriBiDi.')
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from agents.telugu_support import configure_windows_dll_search_path, get_telugu_rendering_status

    _write_venv_pth()
    configure_windows_dll_search_path()
    status = get_telugu_rendering_status()
    if status['ready']:
        print(
            f"Telugu rendering ready (Pillow {status['pillow_version']}, "
            f"RAQM={status['raqm']}, HarfBuzz={status['harfbuzz']}, FriBiDi={status['fribidi']})"
        )
        return 0

    if args.check_only or os.name != 'nt':
        print('Telugu rendering NOT ready:', '; '.join(status['failures']))
        return 1

    if not status['fribidi'] or not status['raqm']:
        dll_path = _install_from_msys2()
        print(f'Installed trusted MSYS2 FriBiDi DLL to {dll_path}')
        _write_venv_pth()
        configure_windows_dll_search_path()
        status = get_telugu_rendering_status()

    if not status['ready']:
        print('ERROR: Telugu rendering setup incomplete:', '; '.join(status['failures']))
        return 1

    print(
        f"Telugu rendering verified after setup (Pillow {status['pillow_version']}, "
        f"RAQM={status['raqm']}, HarfBuzz={status['harfbuzz']}, FriBiDi={status['fribidi']})"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
