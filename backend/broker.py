"""Single-user Groq broker. Run one Uvicorn worker; persist /data on deployment."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import UUID

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request as WebRequest, Depends

load_dotenv(Path(__file__).with_name('.env'))
MAX_BODY = 512 * 1024
MAX_TEXT = 64 * 1024
MAX_ANSWER = 200 * 1024

class Broker:
    def __init__(self, db_path, key, token, model='gpt-5.6-sol', endpoint=None, provider=None):
        if len(token) < 32 or token.startswith('CHANGE_'):
            raise ValueError('Set BRIDGE_TOKEN to a random token of at least 32 characters')
        if not key or key.startswith('PASTE_'):
            raise ValueError('Set OPENAI_API_KEY or GROQ_API_KEY in backend/.env')
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.key, self.token, self.model = key, token, model
        if endpoint:
            self.endpoint = endpoint
        elif self.key.startswith('sk-') or 'gpt' in self.model.lower():
            self.endpoint = 'https://api.openai.com/v1/chat/completions'
        else:
            self.endpoint = 'https://api.groq.com/openai/v1/chat/completions'
        self.provider = provider or self.call_ai
        self.stop = threading.Event()
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, digest TEXT NOT NULL, messages TEXT NOT NULL,
                status TEXT NOT NULL, answer TEXT, error TEXT, finish_reason TEXT,
                created REAL NOT NULL)''')
            # Never repeat an ambiguous provider call automatically after a crash.
            db.execute("UPDATE jobs SET status='failed', error='Broker restarted during generation. Submit a new message to retry.' WHERE status='running'")
        self.thread = threading.Thread(target=self.worker, daemon=True)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, data):
        if not isinstance(data, dict): raise HTTPException(422, 'Expected JSON object')
        try:
            ident = str(UUID(data['id']))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise HTTPException(422, 'id must be a UUID')
        messages = data.get('messages')
        if not isinstance(messages, list) or not 1 <= len(messages) <= 40:
            raise HTTPException(422, 'messages must contain 1–40 messages')
        normalized = []
        for m in messages:
            if not isinstance(m, dict) or m.get('role') not in ('user', 'assistant') or not isinstance(m.get('content'), str) or not m['content'].strip():
                raise HTTPException(422, 'Each message needs role user/assistant and nonempty content')
            normalized.append({'role': m['role'], 'content': m['content']})
        if normalized[-1]['role'] != 'user': raise HTTPException(422, 'Last message must be user')
        try:
            total = sum(len(m['content'].encode('utf-8')) for m in normalized)
        except UnicodeError:
            raise HTTPException(422, 'Invalid Unicode')
        if total > MAX_TEXT: raise HTTPException(413, 'Context exceeds 64 KiB UTF-8')
        raw = json.dumps(normalized, ensure_ascii=False, separators=(',', ':'))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
            if old:
                if old['digest'] != digest: raise HTTPException(409, 'Request ID reused with different content')
                return self.public(old)
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 20:
                raise HTTPException(429, 'Queue full; try again later')
            if db.execute('SELECT COUNT(*) FROM jobs WHERE created>?', (time.time()-60,)).fetchone()[0] >= 20:
                raise HTTPException(429, 'At most 20 new requests per minute')
            db.execute('INSERT INTO jobs(id,digest,messages,status,created) VALUES(?,?,?,?,?)',
                       (ident, digest, raw, 'queued', time.time()))
            return self.public(db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone())

    def public(self, row):
        return {k: row[k] for k in ('id', 'status', 'answer', 'error', 'finish_reason')}

    def get(self, ident):
        with self.db() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
        if not row: raise HTTPException(404, 'Job not found')
        return self.public(row)

    def call_ai(self, messages):
        body = json.dumps({'model': self.model, 'messages': messages,
                           'max_completion_tokens': 8192, 'stream': False}).encode()
        req = Request(self.endpoint, body,
                      {'Authorization': f'Bearer {self.key}', 'Content-Type': 'application/json',
                       'User-Agent': 'BlueDropAI/2.0'}, method='POST')
        try:
            with urlopen(req, timeout=120) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024: raise ValueError('Provider response too large')
                choice = json.loads(raw)['choices'][0]
                answer = choice['message']['content']
                reason = choice.get('finish_reason', 'stop')
        except HTTPError as exc:
            # Do not return provider bodies/headers or secrets to clients/logs.
            raise ValueError(f'AI Provider HTTP {exc.code}. Check API key, quota and model access; submit again after fixing.') from None
        if not isinstance(answer, str) or not answer.strip(): raise ValueError('Provider returned no answer')
        if len(answer.encode('utf-8')) > MAX_ANSWER: raise ValueError('Answer exceeds 200 KiB')
        return answer, reason

    call_groq = call_ai

    def work_one(self):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not job: return False
            db.execute("UPDATE jobs SET status='running' WHERE id=?", (job['id'],))
        try:
            answer, reason = self.provider(json.loads(job['messages']))
            if not isinstance(answer, str) or not answer.strip() or len(answer.encode()) > MAX_ANSWER:
                raise ValueError('Invalid or oversized answer')
            with self.db() as db:
                db.execute("UPDATE jobs SET status='completed',answer=?,finish_reason=?,messages='[]' WHERE id=?",
                           (answer, reason, job['id']))
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else 'Provider connection failed or timed out. Submit a new message to retry.'
            with self.db() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,messages='[]' WHERE id=?", (error, job['id']))
        return True

    def worker(self):
        while not self.stop.is_set():
            try:
                if self.work_one(): continue
            except sqlite3.Error:
                pass
            self.stop.wait(0.5)


def create_app(broker=None):
    @asynccontextmanager
    async def lifespan(app):
        key = os.getenv('OPENAI_API_KEY') or os.getenv('GROQ_API_KEY', '')
        model = os.getenv('OPENAI_MODEL') or os.getenv('GROQ_MODEL', 'gpt-5.6-sol')
        endpoint = os.getenv('OPENAI_BASE_URL') or os.getenv('AI_ENDPOINT')
        app.state.broker = broker or Broker(
            os.getenv('DATABASE_PATH', './data/jobs.sqlite3'),
            key,
            os.getenv('BRIDGE_TOKEN', ''),
            model,
            endpoint=endpoint)
        app.state.broker.thread.start()
        yield
        app.state.broker.stop.set()
        app.state.broker.thread.join(timeout=130)

    app = FastAPI(title='BlueDrop AI Broker', version='2.0.0', lifespan=lifespan)

    def authorized(request: WebRequest):
        b = request.app.state.broker
        supplied = request.headers.get('authorization', '')
        if not hmac.compare_digest(supplied.encode(), ('Bearer '+b.token).encode()):
            raise HTTPException(401, 'Invalid bridge token')
        return b

    @app.get('/')
    @app.get('/healthz')
    def health(): return {'status': 'ok', 'service': 'bluedrop-ai'}

    @app.get('/v1/config')
    def config(b=Depends(authorized)):
        return {'model': b.model, 'max_context_bytes': MAX_TEXT, 'protocol': 2}

    @app.post('/v1/jobs')
    async def submit(request: WebRequest, b=Depends(authorized)):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BODY: raise HTTPException(413, 'Request body too large')
        try: data = json.loads(body)
        except (ValueError, UnicodeError): raise HTTPException(422, 'Invalid JSON')
        return b.submit(data)

    @app.get('/v1/jobs/{ident}')
    def get(ident: str, b=Depends(authorized)):
        return b.get(ident)

    return app

app = create_app()
