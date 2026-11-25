"""Durable mailbox, exclusive work claims and optimistic artifact revisions."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,95}", value):
        raise ValueError("Use a 1–96 character identifier: letters, digits, _, ., :, -")
    return value


class Store:
    def __init__(self, path, default_host='dell1'):
        self.path = path
        self.default_host = default_host
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, run TEXT, sender TEXT, recipient TEXT,
                    body TEXT, created REAL, acked REAL);
                CREATE TABLE IF NOT EXISTS tasks (
                    run TEXT, task TEXT, owner TEXT, token TEXT, deadline REAL,
                    state TEXT, revision INTEGER, result TEXT,
                    PRIMARY KEY(run,task));
                CREATE TABLE IF NOT EXISTS artifacts (
                    run TEXT, artifact TEXT, revision INTEGER, author TEXT,
                    content TEXT, sha256 TEXT, created REAL,
                    PRIMARY KEY(run,artifact,revision));
                CREATE TABLE IF NOT EXISTS runs (
                    run TEXT, host TEXT NOT NULL, project TEXT, thread TEXT, turn TEXT,
                    status TEXT, updated REAL, PRIMARY KEY(run,host));
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, run TEXT, kind TEXT, data TEXT, created REAL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'host' not in [r[1] for r in db.execute('PRAGMA table_info(runs)')]:
                db.execute('CREATE TABLE runs_multi (run TEXT,host TEXT NOT NULL,project TEXT,thread TEXT,turn TEXT,status TEXT,updated REAL,PRIMARY KEY(run,host))')
                db.execute('INSERT INTO runs_multi SELECT run,?,project,thread,turn,status,updated FROM runs',(default_host,))
                db.execute('DROP TABLE runs')
                db.execute('ALTER TABLE runs_multi RENAME TO runs')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, run, kind, data):
        with self.db() as db:
            db.execute('INSERT INTO events(run,kind,data,created) VALUES (?,?,?,?)',
                       (run, kind, json.dumps(data), time.time()))

    def message(self, run_id, sender, recipient, body):
        name(run_id); name(sender); name(recipient)
        if not isinstance(body, str) or not 0 < len(body) <= 32768:
            raise ValueError('Message must be 1–32768 characters')
        with self.db() as db:
            cur = db.execute('INSERT INTO messages(run,sender,recipient,body,created) VALUES (?,?,?,?,?)',
                             (run_id, sender, recipient, body, time.time()))
            return {'message_id': cur.lastrowid, 'delivery': 'queued'}

    def inbox(self, run_id, recipient, after=0, wait_seconds=0):
        name(run_id); name(recipient)
        deadline = time.monotonic() + min(max(float(wait_seconds), 0), 30)
        while True:
            with self.db() as db:
                rows = db.execute('SELECT * FROM messages WHERE run=? AND recipient=? AND id>? AND acked IS NULL ORDER BY id LIMIT 100',
                                  (run_id, recipient, int(after))).fetchall()
            if rows or time.monotonic() >= deadline:
                return {'messages': [dict(r) for r in rows], 'cursor': rows[-1]['id'] if rows else after}
            time.sleep(0.15)

    def ack(self, run_id, recipient, message_ids):
        name(run_id); name(recipient)
        if not isinstance(message_ids, list) or len(message_ids) > 100:
            raise ValueError('Provide at most 100 message IDs')
        with self.db() as db:
            count = 0
            for mid in message_ids:
                count += db.execute('UPDATE messages SET acked=? WHERE run=? AND recipient=? AND id=? AND acked IS NULL',
                                    (time.time(), run_id, recipient, int(mid))).rowcount
        return {'acknowledged': count}

    def task_claim(self, run_id, task_id, owner, expected_revision=0, phase_seconds=1800):
        name(run_id); name(task_id); name(owner)
        duration = int(phase_seconds)
        if not 1 <= duration <= 1800:
            raise ValueError('Phase must be between 1 and 1800 seconds')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM tasks WHERE run=? AND task=?', (run_id,task_id)).fetchone()
            if old and (old['revision'] != expected_revision or
                        (old['state'] == 'claimed' and old['deadline'] > time.time())):
                raise ValueError('Task conflict: inspect team_status before claiming')
            if not old and expected_revision != 0:
                raise ValueError('New task requires expected_revision=0')
            if old and old['state'] == 'complete':
                raise ValueError('Completed task is immutable; create a new task ID')
            row = (run_id, task_id, owner, uuid.uuid4().hex, time.time()+duration,
                   'claimed', (old['revision'] if old else 0)+1, '')
            db.execute('INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?,?)', row)
            return dict(db.execute('SELECT * FROM tasks WHERE run=? AND task=?', (run_id,task_id)).fetchone())

    def task_finish(self, run_id, task_id, owner, claim_token, result):
        with self.db() as db:
            changed = db.execute("UPDATE tasks SET state='complete',result=?,revision=revision+1 WHERE run=? AND task=? AND owner=? AND token=? AND state='claimed' AND deadline>=?",
                                 (str(result)[:32768],run_id,task_id,owner,claim_token,time.time())).rowcount
            if changed != 1:
                raise ValueError('Claim expired, changed, or belongs to another participant')
        return {'state': 'complete'}

    def artifact_put(self, run_id, artifact_id, author, content, expected_revision=0):
        name(run_id); name(artifact_id); name(author)
        if not isinstance(content,str) or len(content.encode()) > 524288:
            raise ValueError('Artifact content must be text, at most 512 KiB')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            prev = db.execute('SELECT MAX(revision) FROM artifacts WHERE run=? AND artifact=?', (run_id,artifact_id)).fetchone()[0] or 0
            if prev != expected_revision:
                raise ValueError(f'Artifact revision conflict: current revision is {prev}')
            digest = hashlib.sha256(content.encode()).hexdigest()
            db.execute('INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)',
                       (run_id,artifact_id,prev+1,author,content,digest,time.time()))
            return {'artifact_id':artifact_id,'revision':prev+1,'sha256':digest}

    def artifact_get(self, run_id, artifact_id, revision=None):
        with self.db() as db:
            if revision is None:
                row = db.execute('SELECT * FROM artifacts WHERE run=? AND artifact=? ORDER BY revision DESC LIMIT 1', (run_id,artifact_id)).fetchone()
            else:
                row = db.execute('SELECT * FROM artifacts WHERE run=? AND artifact=? AND revision=?', (run_id,artifact_id,revision)).fetchone()
            if row is None: raise ValueError('Artifact does not exist')
            return dict(row)

    def events(self, run_id, after=0, limit=50):
        with self.db() as db:
            rows = db.execute('SELECT * FROM events WHERE run=? AND id>? ORDER BY id LIMIT ?',
                              (run_id,int(after),min(max(int(limit),1),100))).fetchall()
            return {'events': [{**dict(r),'data':json.loads(r['data'])} for r in rows],
                    'cursor':rows[-1]['id'] if rows else after}

    def status(self, run_id=None, host=None):
        with self.db() as db:
            where, args = (' WHERE run=?', (run_id,)) if run_id else ('', ())
            runs_where = where
            runs_args = args
            if host:
                runs_where += (' AND ' if where else ' WHERE ')+'host=?'
                runs_args += (host,)
            runs = [dict(r) for r in db.execute('SELECT * FROM runs'+runs_where,runs_args)]
            # Claim tokens are returned only to the claimant, never in global status.
            tasks = [dict(r) for r in db.execute('SELECT run,task,owner,deadline,state,revision,result FROM tasks'+where,args)]
            artifacts = [dict(r) for r in db.execute('SELECT run,artifact,MAX(revision) AS revision FROM artifacts'+where+' GROUP BY run,artifact',args)]
            return {'runs':runs,'tasks':tasks,'artifacts':artifacts}

    def run(self, run_id, host=None):
        with self.db() as db:
            row = db.execute('SELECT * FROM runs WHERE run=? AND host=?',(run_id,host or self.default_host)).fetchone()
            return dict(row) if row else None

    def save_run(self, run_id, project, thread, turn, status, host=None):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO runs(run,host,project,thread,turn,status,updated) VALUES (?,?,?,?,?,?,?)',
                       (run_id,host or self.default_host,project,thread,turn,status,time.time()))

    def turn_event(self, thread, turn, status, host=None):
        with self.db() as db:
            db.execute('UPDATE runs SET turn=?,status=?,updated=? WHERE thread=? AND host=?',
                       (turn,status,time.time(),thread,host or self.default_host))

    def run_for_thread(self, thread, host=None):
        with self.db() as db:
            row = db.execute('SELECT run FROM runs WHERE thread=? AND host=?',(thread,host or self.default_host)).fetchone()
            return row[0] if row else '_unassigned'

    def disconnect_host(self, host):
        with self.db() as db:
            db.execute("UPDATE runs SET status='disconnected' WHERE host=? AND status IN ('running','dispatching')",(host,))
