"""Secure credential storage for future Meta and YouTube API integration."""

from __future__ import annotations

import base64
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import os
from pathlib import Path
import secrets
from typing import Any, Optional

import engine

_KNOWN_KEYS = (
    'meta_ig_token',
    'meta_ig_id',
    'meta_fb_page_token',
    'meta_fb_page_id',
    'meta_graph_version',
    'youtube_oauth_token',
    'youtube_channel_id',
)


def _mask_secret(value: str) -> str:
    if not value:
        return ''
    if len(value) <= 4:
        return '•' * len(value)
    return '•' * min(12, len(value) - 4) + value[-4:]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(raw: bytes) -> Optional[bytes]:
    if os.name != 'nt':
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_blob = _DATA_BLOB(len(raw), ctypes.create_string_buffer(raw, len(raw)))
        out_blob = _DATA_BLOB()
        if not crypt32.CryptProtectData(ctypes.byref(in_blob), 'ProdXMartStudio', None, None, None, 0x01, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


def _dpapi_unprotect(enc: bytes) -> Optional[bytes]:
    if os.name != 'nt':
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_blob = _DATA_BLOB(len(enc), ctypes.create_string_buffer(enc, len(enc)))
        out_blob = _DATA_BLOB()
        if not crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None, None, 0x01, ctypes.byref(out_blob)):
            return None
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)
    except Exception:
        return None


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    counter = 0
    offset = 0
    while offset < len(data):
        block = hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
        take = min(len(block), len(data) - offset)
        for i in range(take):
            out[offset + i] = data[offset + i] ^ block[i]
        offset += take
        counter += 1
    return bytes(out)


class SecureCredentialStore:
    """Encrypts and masks API credentials in SQLite without ever returning plaintext in API state."""

    def __init__(self, *, use_dpapi: bool = True):
        self._use_dpapi = use_dpapi

    def _local_key(self) -> bytes:
        key_path = engine.DATA / '.keystore'
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.is_file():
            raw = key_path.read_bytes()
            if len(raw) >= 32:
                return raw[:32]
        key = secrets.token_bytes(32)
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key

    def _encrypt(self, plaintext: str) -> str:
        raw = plaintext.encode('utf-8')
        if self._use_dpapi:
            protected = _dpapi_protect(raw)
            if protected is not None:
                return 'dpapi:' + base64.b64encode(protected).decode('ascii')
        key = self._local_key()
        nonce = secrets.token_bytes(16)
        cipher = _xor_stream(raw, key, nonce)
        tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
        blob = nonce + tag + cipher
        return 'local:' + base64.b64encode(blob).decode('ascii')

    def _decrypt(self, token: str) -> str:
        if not token:
            return ''
        if token.startswith('dpapi:'):
            raw = base64.b64decode(token[6:].encode('ascii'))
            dec = _dpapi_unprotect(raw)
            if dec is None:
                raise ValueError('Could not decrypt DPAPI credential.')
            return dec.decode('utf-8')
        if token.startswith('local:'):
            blob = base64.b64decode(token[6:].encode('ascii'))
            if len(blob) < 48:
                raise ValueError('Corrupted encrypted credential.')
            nonce, tag, cipher = blob[:16], blob[16:48], blob[48:]
            key = self._local_key()
            expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
            if not hmac.compare_digest(tag, expected):
                raise ValueError('Credential integrity check failed.')
            return _xor_stream(cipher, key, nonce).decode('utf-8')
        raise ValueError('Unsupported credential encoding.')

    def _ensure_table(self, con) -> None:
        con.execute(
            '''CREATE TABLE IF NOT EXISTS social_credentials (
                cred_key TEXT PRIMARY KEY,
                encrypted_value TEXT NOT NULL,
                masked_preview TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )'''
        )

    def set_credential(self, cred_key: str, value: str) -> dict[str, Any]:
        key = (cred_key or '').strip()
        if not key or key not in _KNOWN_KEYS:
            raise ValueError(f'Unsupported credential key: {cred_key}')
        clean_val = (value or '').strip()
        stamp = engine.now().isoformat()
        with engine.LOCK, closing(engine.db()) as con, con:
            self._ensure_table(con)
            if not clean_val:
                con.execute('DELETE FROM social_credentials WHERE cred_key=?', (key,))
                return {'cred_key': key, 'configured': False, 'masked_preview': '', 'updated_at': stamp}
            enc = self._encrypt(clean_val)
            masked = _mask_secret(clean_val)
            con.execute(
                '''INSERT INTO social_credentials (cred_key, encrypted_value, masked_preview, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(cred_key) DO UPDATE SET
                       encrypted_value=excluded.encrypted_value,
                       masked_preview=excluded.masked_preview,
                       updated_at=excluded.updated_at''',
                (key, enc, masked, stamp),
            )
        return {'cred_key': key, 'configured': True, 'masked_preview': masked, 'updated_at': stamp}

    def get_secret(self, cred_key: str) -> str:
        with engine.LOCK, closing(engine.db()) as con:
            self._ensure_table(con)
            row = con.execute('SELECT encrypted_value FROM social_credentials WHERE cred_key=?', (cred_key,)).fetchone()
            if not row:
                return ''
            return self._decrypt(row['encrypted_value'])

    def list_status(self) -> dict[str, dict[str, Any]]:
        with engine.LOCK, closing(engine.db()) as con:
            self._ensure_table(con)
            rows = {
                r['cred_key']: {
                    'cred_key': r['cred_key'],
                    'configured': True,
                    'masked_preview': r['masked_preview'],
                    'updated_at': r['updated_at'],
                }
                for r in con.execute('SELECT cred_key, masked_preview, updated_at FROM social_credentials')
            }
        return {
            key: rows.get(key, {'cred_key': key, 'configured': False, 'masked_preview': '', 'updated_at': ''})
            for key in _KNOWN_KEYS
        }
