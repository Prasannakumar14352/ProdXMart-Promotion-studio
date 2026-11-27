"""Read-only R1 diagnostics. Does not launch Studio, queue jobs or publish."""
import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.ollama_client import OllamaClient, OllamaConfig, OllamaError

ROUTES = ('js/phase4_studio.js', 'js/phase5_publishing.js', 'js/phase6_analytics.js')
JS_MIME = {'text/javascript', 'application/javascript', 'application/ecmascript', 'text/ecmascript'}


def check_asset(port, path):
    local = ROOT / path
    if not local.is_file():
        return False, 'Missing checkout file'
    try:
        with urlopen(f'http://127.0.0.1:{port}/{path}', timeout=5) as response:
            same = hashlib.sha256(response.read()).digest() == hashlib.sha256(local.read_bytes()).digest()
            mime = response.headers.get_content_type()
            ok = response.status == 200 and mime in JS_MIME and same
            return ok, f'HTTP {response.status}; MIME {mime}; checkout bytes match={same}'
    except (OSError, ValueError) as exc:
        return False, str(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--model', required=True, help='Exact installed Ollama model from Studio settings')
    parser.add_argument('--ollama-url', default='http://localhost:11434')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--generate', action='store_true', help='Also run one short real Ollama generation')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 5 <= args.timeout <= 300:
        parser.error('Port must be 1–65535 and timeout must be 5–300 seconds.')
    failures = []
    def report(name, ok, detail):
        print(f'{"PASS" if ok else "FAIL"} {name}: {detail}')
        if not ok:
            failures.append(name)

    print(f'Checkout: {ROOT}')
    print(f'Python: {sys.version.split()[0]}')
    if shutil.which('git'):
        git = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
        print(f'Commit: {git.stdout.strip() if git.returncode == 0 else "unknown"}')
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True)
        print(f'Working tree: {"modified" if status.stdout.strip() else "clean" if status.returncode == 0 else "unknown"}')
    for path in ROUTES:
        ok, detail = check_asset(args.port, path)
        report(path, ok, detail)
    for name in ('ffmpeg', 'ffprobe'):
        binary = shutil.which(name)
        report(name, bool(binary), binary or 'Not found in PATH (check app bundled binaries separately)')
    from telugu_support import get_telugu_rendering_status
    te = get_telugu_rendering_status()
    report('Telugu shaping', bool(te['ready']), 'Ready' if te['ready'] else '; '.join(te['failures']))

    client = OllamaClient(OllamaConfig(base_url=args.ollama_url, model=args.model, timeout_seconds=args.timeout))
    tags = client.test_connection()
    ready = tags['ok'] and tags['model_available']
    report('Ollama tags/model', ready, tags['configured_model'] if ready else tags['error'])
    if args.generate and ready:
        try:
            result = client.generate('Reply with the single word OK.')
            report('Ollama generation', bool(result['response']), 'Nonempty completed response; text not logged')
        except OllamaError as exc:
            report('Ollama generation', False, str(exc))
    elif args.generate:
        report('Ollama generation', False, 'Skipped: model is not ready')
    else:
        print('SKIP Ollama generation: pass --generate to test actual inference')
    print(f'Failed checks: {len(failures)}')
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
