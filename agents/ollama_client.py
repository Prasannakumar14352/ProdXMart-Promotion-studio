"""Local Ollama REST API client with cancellation-aware streaming."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import socket
import threading
import time
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from agents.base import AgentCancelledError

DEFAULT_OLLAMA_URL = 'http://localhost:11434'
DEFAULT_MODEL = 'qwen2.5:3b'


class OllamaError(RuntimeError):
    """Raised when Ollama cannot be reached or returns an error."""

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: float = 120.0


def normalize_ollama_url(raw_url: str) -> str:
    """Validate and normalize the configured Ollama base URL."""
    text = (raw_url or '').strip()
    if not text:
        raise ValueError('Ollama base URL cannot be empty.')
    parsed = urlsplit(text)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Enter a valid HTTP or HTTPS Ollama URL (for example http://localhost:11434).')
    clean_path = parsed.path.rstrip('/')
    return urlunsplit((parsed.scheme, parsed.netloc, clean_path, '', ''))


def _extract_error_detail(raw_body: bytes) -> str:
    if not raw_body:
        return ''
    text = raw_body.decode('utf-8', errors='replace').strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and parsed.get('error'):
            return str(parsed['error'])
    except ValueError:
        pass
    return text[:240]


def _model_matches(installed: list[str], target: str) -> bool:
    if not target:
        return False
    if target in installed:
        return True
    if ':' not in target and f'{target}:latest' in installed:
        return True
    return False


class OllamaClient:
    """Thin stdlib HTTP client for the local Ollama REST API."""

    def __init__(self, config: Optional[OllamaConfig] = None):
        cfg = config or OllamaConfig()
        self.base_url = normalize_ollama_url(cfg.base_url)
        self.model = (cfg.model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.timeout_seconds = max(5.0, float(cfg.timeout_seconds))

    def test_connection(self, model: Optional[str] = None, timeout: float = 5.0) -> dict[str, Any]:
        target_model = (model or self.model).strip()
        started = time.monotonic()
        url = f'{self.base_url}/api/tags'
        req = Request(url, method='GET', headers={'Accept': 'application/json'})
        try:
            with urlopen(req, timeout=max(1.0, float(timeout))) as res:
                body = json.loads(res.read().decode('utf-8', errors='replace'))
            if not isinstance(body, dict) or not isinstance(body.get('models'), list):
                raise ValueError('Invalid Ollama /api/tags response: expected a models list.')
            models = [
                str(item.get('name', '')).strip()
                for item in (body.get('models') or [])
                if isinstance(item, dict) and item.get('name')
            ]
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            available = _model_matches(models, target_model)
            return {
                'ok': True,
                'base_url': self.base_url,
                'configured_model': target_model,
                'model_available': available,
                'installed_models': models,
                'latency_ms': latency_ms,
                'error': None if available else f'Connected to Ollama, but model "{target_model}" is not installed. Run: ollama pull {target_model}',
            }
        except HTTPError as exc:
            detail = _extract_error_detail(exc.read())
            return {
                'ok': False,
                'base_url': self.base_url,
                'configured_model': target_model,
                'model_available': False,
                'installed_models': [],
                'latency_ms': round((time.monotonic() - started) * 1000, 1),
                'error': f'Ollama returned HTTP {exc.code}: {detail}' if detail else f'Ollama returned HTTP {exc.code}.',
            }
        except (URLError, OSError, TimeoutError, ValueError, http.client.HTTPException) as exc:
            reason = getattr(exc, 'reason', exc)
            return {
                'ok': False,
                'base_url': self.base_url,
                'configured_model': target_model,
                'model_available': False,
                'installed_models': [],
                'latency_ms': round((time.monotonic() - started) * 1000, 1),
                'error': f'Could not connect to Ollama at {self.base_url}: {reason}',
            }

    def generate(
        self,
        prompt: str,
        *,
        system: str = '',
        model: Optional[str] = None,
        temperature: float = 0.2,
        cancel_event: Optional[threading.Event] = None,
        format_json: bool = False,
    ) -> dict[str, Any]:
        """Call Ollama /api/generate with streaming so Stop cancels promptly."""
        if cancel_event and cancel_event.is_set():
            raise AgentCancelledError('Execution stopped before calling Ollama.')

        target_model = (model or self.model).strip()
        if not target_model:
            raise OllamaError('No Ollama model configured.', retryable=False)

        payload: dict[str, Any] = {
            'model': target_model,
            'prompt': prompt,
            'stream': True,
            'options': {'temperature': temperature},
        }
        if system:
            payload['system'] = system
        if format_json:
            payload['format'] = 'json'

        parsed = urlsplit(self.base_url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == 'https' else http.client.HTTPConnection
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path_prefix = parsed.path.rstrip('/')
        endpoint = f'{path_prefix}/api/generate'

        # Cold model startup can exceed 1.5 seconds. Buffered streams cannot resume
        # reliably after a read timeout, so use the configured socket timeout.
        conn = conn_cls(parsed.hostname, port, timeout=self.timeout_seconds)
        deadline = time.monotonic() + self.timeout_seconds
        chunks: list[str] = []
        last_meta: dict[str, Any] = {}

        try:
            body_bytes = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            conn.request(
                'POST',
                endpoint,
                body=body_bytes,
                headers={'Content-Type': 'application/json', 'Accept': 'application/x-ndjson'},
            )
            res = conn.getresponse()
            if res.status != 200:
                detail = _extract_error_detail(res.read())
                retryable = res.status >= 500 or res.status == 429
                msg = f'Ollama returned HTTP {res.status} for model "{target_model}"'
                if detail:
                    msg += f': {detail}'
                raise OllamaError(msg, retryable=retryable)

            while True:
                if cancel_event and cancel_event.is_set():
                    raise AgentCancelledError('Execution stopped during Ollama generation.')
                if time.monotonic() > deadline:
                    raise OllamaError(
                        f'Ollama request timed out after {int(self.timeout_seconds)}s for model "{target_model}".',
                        retryable=True,
                    )
                try:
                    raw_line = res.readline()
                except socket.timeout as exc:
                    if cancel_event and cancel_event.is_set():
                        raise AgentCancelledError('Execution stopped during Ollama generation.') from exc
                    raise OllamaError(f'Ollama response timed out for model "{target_model}".', retryable=True) from exc
                if cancel_event and cancel_event.is_set():
                    raise AgentCancelledError('Execution stopped during Ollama generation.')
                if time.monotonic() > deadline:
                    raise OllamaError(
                        f'Ollama request timed out after {int(self.timeout_seconds)}s for model "{target_model}".',
                        retryable=True,
                    )
                if not raw_line:
                    break
                line = raw_line.decode('utf-8', errors='replace').strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise OllamaError(f'Invalid JSON chunk from Ollama: {exc}', retryable=True) from exc
                if not isinstance(event, dict):
                    raise OllamaError('Invalid JSON chunk from Ollama: expected an object.', retryable=True)
                if event.get('error'):
                    err_text = str(event['error'])
                    retryable = 'not found' not in err_text.lower()
                    raise OllamaError(f'Ollama error: {err_text}', retryable=retryable)
                piece = event.get('response')
                if isinstance(piece, str):
                    chunks.append(piece)
                if event.get('done') is True:
                    last_meta = event
                    break

            if cancel_event and cancel_event.is_set():
                raise AgentCancelledError('Execution stopped during Ollama generation.')

            if not last_meta.get('done'):
                raise OllamaError('Ollama stream ended before generation completed.', retryable=True)
            text = ''.join(chunks).strip()
            if not text:
                raise OllamaError(f'Ollama returned an empty response for model "{target_model}".', retryable=True)
            return {
                'model': last_meta.get('model', target_model),
                'response': text,
                'eval_count': last_meta.get('eval_count'),
                'total_duration': last_meta.get('total_duration'),
            }
        except AgentCancelledError:
            raise
        except OllamaError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            if cancel_event and cancel_event.is_set():
                raise AgentCancelledError('Execution stopped during Ollama generation.') from exc
            raise OllamaError(f'Could not communicate with Ollama at {self.base_url}: {exc}', retryable=True) from exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
