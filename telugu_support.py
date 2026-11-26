"""Windows RAQM/FriBiDi DLL configuration, Telugu Unicode shaping checks, akshara clustering, and mixed-script runs."""

from __future__ import annotations

import ctypes
from functools import lru_cache
import importlib.util
import os
from pathlib import Path
import re
import sys
from typing import Optional

ROOT = Path(__file__).resolve().parent
_TELUGU_CHAR_RE = re.compile(r'[\u0c00-\u0c7f]')
_TELUGU_CLUSTER_RE = re.compile(
    r'[\u0c05-\u0c39\u0c58-\u0c61](?:\u0c4d[\u200c\u200d]?[\u0c15-\u0c39\u0c58-\u0c5a])*'
    r'[\u0c3e-\u0c4d\u0c55-\u0c56\u0c62-\u0c63\u0c00-\u0c04\u0c3c\u200c\u200d]*'
    r'|[^\u0c00-\u0c7f][\u0300-\u036f]*'
    r'|.',
    re.S,
)
_DLL_DIR_COOKIES: list[object] = []
_CONFIGURED_DIRS: set[str] = set()
_PRELOADED_DLL: Optional[Path] = None

_FRIBIDI_NAMES = ('libfribidi-0.dll', 'fribidi-0.dll', 'fribidi.dll')


class TeluguShapingError(RuntimeError):
    """Raised when Telugu text shaping is requested without RAQM/HarfBuzz/FriBiDi support."""


def _candidate_dll_dirs() -> list[Path]:
    dirs: list[Path] = []
    for candidate in (
        Path(sys.executable).parent,
        Path(sys.prefix) / 'Scripts',
        ROOT / '.venv' / 'Scripts',
        ROOT / 'assets' / 'bin',
        Path(r'C:\msys64\ucrt64\bin'),
        Path(r'C:\msys64\mingw64\bin'),
    ):
        try:
            resolved = str(candidate)
        except Exception:
            continue
        if candidate.is_dir() and resolved not in {str(d) for d in dirs}:
            dirs.append(candidate)
    return dirs


def find_fribidi_dll() -> Optional[Path]:
    """Locate a compatible FriBiDi DLL on Windows if installed."""
    if os.name != 'nt':
        return None
    for directory in _candidate_dll_dirs():
        for name in _FRIBIDI_NAMES:
            dll = directory / name
            if dll.is_file():
                return dll
    sys_root = os.environ.get('SystemRoot')
    if sys_root:
        for name in _FRIBIDI_NAMES:
            dll = Path(sys_root) / 'System32' / name
            if dll.is_file():
                return dll
    return None


def configure_windows_dll_search_path() -> list[str]:
    """Ensure Windows DLL search paths and process PATH include FriBiDi before Pillow loads _imagingft."""
    global _PRELOADED_DLL
    if os.name != 'nt':
        return []

    added: list[str] = []
    current_path = os.environ.get('PATH', '')
    path_parts = [p for p in current_path.split(os.pathsep) if p]
    path_norm = {os.path.normcase(os.path.normpath(p)) for p in path_parts}

    for directory in _candidate_dll_dirs():
        raw = str(directory)
        norm = os.path.normcase(os.path.normpath(raw))
        if norm not in _CONFIGURED_DIRS:
            _CONFIGURED_DIRS.add(norm)
            if hasattr(os, 'add_dll_directory'):
                try:
                    _DLL_DIR_COOKIES.append(os.add_dll_directory(raw))
                except OSError:
                    pass
        if norm not in path_norm:
            path_parts.insert(0, raw)
            path_norm.add(norm)
        added.append(raw)

    if added:
        os.environ['PATH'] = os.pathsep.join(path_parts)

    if _PRELOADED_DLL is None:
        dll = find_fribidi_dll()
        if dll is not None:
            try:
                ctypes.WinDLL(str(dll))
                _PRELOADED_DLL = dll
            except OSError:
                pass
    return added


# Configure immediately on module import so any subsequent Pillow import detects FriBiDi.
configure_windows_dll_search_path()


def is_raqm_ready() -> bool:
    """Return True only when Pillow RAQM, HarfBuzz, and FriBiDi are all operational."""
    configure_windows_dll_search_path()
    from PIL import features

    return bool(
        features.check('raqm')
        and features.check('harfbuzz')
        and features.check('fribidi')
    )


def ensure_telugu_raqm_ready() -> None:
    """Fail explicitly instead of silently degrading Telugu shaping to Layout.BASIC."""
    if is_raqm_ready():
        return
    from PIL import features

    raise TeluguShapingError(
        'Telugu text rendering requires Pillow RAQM, HarfBuzz, and FriBiDi support '
        f'(detected raqm={features.check("raqm")}, harfbuzz={features.check("harfbuzz")}, '
        f'fribidi={features.check("fribidi")}). '
        'Refusing to fall back to Layout.BASIC because vowel signs and conjuncts would render broken. '
        'Run Start_Windows.bat or `.venv\\Scripts\\python.exe scripts\\setup_telugu_windows.py` to install FriBiDi.'
    )


def contains_telugu(text: str) -> bool:
    return bool(_TELUGU_CHAR_RE.search(text or ''))


def split_text_clusters(text: str) -> list[str]:
    """Split text into orthographic clusters so Telugu conjuncts and vowel signs are never split."""
    if not text:
        return []
    return _TELUGU_CLUSTER_RE.findall(text)


@lru_cache(maxsize=512)
def _te_font_has_glyph(font_path: str, ch: str) -> bool:
    from PIL import ImageFont

    layout = ImageFont.Layout.RAQM if is_raqm_ready() else ImageFont.Layout.BASIC
    font_obj = ImageFont.truetype(font_path, 48, layout_engine=layout)
    notdef = bytes(font_obj.getmask('\ufffe'))
    return bytes(font_obj.getmask(ch)) != notdef


def segment_script_runs(text: str, telugu_font_path: Optional[Path] = None) -> list[tuple[str, bool]]:
    """Split mixed strings into contiguous (segment, is_telugu) runs aligned cleanly across scripts."""
    if not text:
        return []
    if not contains_telugu(text):
        return [(text, False)]

    font_str = str(telugu_font_path or (ROOT / 'assets' / 'NotoSansTelugu-Regular.ttf'))
    classes: list[str] = []
    n = len(text)

    for idx, ch in enumerate(text):
        code = ord(ch)
        if 0x0C00 <= code <= 0x0C7F:
            classes.append('te')
        elif ch in ('\u200c', '\u200d'):
            prev_is_te = idx > 0 and classes[idx - 1] == 'te'
            next_is_te = idx + 1 < n and (0x0C00 <= ord(text[idx + 1]) <= 0x0C7F)
            classes.append('te' if (prev_is_te or next_is_te) else 'en')
        elif ch == ' ':
            classes.append('space')
        elif ch in ('₹', '·') or ('A' <= ch <= 'Z') or ('a' <= ch <= 'z') or ('0' <= ch <= '9'):
            classes.append('en')
        elif ch in ('.', ',', '!', '?', ':', ';', '-', '…', '(', ')'):
            prev_cls = next((c for c in reversed(classes) if c != 'space'), None)
            if prev_cls == 'te' and _te_font_has_glyph(font_str, ch):
                classes.append('te')
            else:
                classes.append('en')
        else:
            classes.append('te' if _te_font_has_glyph(font_str, ch) else 'en')

    resolved = list(classes)
    last_script = next((c for c in resolved if c != 'space'), 'te')
    for idx, cls in enumerate(resolved):
        if cls == 'space':
            next_script = next((resolved[j] for j in range(idx + 1, n) if resolved[j] != 'space'), last_script)
            resolved[idx] = 'en' if ('en' in (last_script, next_script) and last_script != 'te') else last_script
        else:
            last_script = cls

    runs: list[tuple[str, bool]] = []
    cur_chars: list[str] = []
    cur_te = resolved[0] == 'te'
    for ch, cls in zip(text, resolved):
        is_te = cls == 'te'
        if is_te == cur_te:
            cur_chars.append(ch)
        else:
            runs.append((''.join(cur_chars), cur_te))
            cur_chars = [ch]
            cur_te = is_te
    if cur_chars:
        runs.append((''.join(cur_chars), cur_te))
    return runs


def get_telugu_rendering_status() -> dict:
    """Inspect active Python runtime, Pillow RAQM/HarfBuzz/FriBiDi, fonts, and Playwright availability."""
    import PIL
    from PIL import ImageFont, features

    configure_windows_dll_search_path()
    raqm = bool(features.check('raqm'))
    harfbuzz = bool(features.check('harfbuzz'))
    fribidi = bool(features.check('fribidi'))
    te_font_path = ROOT / 'assets' / 'NotoSansTelugu-Regular.ttf'
    en_reg_path = ROOT / 'assets' / 'NotoSans-Regular.ttf'
    en_bold_path = ROOT / 'assets' / 'NotoSans-Bold.ttf'

    te_font_ok = te_font_path.is_file()
    en_fonts_ok = en_reg_path.is_file() and en_bold_path.is_file()
    font_family = None
    glyphs_ok = False
    font_error = ''

    if te_font_ok:
        try:
            layout = ImageFont.Layout.RAQM if raqm else ImageFont.Layout.BASIC
            f = ImageFont.truetype(str(te_font_path), 48, layout_engine=layout)
            font_family = ' '.join(f.getname())
            notdef = bytes(f.getmask('\ufffe'))
            glyphs_ok = all(bytes(f.getmask(ch)) != notdef for ch in ('మ', 'రి', '్', 'న', 'చ', 'ూ'))
        except Exception as exc:
            te_font_ok = False
            font_error = str(exc)
    else:
        font_error = f'Font file missing: {te_font_path}'

    fribidi_dll = find_fribidi_dll()
    playwright_available = importlib.util.find_spec('playwright') is not None
    ready = bool(raqm and harfbuzz and fribidi and te_font_ok and glyphs_ok and en_fonts_ok)

    failures: list[str] = []
    if not raqm:
        failures.append('Pillow RAQM support is disabled (raqm=False).')
    if not harfbuzz:
        failures.append('HarfBuzz shaping is disabled (harfbuzz=False).')
    if not fribidi:
        failures.append('FriBiDi DLL not loaded (fribidi=False).')
    if not (te_font_ok and glyphs_ok):
        failures.append(font_error or 'Noto Sans Telugu font failed validation.')
    if not en_fonts_ok:
        failures.append('Noto Sans English fallback fonts are missing in assets/.')

    return {
        'ready': ready,
        'status': 'ready' if ready else 'missing',
        'pillow_version': getattr(PIL, '__version__', 'unknown'),
        'raqm': raqm,
        'harfbuzz': harfbuzz,
        'fribidi': fribidi,
        'fribidi_dll_path': str(fribidi_dll) if fribidi_dll else '',
        'font_name': font_family or 'Noto Sans Telugu Regular',
        'font_path': str(te_font_path),
        'font_ok': bool(te_font_ok and glyphs_ok),
        'latin_fonts_ok': en_fonts_ok,
        'playwright_available': playwright_available,
        'setup_command': '.venv\\Scripts\\python.exe scripts\\setup_telugu_windows.py',
        'failures': failures,
    }
