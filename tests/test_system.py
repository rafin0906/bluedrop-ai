import ctypes
import importlib.util
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value
r = module('receiver', ROOT/'receiver/receiver.py')
b = module('broker', ROOT/'backend/broker.py')
from fastapi import HTTPException
from fastapi.testclient import TestClient
TOKEN = 'test-only-token-' + 'x'*40

class ProtocolTests(unittest.TestCase):
    def test_windows_abi(self):
        self.assertEqual(ctypes.sizeof(r.SOCKADDR_BTH), 30)
        self.assertEqual(r.SOCKADDR_BTH.address.offset, 2)
        self.assertEqual(ctypes.sizeof(r.GUID), 16)
        self.assertEqual(ctypes.sizeof(r.DEVICE_INFO), 560)
    def test_address(self):
        self.assertEqual(r.address_text(r.parse_address('54:10:4F:F0:5A:E4')), '54:10:4F:F0:5A:E4')
        with self.assertRaises(ValueError): r.parse_address('not a MAC')
    def test_fragmented_unicode_frame(self):
        a, c = socket.socketpair()
        with a, c:
            raw = json.dumps({'answer': 'বাংলা ✓'}, ensure_ascii=False).encode()
            data = struct.pack('>I', len(raw)) + raw
            def writer():
                for v in data: c.sendall(bytes([v]))
            thread = threading.Thread(target=writer); thread.start()
            self.assertEqual(r.receive_json(a)['answer'], 'বাংলা ✓'); thread.join()
    def test_oversized_frame(self):
        a, c = socket.socketpair()
        with a, c:
            c.sendall(struct.pack('>I', r.MAX_FRAME+1))
            with self.assertRaises(r.ProtocolError): r.receive_json(a)
    def test_truncated_frame(self):
        a, c = socket.socketpair()
        with a, c:
            c.sendall(struct.pack('>I', 50)+b'{}'); c.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError): r.receive_json(a)
    def test_non_object(self):
        a, c = socket.socketpair()
        with a, c:
            c.sendall(struct.pack('>I', 2)+b'[]')
            with self.assertRaises(r.ProtocolError): r.receive_json(a)
    def test_context_trims_whole_turns(self):
        history = [{'role': role, 'content': 'x'*20000} for role in ['user','assistant','user','assistant']]
        messages = r.context_messages(history, 'বাংলায় উত্তর দাও')
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0]['role'], 'user')
        self.assertLessEqual(sum(len(m['content'].encode()) for m in messages), 65536)
    def test_prompt_limit(self):
        with self.assertRaises(ValueError): r.new_request([], 'ক'*30000)
    def test_no_terminal_escape(self):
        self.assertNotIn('\x1b', r.printable('\x1b[31mhello\x07'))
    def test_atomic_save_and_collision(self):
        with tempfile.TemporaryDirectory() as folder:
            reply = {'id': str(uuid4()), 'answer': 'বাংলা answer'}
            target = r.save_reply(folder, reply)
            self.assertEqual(target.read_text(encoding='utf-8'), reply['answer'])
            self.assertEqual(r.save_reply(folder, reply), target)
            target.write_text('user edited', encoding='utf-8')
            with self.assertRaises(OSError): r.save_reply(folder, reply)
            self.assertEqual(target.read_text(encoding='utf-8'), 'user edited')
            self.assertFalse(list(Path(folder).glob('*.part')))
    def test_mismatched_response(self):
        a, c = socket.socketpair()
        request = r.new_request([], 'hello')
        with a, c:
            r.send_json(c, {'id': str(uuid4()), 'type': 'result', 'answer': 'bad'})
            with self.assertRaises(r.ProtocolError): r.chat_session(a, request)

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        def provider(messages):
            self.calls.append(messages)
            return 'উত্তর: '+messages[-1]['content'], 'stop'
        self.broker = b.Broker(Path(self.tmp.name)/'jobs.db', 'test-key', TOKEN, provider=provider)
    def tearDown(self): self.tmp.cleanup()
    def test_deduplication(self):
        req = r.new_request([], 'hello')
        self.broker.submit(req); self.broker.work_one()
        self.assertEqual(self.broker.submit(req)['status'], 'completed')
        self.assertFalse(self.broker.work_one()); self.assertEqual(len(self.calls), 1)
    def test_id_conflict(self):
        req = r.new_request([], 'hello'); self.broker.submit(req)
        req['messages'][0]['content'] = 'changed'
        with self.assertRaises(HTTPException) as caught: self.broker.submit(req)
        self.assertEqual(caught.exception.status_code, 409)
    def test_invalid_messages(self):
        for messages in [[], [{'role': 'system', 'content': 'x'}], [{'role': 'user', 'content': 'ক'*30000}], [{'role': 'user', 'content': ''}]]:
            with self.assertRaises(HTTPException): self.broker.submit({'id': str(uuid4()), 'messages': messages})
    def test_failure_persists(self):
        self.broker.provider = lambda _: (_ for _ in ()).throw(TimeoutError('sensitive detail'))
        req = r.new_request([], 'hello'); self.broker.submit(req); self.broker.work_one()
        result = self.broker.get(req['id'])
        self.assertEqual(result['status'], 'failed'); self.assertNotIn('sensitive detail', result['error'])
    def test_restart_retains_completed(self):
        req = r.new_request([], 'hello'); self.broker.submit(req); self.broker.work_one()
        reopened = b.Broker(self.broker.path, 'test-key', TOKEN)
        self.assertEqual(reopened.get(req['id'])['status'], 'completed')
    def test_crash_does_not_repeat_provider_call(self):
        req = r.new_request([], 'hello'); self.broker.submit(req)
        with self.broker.db() as db: db.execute("UPDATE jobs SET status='running'")
        reopened = b.Broker(self.broker.path, 'test-key', TOKEN)
        self.assertEqual(reopened.get(req['id'])['status'], 'failed')
        self.assertFalse(reopened.work_one())
    def test_auth_and_http(self):
        with TestClient(b.create_app(self.broker)) as client:
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.get('/v1/config').status_code, 401)
            headers = {'Authorization': 'Bearer '+TOKEN}
            self.assertEqual(client.get('/v1/config', headers=headers).json()['protocol'], 2)
            self.assertEqual(client.post('/v1/jobs', headers=headers, content=b'bad json').status_code, 422)
            self.assertEqual(client.post('/v1/jobs', headers=headers, content=b'x'*(b.MAX_BODY+1)).status_code, 413)
            req = r.new_request([], 'test')
            self.assertEqual(client.post('/v1/jobs', headers=headers, json=req).status_code, 200)
            until = time.monotonic()+3
            while time.monotonic() < until:
                result = client.get('/v1/jobs/'+req['id'], headers=headers).json()
                if result['status'] == 'completed': break
                time.sleep(.03)
            self.assertEqual(result['answer'], 'উত্তর: test')
    def test_queue_limit(self):
        for _ in range(20): self.broker.submit(r.new_request([], 'hello'))
        with self.assertRaises(HTTPException) as caught: self.broker.submit(r.new_request([], 'extra'))
        self.assertEqual(caught.exception.status_code, 429)
    def test_full_client_reconnect_saved_once(self):
        """Real PC code + framed socket surrogate for Android + HTTP broker + mock Groq.
        Drop first Bluetooth reply after generation, then retry the same job ID.
        This is a software integration test, not a physical Bluetooth test.
        """
        errors = []; threads = []; attempts = []
        with TestClient(b.create_app(self.broker)) as http:
            headers = {'Authorization': 'Bearer '+TOKEN}
            def connect(_address):
                a, c = socket.socketpair(); a.settimeout(5); c.settimeout(5)
                attempts.append(1); drop = len(attempts) == 1
                def phone():
                    try:
                        with c:
                            request = r.receive_json(c)
                            job = http.post('/v1/jobs', headers=headers, json=request).json()
                            until = time.monotonic()+5
                            while job['status'] != 'completed':
                                if time.monotonic() > until: raise TimeoutError()
                                time.sleep(.01)
                                job = http.get('/v1/jobs/'+request['id'], headers=headers).json()
                            if drop: return
                            r.send_json(c, {'type': 'result', 'id': request['id'], 'answer': job['answer'], 'finish_reason': 'stop'})
                            self.assertEqual(r.receive_json(c)['type'], 'ack')
                    except Exception as exc: errors.append(exc)
                thread = threading.Thread(target=phone); thread.start(); threads.append(thread)
                return a
            state = {'history': [], 'pending': r.new_request([], 'বাংলা test')}
            path = Path(self.tmp.name)/'chat.json'; r.atomic_json(path, state)
            # Bypass only reconnect backoff; phone worker still gets actual short sleeps.
            real_sleep = time.sleep
            with patch.object(r.time, 'sleep', side_effect=lambda seconds: real_sleep(min(seconds, .01))):
                result = r.execute_pending({'address': 'unused', 'output': self.tmp.name}, state, path, connect)
            for thread in threads: thread.join(6)
            self.assertFalse(errors, errors); self.assertEqual(len(attempts), 2)
            self.assertEqual(len(self.calls), 1)
            self.assertIsNone(json.loads(path.read_text(encoding='utf-8'))['pending'])
            self.assertEqual(len(state['history']), 2)
            self.assertEqual(len(list(Path(self.tmp.name).glob('answer-*.txt'))), 1)
            self.assertIn('বাংলা', result['answer'])

if __name__ == '__main__': unittest.main()
