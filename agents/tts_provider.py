"""Pluggable voiceover providers (recorded audio and approved TTS) with FFmpeg alignment."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Optional
from urllib.request import Request, urlopen
import wave

import creative


class VoiceoverProvider(ABC):
    """Interface for approved text-to-speech or recorded narration providers."""

    provider_id: str

    @abstractmethod
    def supports_language(self, language: str) -> bool:
        """Return True if this provider can produce intelligible narration for language."""

    @abstractmethod
    def synthesize_to_wav(self, text: str, *, language: str, out_wav: Path) -> bool:
        """Write narration WAV for text; return True if usable non-silent audio was written."""


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), 'rb') as wf:
        rate = wf.getframerate() or 48000
        return wf.getnframes() / float(rate)


def _normalize_to_48k_mono_wav(src: Path, dest: Path, *, max_seconds: Optional[float] = None) -> float:
    """Convert any audio file to 48kHz mono PCM WAV and optionally fit inside max_seconds."""
    ff = creative.ffmpeg()
    tmp = dest.with_suffix('.raw.wav')
    subprocess.run(
        [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(src), '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(tmp)],
        check=True,
        timeout=60,
    )
    dur = _wav_duration(tmp)
    if max_seconds and dur > max_seconds > 0.2:
        ratio = min(1.5, dur / max_seconds)
        if ratio > 1.02:
            subprocess.run(
                [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp), '-af', f'atempo={ratio:.3f}', '-t', f'{max_seconds:.3f}', str(dest)],
                check=True,
                timeout=60,
            )
            tmp.unlink(missing_ok=True)
            return _wav_duration(dest)
        subprocess.run(
            [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(tmp), '-t', f'{max_seconds:.3f}', str(dest)],
            check=True,
            timeout=60,
        )
        tmp.unlink(missing_ok=True)
        return _wav_duration(dest)
    shutil.move(str(tmp), str(dest))
    return dur


class RecordedAudioProvider(VoiceoverProvider):
    """Uses an approved human-recorded narration file."""

    provider_id = 'recorded_audio'

    def __init__(self, audio_path: Path):
        self.audio_path = Path(audio_path)

    def supports_language(self, language: str) -> bool:
        return self.audio_path.is_file()

    def synthesize_to_wav(self, text: str, *, language: str, out_wav: Path) -> bool:
        if not self.audio_path.is_file():
            return False
        _normalize_to_48k_mono_wav(self.audio_path, out_wav)
        return out_wav.is_file() and out_wav.stat().st_size > 44


class WindowsSapiTtsProvider(VoiceoverProvider):
    """Approved Windows SAPI offline TTS provider when desktop speech voices are available."""

    provider_id = 'windows_sapi'

    def __init__(self):
        self._voices: Optional[list[str]] = None

    def _discover_voices(self) -> list[str]:
        if self._voices is not None:
            return self._voices
        if os.name != 'nt':
            self._voices = []
            return self._voices
        try:
            ps = (
                "$v = New-Object -ComObject SAPI.SpVoice; "
                "$v.GetVoices() | ForEach-Object { $_.GetDescription() }"
            )
            res = subprocess.run(
                ['powershell', '-NoProfile', '-Command', ps],
                capture_output=True,
                text=True,
                timeout=6,
            )
            if res.returncode == 0 and res.stdout.strip():
                self._voices = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            else:
                self._voices = []
        except Exception:
            self._voices = []
        return self._voices

    def supports_language(self, language: str) -> bool:
        voices = self._discover_voices()
        if not voices:
            return False
        if language == 'te':
            return any('telugu' in v.lower() or 'te-in' in v.lower() for v in voices)
        return len(voices) > 0

    def synthesize_to_wav(self, text: str, *, language: str, out_wav: Path) -> bool:
        if not self.supports_language(language) or not text.strip():
            return False
        escaped = text.replace("'", "''")
        ps = (
            "$s = New-Object -ComObject SAPI.SpVoice; "
            "$f = New-Object -ComObject SAPI.SpFileStream; "
            f"$f.Open('{str(out_wav).replace(chr(39), chr(39)*2)}', 3); "
            "$s.AudioOutputStream = $f; "
            f"$s.Speak('{escaped}') | Out-Null; "
            "$f.Close()"
        )
        res = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=25)
        return res.returncode == 0 and out_wav.is_file() and out_wav.stat().st_size > 44


class HttpTtsProvider(VoiceoverProvider):
    """Approved local/self-hosted HTTP TTS endpoint returning WAV audio."""

    provider_id = 'http_tts'

    def __init__(self, endpoint_url: str, languages: tuple[str, ...] = ('en', 'te', 'bilingual')):
        self.endpoint_url = (endpoint_url or '').strip()
        self.languages = languages

    def supports_language(self, language: str) -> bool:
        return bool(self.endpoint_url) and language in self.languages

    def synthesize_to_wav(self, text: str, *, language: str, out_wav: Path) -> bool:
        if not self.supports_language(language) or not text.strip():
            return False
        req = Request(
            self.endpoint_url,
            data=json.dumps({'text': text, 'language': language}, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Accept': 'audio/wav'},
            method='POST',
        )
        with urlopen(req, timeout=30) as res:
            raw = res.read()
        if len(raw) <= 44:
            return False
        raw_path = out_wav.with_suffix('.http.wav')
        raw_path.write_bytes(raw)
        _normalize_to_48k_mono_wav(raw_path, out_wav)
        raw_path.unlink(missing_ok=True)
        return out_wav.is_file() and out_wav.stat().st_size > 44


def build_narration_track(
    *,
    scenes: list[dict[str, Any]],
    total_duration: int,
    language: str,
    out_wav: Path,
    provider: Optional[VoiceoverProvider] = None,
    recorded_audio_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Assemble a full-duration narration WAV synchronized with scene start times."""
    ff = creative.ffmpeg()

    if recorded_audio_path is not None:
        rec = RecordedAudioProvider(recorded_audio_path)
        if not rec.supports_language(language):
            raise ValueError(f'Recorded audio file not found: {recorded_audio_path}')
        with tempfile.TemporaryDirectory() as tmp:
            norm = Path(tmp) / 'recorded_norm.wav'
            dur = _normalize_to_48k_mono_wav(recorded_audio_path, norm, max_seconds=float(total_duration))
            # Pad to exact total_duration so FFmpeg sidechaincompress stays synchronized.
            subprocess.run(
                [
                    ff, '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(norm),
                    '-af', f'apad=whole_dur={total_duration}',
                    '-t', str(total_duration),
                    '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(out_wav),
                ],
                check=True,
                timeout=60,
            )
        return {
            'status': 'recorded_audio',
            'provider': rec.provider_id,
            'speech_segments': [[0.0, round(min(total_duration, dur), 2)]],
        }

    chosen = provider or WindowsSapiTtsProvider()
    if not chosen.supports_language(language):
        return {
            'status': 'unavailable_music_only',
            'provider': None,
            'speech_segments': [],
        }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        clips: list[tuple[float, Path, float]] = []
        cursor = 0.0
        for idx, sc in enumerate(scenes):
            dur = float(sc['duration_seconds'])
            vo_text = str(sc.get('voiceover') or '').strip()
            if vo_text:
                raw_wav = tmp_dir / f'raw_{idx:02d}.wav'
                norm_wav = tmp_dir / f'norm_{idx:02d}.wav'
                if chosen.synthesize_to_wav(vo_text, language=language, out_wav=raw_wav):
                    clip_dur = _normalize_to_48k_mono_wav(raw_wav, norm_wav, max_seconds=max(0.8, dur - 0.2))
                    clips.append((cursor + 0.1, norm_wav, clip_dur))
            cursor += dur

        if not clips:
            return {
                'status': 'unavailable_music_only',
                'provider': None,
                'speech_segments': [],
            }

        cmd = [
            ff, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-t', str(total_duration), '-i', 'anullsrc=channel_layout=mono:sample_rate=48000',
        ]
        filter_parts = []
        mix_labels = ['[0:a]']
        for idx, (start_s, clip_path, _) in enumerate(clips, 1):
            cmd.extend(['-i', str(clip_path)])
            delay_ms = max(0, int(round(start_s * 1000)))
            lbl = f'[vo{idx}]'
            filter_parts.append(f'[{idx}:a]adelay={delay_ms}|{delay_ms}{lbl}')
            mix_labels.append(lbl)

        filter_parts.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0[out]")
        cmd.extend([
            '-filter_complex', ';'.join(filter_parts),
            '-map', '[out]',
            '-t', str(total_duration),
            '-ac', '1', '-ar', '48000', '-c:a', 'pcm_s16le', str(out_wav),
        ])
        subprocess.run(cmd, check=True, timeout=90)

        return {
            'status': 'tts_generated',
            'provider': chosen.provider_id,
            'speech_segments': [[round(s, 2), round(min(total_duration, s + d), 2)] for s, _, d in clips],
        }
