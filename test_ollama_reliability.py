"""Regression tests for malformed Ollama responses, deadline and cancellation."""
import json
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from agents.base import AgentCancelledError
from agents.ollama_client import OllamaClient, OllamaConfig, OllamaError


class OllamaReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient(OllamaConfig(timeout_seconds=5))

    def tags_response(self, body):
        context = Mock()
        context.__enter__ = Mock(return_value=context)
        context.__exit__ = Mock(return_value=False)
        context.read.return_value = json.dumps(body).encode()
        with patch('agents.ollama_client.urlopen', return_value=context):
            return self.client.test_connection()

    def test_tags_requires_models_list(self):
        for body in (None, [], {}, {'models': 'wrong'}, {'models': None}):
            with self.subTest(body=body):
                result = self.tags_response(body)
                self.assertFalse(result['ok'])
                self.assertFalse(result['model_available'])
                self.assertIn('expected a models list', result['error'])

    def test_valid_tags_and_missing_model(self):
        result = self.tags_response({'models': [{'name': self.client.model}]})
        self.assertTrue(result['ok'])
        self.assertTrue(result['model_available'])
        result = self.tags_response({'models': []})
        self.assertTrue(result['ok'])
        self.assertFalse(result['model_available'])

    def connection(self, lines):
        conn = Mock()
        response = conn.getresponse.return_value
        response.status = 200
        response.readline.side_effect = lines
        return conn

    def test_stream_rejects_non_object_and_malformed_json(self):
        for line in (b'[]\n', b'null\n', b'"text"\n', b'not-json\n'):
            with self.subTest(line=line):
                conn = self.connection([line])
                with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn):
                    with self.assertRaisesRegex(OllamaError, 'Invalid JSON chunk') as err:
                        self.client.generate('test')
                    self.assertTrue(err.exception.retryable)
                conn.close.assert_called_once()

    def test_stream_requires_actual_done_boolean(self):
        conn = self.connection([b'{"response":"partial","done":"true"}\n', b''])
        with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn):
            with self.assertRaisesRegex(OllamaError, 'ended before generation completed'):
                self.client.generate('test')
        conn.close.assert_called_once()

    def test_stream_success(self):
        conn = self.connection([b'{"response":"hello ","done":false}\n',
                                b'{"response":"world","done":true}\n'])
        with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn):
            self.assertEqual(self.client.generate('test')['response'], 'hello world')
        conn.close.assert_called_once()

    def test_late_done_is_timeout(self):
        conn = self.connection([b'{"response":"late","done":true}\n'])
        with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn), \
             patch('agents.ollama_client.time.monotonic', side_effect=[0, 1, 6]):
            with self.assertRaisesRegex(OllamaError, 'timed out'):
                self.client.generate('test')
        conn.close.assert_called_once()

    def test_cancel_during_read_rejects_result(self):
        cancel = threading.Event()
        conn = self.connection([])
        def read():
            cancel.set()
            return b'{"response":"late","done":true}\n'
        conn.getresponse.return_value.readline.side_effect = read
        with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn):
            with self.assertRaises(AgentCancelledError):
                self.client.generate('test', cancel_event=cancel)
        conn.close.assert_called_once()

    def test_socket_timeout_is_retryable(self):
        conn = self.connection([socket.timeout('test timeout')])
        with patch('agents.ollama_client.http.client.HTTPConnection', return_value=conn):
            with self.assertRaisesRegex(OllamaError, 'timed out') as err:
                self.client.generate('test')
            self.assertTrue(err.exception.retryable)
        conn.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
