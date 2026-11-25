from contextlib import ExitStack, closing
from io import BytesIO
import json
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import engine
import studio
from agents import (
    AgentCancelledError,
    AgentContext,
    AgentMetadata,
    AgentResult,
    BaseAgent,
    DEFAULT_MODEL,
    DEFAULT_OLLAMA_URL,
    MasterOrchestrator,
    OllamaClient,
    OllamaConfig,
    OllamaError,
)


def wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(interval)
    raise AssertionError('Condition was not met before timeout.')


class FakeSocket:
    """In-memory socket for driving studio.Handler over real HTTP wire bytes."""

    def __init__(self, raw_request: bytes):
        self._rfile = BytesIO(raw_request)
        self._wfile = BytesIO()

    def makefile(self, mode, *args, **kwargs):
        return self._rfile if 'r' in mode else self._wfile

    def sendall(self, data):
        self._wfile.write(data)

    def close(self):
        pass

    @property
    def raw_response(self) -> bytes:
        return self._wfile.getvalue()


class FakeServer:
    server_port = 8765

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator


def invoke_handler(method: str, path: str, *, orchestrator, body: dict | None = None, host: str = '127.0.0.1:8765', decode: bool = True):
    payload = json.dumps(body).encode('utf-8') if body is not None else b''
    headers = [f'{method} {path} HTTP/1.1', f'Host: {host}']
    if body is not None:
        headers.extend([
            f'Origin: http://{host}',
            'Content-Type: application/json',
            f'Content-Length: {len(payload)}',
        ])
    raw = ('\r\n'.join(headers) + '\r\n\r\n').encode('utf-8') + payload
    sock = FakeSocket(raw)
    studio.Handler(sock, ('127.0.0.1', 50000), FakeServer(orchestrator))
    head, _, resp_body = sock.raw_response.partition(b'\r\n\r\n')
    status_line = head.decode('latin-1').splitlines()[0]
    code = int(status_line.split()[1])
    return code, (resp_body.decode('utf-8') if decode else resp_body)


class FakeStreamResponse:
    def __init__(self, status: int, lines: list[bytes]):
        self.status = status
        self._lines = list(lines)
        self._index = 0

    def readline(self):
        if self._index >= len(self._lines):
            return b''
        item = self._lines[self._index]
        self._index += 1
        if callable(item):
            return item()
        return item

    def read(self):
        return b''.join(x for x in self._lines if isinstance(x, bytes))


class OllamaClientTests(unittest.TestCase):
    def test_connection_reports_installed_models_and_missing_model(self):
        body = json.dumps({'models': [{'name': 'qwen2.5:3b'}, {'name': 'llama3.2:3b'}]}).encode()

        class FakeUrlResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return body

        with patch('agents.ollama_client.urlopen', return_value=FakeUrlResponse()) as mock_open:
            client = OllamaClient(OllamaConfig(base_url='http://localhost:11434/', model='qwen2.5:3b'))
            ok = client.test_connection()
            self.assertEqual(mock_open.call_args.args[0].full_url, 'http://localhost:11434/api/tags')
            self.assertTrue(ok['ok'])
            self.assertTrue(ok['model_available'])
            self.assertIn('qwen2.5:3b', ok['installed_models'])
            self.assertIsNone(ok['error'])

            missing = client.test_connection(model='missing-model:7b')
            self.assertTrue(missing['ok'])
            self.assertFalse(missing['model_available'])
            self.assertIn('ollama pull missing-model:7b', missing['error'])

    def test_connection_failure_returns_honest_error(self):
        with patch('agents.ollama_client.urlopen', side_effect=URLError('Connection refused')):
            client = OllamaClient(OllamaConfig(base_url='http://localhost:11434', model='qwen2.5:3b'))
            res = client.test_connection(timeout=1.0)
            self.assertFalse(res['ok'])
            self.assertFalse(res['model_available'])
            self.assertIn('Could not connect to Ollama', res['error'])

    def test_streaming_generate_and_cancellation(self):
        chunks = [
            (json.dumps({'response': 'Hello ', 'done': False}) + '\n').encode(),
            (json.dumps({'response': 'ProdXMart', 'done': True, 'model': 'qwen2.5:3b'}) + '\n').encode(),
        ]

        class FakeHTTPConn:
            def __init__(self, *args, **kwargs):
                self.requests = []
                self.closed = False

            def request(self, method, endpoint, body=None, headers=None):
                self.requests.append((method, endpoint, json.loads(body.decode('utf-8'))))

            def getresponse(self):
                return FakeStreamResponse(200, chunks)

            def close(self):
                self.closed = True

        with patch('agents.ollama_client.http.client.HTTPConnection', FakeHTTPConn):
            client = OllamaClient(OllamaConfig(base_url='http://localhost:11434', model='qwen2.5:3b', timeout_seconds=10.0))
            out = client.generate('normal prompt')
            self.assertEqual(out['response'], 'Hello ProdXMart')
            self.assertEqual(out['model'], 'qwen2.5:3b')

        cancel = threading.Event()

        def delayed_line():
            cancel.set()
            raise socket.timeout()

        cancel_chunks = [
            (json.dumps({'response': 'partial', 'done': False}) + '\n').encode(),
            delayed_line,
        ]

        class CancelHTTPConn(FakeHTTPConn):
            def getresponse(self):
                return FakeStreamResponse(200, cancel_chunks)

        with patch('agents.ollama_client.http.client.HTTPConnection', CancelHTTPConn):
            client = OllamaClient(OllamaConfig(base_url='http://localhost:11434', model='qwen2.5:3b', timeout_seconds=10.0))
            with self.assertRaises(AgentCancelledError):
                client.generate('please cancel', cancel_event=cancel)


class FakeOllamaClient:
    """Controlled test double for OllamaClient used inside orchestrator unit tests."""

    def __init__(self, config: OllamaConfig, tracker: dict):
        self.base_url = config.base_url
        self.model = config.model
        self.timeout_seconds = config.timeout_seconds
        self.tracker = tracker

    def test_connection(self, model=None, timeout=5.0):
        target = model or self.model
        return {
            'ok': True,
            'base_url': self.base_url,
            'configured_model': target,
            'model_available': True,
            'installed_models': [target],
            'latency_ms': 2.5,
            'error': None,
        }

    def generate(self, prompt: str, *, system: str = '', model=None, temperature: float = 0.2, cancel_event=None, format_json: bool = False):
        if cancel_event and cancel_event.is_set():
            raise AgentCancelledError('Cancelled before generation.')
        self.tracker['calls'] += 1
        self.tracker['prompts'].append(prompt)
        if self.tracker['calls'] <= self.tracker.get('fail_count', 0):
            raise OllamaError('Ollama returned HTTP 503 for model "qwen2.5:3b": transient overload', retryable=True)
        return {
            'model': model or self.model,
            'response': f'Grounded response #{self.tracker["calls"]} using {model or self.model}',
            'eval_count': 42,
            'total_duration': 1000,
        }


class OrchestratorAndAgentCenterTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        original = engine.ROOT
        shutil.copytree(original / 'templates', self.root / 'templates')
        shutil.copytree(original / 'demo', self.root / 'demo')
        shutil.copy(original / 'settings.json', self.root / 'settings.json')
        self.data = self.root / 'data'
        (self.data / 'reels').mkdir(parents=True)
        self.stack.enter_context(patch.object(engine, 'ROOT', self.root))
        self.stack.enter_context(patch.object(engine, 'DATA', self.data))
        self.stack.enter_context(patch.object(engine, 'OUT', self.data / 'reels'))

    def _new_orchestrator(self, *, fail_count=0, specialists=None):
        tracker = {'calls': 0, 'prompts': [], 'fail_count': fail_count}
        orch = MasterOrchestrator(
            specialists=specialists,
            ollama_factory=lambda cfg: FakeOllamaClient(cfg, tracker),
        )
        orch.update_config({'retry_delay_seconds': 0.02})
        orch.start()
        self.addCleanup(orch.stop)
        return orch, tracker

    def test_defaults_manual_mode_and_no_auto_execution(self):
        orch = MasterOrchestrator()
        orch.start()
        self.addCleanup(orch.stop)
        state = orch.get_state(mode='demo')
        cfg = state['config']
        self.assertEqual(cfg['ollama_base_url'], DEFAULT_OLLAMA_URL)
        self.assertEqual(cfg['default_model'], DEFAULT_MODEL)
        self.assertEqual(cfg['execution_mode'], 'manual')
        self.assertFalse(cfg['allow_publishing'])
        self.assertFalse(cfg['allow_spending'])
        self.assertFalse(cfg['require_factori_api'])
        self.assertEqual(state['runs'], [])
        self.assertEqual(len(state['agents']), 9)
        for card in state['agents']:
            self.assertFalse(card['can_publish'])
            self.assertFalse(card['can_spend'])
            self.assertEqual(card['status'], 'idle')

    def test_master_orchestrator_runs_enabled_specialists_and_skips_disabled(self):
        orch, tracker = self._new_orchestrator()
        orch.set_agent_enabled('performance_analyst', False)

        run = orch.enqueue_run('master_orchestrator', mode='demo')
        done = wait_until(lambda: (r := orch.storage.get_run(run['id'])) and r['status'] == 'succeeded' and r, timeout=20.0)
        self.assertEqual(done['progress'], 100)
        output = done['output']
        self.assertEqual(output['data']['executed_agents'], ['catalog_agent', 'catalogue_auditor', 'reel_copy_strategist', 'marketing_agent', 'video_agent', 'qa_agent', 'social_agent'])
        self.assertEqual(output['data']['skipped_agents'], ['performance_analyst'])
        self.assertIn('catalog_agent', output['data']['steps'])
        self.assertIn('catalogue_auditor', output['data']['steps'])
        self.assertIn('reel_copy_strategist', output['data']['steps'])
        self.assertIn('marketing_agent', output['data']['steps'])
        self.assertIn('video_agent', output['data']['steps'])
        self.assertIn('qa_agent', output['data']['steps'])
        self.assertIn('social_agent', output['data']['steps'])
        self.assertEqual(tracker['calls'], 4)  # 3 Ollama specialists + 1 orchestrator synthesis

        all_runs = orch.storage.list_runs(mode='demo')
        child_runs = [r for r in all_runs if r['parent_run_id'] == run['id']]
        self.assertEqual(len(child_runs), 7)
        self.assertTrue(all(r['status'] == 'succeeded' for r in child_runs))
        with closing(engine.db()) as con:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'jobs', 'reel_assets', 'agent_runs', 'agent_logs', 'agent_configs', 'agent_global_config'}.issubset(tables))

    def test_retry_handling_automatic_and_manual_and_error_logging(self):
        orch, tracker = self._new_orchestrator(fail_count=2)
        orch.update_config({'max_retries': 1, 'retry_delay_seconds': 0.02})

        first = orch.enqueue_run('catalogue_auditor', mode='demo')
        failed = wait_until(lambda: (r := orch.storage.get_run(first['id'])) and r['status'] == 'failed' and r)
        self.assertEqual(failed['attempt'], 2)
        self.assertIn('503', failed['error_message'])
        logs = orch.storage.list_logs(run_id=first['id'])
        error_logs = [l for l in logs if l['level'] == 'ERROR']
        self.assertEqual(len(error_logs), 2)

        retried = orch.retry_run(first['id'])
        self.assertEqual(retried['retry_of_run_id'], first['id'])
        done = wait_until(lambda: (r := orch.storage.get_run(retried['id'])) and r['status'] == 'succeeded' and r)
        self.assertEqual(done['progress'], 100)
        self.assertEqual(tracker['calls'], 3)

    def test_task_queue_and_cancellation_for_queued_and_running_tasks(self):
        started = threading.Event()
        release = threading.Event()

        class SlowAgent(BaseAgent):
            metadata = AgentMetadata(
                agent_id='slow_test',
                name='Slow Test Agent',
                role='specialist',
                description='Controlled test agent for queue and cancellation.',
                requires_ollama=False,
            )

            def execute(self, ctx: AgentContext) -> AgentResult:
                ctx.progress(20, 'Working...')
                started.set()
                while not release.is_set():
                    ctx.check_cancelled()
                    time.sleep(0.02)
                return AgentResult(summary='Finished slow test', data={'ok': True}, model_used=ctx.model)

        orch, _ = self._new_orchestrator(specialists=[SlowAgent()])
        first = orch.enqueue_run('slow_test', mode='demo')
        second = orch.enqueue_run('slow_test', mode='demo')
        self.assertTrue(started.wait(timeout=3.0))

        cancelled_second = orch.stop_run(second['id'])
        self.assertEqual(cancelled_second['status'], 'cancelled')

        orch.stop_run(first['id'])
        stopped_first = wait_until(lambda: (r := orch.storage.get_run(first['id'])) and r['status'] == 'cancelled' and r)
        self.assertEqual(stopped_first['status'], 'cancelled')
        self.assertTrue(any(l['level'] == 'WARNING' for l in orch.storage.list_logs(run_id=first['id'])))

    def test_reconcile_interrupted_runs_on_restart(self):
        orch, _ = self._new_orchestrator()
        stale = orch.storage.create_run(
            agent_id='catalogue_auditor',
            mode='demo',
            trigger='manual',
            model='qwen2.5:3b',
            max_retries=1,
        )
        orch.storage.update_run(stale['id'], status='running')
        orch.stop()

        restarted = MasterOrchestrator()
        restarted.start()
        self.addCleanup(restarted.stop)
        recovered = restarted.storage.get_run(stale['id'])
        self.assertEqual(recovered['status'], 'failed')
        self.assertIn('interrupted', recovered['error_message'].lower())

    def test_http_endpoints_and_existing_ui_routes_preserved(self):
        orch, _ = self._new_orchestrator()

        code, html_body = invoke_handler('GET', '/', orchestrator=orch)
        self.assertEqual(code, 200)
        self.assertIn('AI Agent Center', html_body)
        self.assertIn('/agents.js', html_body)

        code, agents_js = invoke_handler('GET', '/agents.js', orchestrator=orch)
        self.assertEqual(code, 200)
        self.assertIn('refreshAgents', agents_js)

        for asset_path in ('/css/tokens.css', '/css/components.css', '/css/layout.css', '/js/ui_core.js', '/js/overview.js', '/js/agent_views.js', '/vendor/lucide.min.js', '/vendor/chart.umd.min.js'):
            acode, _ = invoke_handler('GET', asset_path, orchestrator=orch, decode=False)
            self.assertEqual(acode, 200, f'Failed to serve {asset_path}')

        code, sys_int_raw = invoke_handler('GET', '/api/system/integrations?mode=demo', orchestrator=orch)
        self.assertEqual(code, 200)
        sys_int = json.loads(sys_int_raw)
        self.assertIn('ollama', sys_int['integrations'])
        self.assertIn('factori_official_api', sys_int['integrations'])
        self.assertEqual(sys_int['integrations']['factori_official_api']['status'], 'pending')
        self.assertGreaterEqual(len(sys_int['integrations']['factori_official_api']['checklist']), 10)
        self.assertNotIn('token', json.dumps(sys_int['safe_config']).lower())

        code, ov_raw = invoke_handler('GET', '/api/overview?mode=demo&range=month', orchestrator=orch)
        self.assertEqual(code, 200)
        ov = json.loads(ov_raw)
        self.assertEqual(ov['kpis']['total_products'], 5)
        self.assertEqual(ov['kpis']['ready_products'], 4)
        self.assertEqual(ov['kpis']['attention_products'], 1)
        self.assertEqual(ov['integration_health']['factori_api']['status'], 'pending')

        code, state_json = invoke_handler('GET', '/state?mode=demo', orchestrator=orch)
        self.assertEqual(code, 200)
        self.assertEqual(len(json.loads(state_json)['products']), 5)

        code, agent_state_raw = invoke_handler('GET', '/agents/state?mode=demo', orchestrator=orch)
        self.assertEqual(code, 200)
        agent_state = json.loads(agent_state_raw)
        self.assertEqual(agent_state['config']['default_model'], 'qwen2.5:3b')

        code, test_raw = invoke_handler('POST', '/agents/ollama/test', orchestrator=orch, body={'mode': 'demo'})
        self.assertEqual(code, 200)
        test_res = json.loads(test_raw)
        self.assertTrue(test_res['ok'])
        self.assertTrue(test_res['model_available'])

        code, bad_cfg = invoke_handler('POST', '/agents/config', orchestrator=orch, body={'mode': 'demo', 'execution_mode': 'autonomous'})
        self.assertEqual(code, 400)
        self.assertIn('manual', json.loads(bad_cfg)['error'].lower())

        code, _ = invoke_handler('POST', '/agents/toggle', orchestrator=orch, body={'mode': 'demo', 'agent_id': 'performance_analyst', 'enabled': False})
        self.assertEqual(code, 200)
        code, disabled_run = invoke_handler('POST', '/agents/run', orchestrator=orch, body={'mode': 'demo', 'agent_id': 'performance_analyst'})
        self.assertEqual(code, 400)
        self.assertIn('disabled', json.loads(disabled_run)['error'].lower())

        code, _ = invoke_handler('POST', '/agents/toggle', orchestrator=orch, body={'mode': 'demo', 'agent_id': 'performance_analyst', 'enabled': True})
        self.assertEqual(code, 200)
        code, run_raw = invoke_handler('POST', '/agents/run', orchestrator=orch, body={'mode': 'demo', 'agent_id': 'performance_analyst'})
        self.assertEqual(code, 200)
        run_id = json.loads(run_raw)['run']['id']
        wait_until(lambda: (r := orch.storage.get_run(run_id)) and r['status'] == 'succeeded')

        code, history_raw = invoke_handler('GET', '/agents/history?mode=demo&agent_id=performance_analyst', orchestrator=orch)
        self.assertEqual(code, 200)
        history = json.loads(history_raw)
        self.assertEqual(len(history['runs']), 1)
        self.assertEqual(history['runs'][0]['id'], run_id)
        self.assertTrue(len(history['logs']) >= 2)


if __name__ == '__main__':
    unittest.main()
