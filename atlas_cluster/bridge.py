import fcntl
import json
import os
import signal
import socketserver
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .protocol import Remote, user_input
from .store import Store, name
from .settings import load_settings, for_host, coordinator_mailbox

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT/'.state'
SOCKET = STATE/'bridge.sock'


class Bridge:
    def __init__(self, root=ROOT):
        self.root = root
        self.state = root/'.state'
        self.settings = load_settings(root)
        self.default_host = self.settings['default_host']
        self.store = Store(self.state/'team.sqlite3',self.default_host)
        self.remotes = {h:Remote(for_host(self.settings,h),self.state/'bridge.sock',self.state,self.store) for h in self.settings['hosts']}
        self.control_locks = {h:threading.Lock() for h in self.remotes}
        with self.store.db() as db:
            db.execute("UPDATE runs SET status='disconnected' WHERE status IN ('running','dispatching')")

    @property
    def remote(self):
        return self.remotes[self.default_host]

    @remote.setter
    def remote(self, value):
        self.remotes[self.default_host] = value

    def select_host(self, host=None):
        selected = for_host(self.settings,host)
        return selected['host'],selected,self.remotes[selected['host']]

    def call(self, action, args):
        if action == 'team_status':
            args = dict(args)
            refresh = args.pop('refresh_remote',False)
            selected,_,_ = self.select_host(args.get('host'))
            hosts = {}
            for h,remote in self.remotes.items():
                active,error = None,remote.last_error
                if refresh and (not args.get('host') or h == selected) and remote.connected():
                    try: active = remote.active_threads()
                    except Exception as exc: error = str(exc)
                hosts[h] = {'connected':remote.connected(),'connection_error':error,'active_remote_threads':active,
                            'coordinator_mailbox':coordinator_mailbox(h,self.default_host),'worker_limit':16}
            return {**self.store.status(**args),'connected':hosts[selected]['connected'],
                    'connection_error':hosts[selected]['connection_error'],'host':selected,'hosts':hosts,
                    'active_remote_threads':hosts[selected]['active_remote_threads'],
                    'remote_worker_limit':16,'active_remote_coordinator_limit':1,
                    'total_remote_worker_limit':16*len(self.remotes),
                    'laptop_delivery':'poll inbox; no automatic native-session wakeup'}
        actions = {'team_inbox':'inbox','team_ack':'ack','team_task_claim':'task_claim',
                   'team_task_finish':'task_finish','team_artifact_put':'artifact_put',
                   'team_artifact_get':'artifact_get','team_events':'events'}
        if action in actions: return getattr(self.store,actions[action])(**args)
        if action == 'team_message': return self.message(**args)
        if action == 'team_dispatch': return self.dispatch(**args)
        if action == 'team_interrupt': return self.interrupt(**args)
        if action in ('_connect','_disconnect'):
            host,_,remote = self.select_host(args.get('host'))
            with self.control_locks[host]:
                if action == '_connect': remote.connect()
                else: remote.close()
            return {'host':host,'connected':remote.connected()}
        raise ValueError(f'Unknown action: {action}')

    def dispatch(self, run_id, prompt, project='atlas', host=None):
        name(run_id)
        host,settings,remote = self.select_host(host)
        if not isinstance(prompt,str) or not 1 <= len(prompt) <= 65536:
            raise ValueError('Prompt must be 1–65536 characters')
        if project not in settings['projects']: raise ValueError('Unknown project')
        with self.control_locks[host]:
            for r in self.store.status(host=host)['runs']:
                if r['status'] in ('running','dispatching','disconnected'):
                    raise ValueError(f"Run {r['run']} on {host} is {r['status']}; inspect/interrupt before dispatching. Only one team per host runs at once.")
            remote.connect()
            active = remote.active_threads()
            if active:
                raise ValueError(f'Remote native threads are still active: {active}. Wait or stop the bridge before launching another team.')
            previous = self.store.run(run_id,host)
            if previous and previous['project'] != project:
                raise ValueError('Run IDs cannot change projects')
            if previous:
                thread = previous['thread']
                if thread not in remote.loaded:
                    remote.request('thread/resume',{'threadId':thread,'excludeTurns':True})
                    remote.loaded.add(thread)
            else:
                instructions = (self.root/'REMOTE_INSTRUCTIONS.md').read_text()
                mailbox = coordinator_mailbox(host,self.default_host)
                instructions += f'\nCurrent run_id: {run_id}\nExecution host: {host}\nYour mailbox identity: {mailbox}\nLaptop coordinator mailbox: laptop-coordinator\nPrefix worker identities and task IDs with {host}: to prevent collisions with other hosts.\n'
                reply = remote.request('thread/start',{
                    'cwd':settings['projects'][project]['remote_cwd'],
                    'model':settings['model'],'modelProvider':settings['provider'],
                    'sandbox':'workspace-write','approvalPolicy':'on-request',
                    'developerInstructions':instructions})
                thread = reply['thread']['id']
                remote.loaded.add(thread)
            self.store.save_run(run_id,project,thread,None,'dispatching',host)
            try:
                reply = remote.request('turn/start',{'threadId':thread,'input':user_input(prompt)})
            except Exception:
                self.store.save_run(run_id,project,thread,None,'disconnected',host)
                raise
            turn = reply['turn']['id']
            # A completion notification may already have arrived. Do not overwrite it.
            current = self.store.run(run_id,host)
            if current['status'] == 'dispatching':
                self.store.save_run(run_id,project,thread,turn,'running',host)
            self.store.event(run_id,'dispatch',{'host':host,'threadId':thread,'turnId':turn,'project':project})
            return {'run_id':run_id,'host':host,'coordinator_mailbox':coordinator_mailbox(host,self.default_host),
                    'thread_id':thread,'turn_id':turn,'status':self.store.run(run_id,host)['status']}

    def message(self, run_id, sender, recipient, body, steer=False):
        result = self.store.message(run_id,sender,recipient,body)
        if steer:
            recipient_hosts = {coordinator_mailbox(h,self.default_host):h for h in self.remotes}
            if recipient not in recipient_hosts:
                result['steer_error'] = 'Only the remote coordinator supports live steering; other participants poll their inbox.'
                return result
            host = recipient_hosts[recipient]
            remote = self.remotes[host]
            with self.control_locks[host]:
                run = self.store.run(run_id,host)
                if not run or run['status'] != 'running' or not remote.connected():
                    result['steer_error'] = 'No connected active turn. Message remains in durable inbox; no new turn was started.'
                else:
                    try:
                        remote.request('turn/steer',{'threadId':run['thread'],'expectedTurnId':run['turn'],
                            'input':user_input(f"Bridge message {result['message_id']} from {sender} (run {run_id}):\n{body}\nAcknowledge this message with team_ack after reading.")})
                        result['delivery'] = 'queued_and_steered'
                    except Exception as exc:
                        result['steer_error'] = str(exc)
        return result

    def interrupt(self, run_id, host=None):
        host,_,remote = self.select_host(host)
        with self.control_locks[host]:
            run = self.store.run(run_id,host)
            if not run: raise ValueError('Run does not exist')
            remote.connect()
            if run['thread'] not in remote.loaded:
                reply = remote.request('thread/resume',{'threadId':run['thread'],'excludeTurns':True})
                remote.loaded.add(run['thread'])
                # Resume never starts new model work. Saved turns might have ended with SSH.
                active = [t for t in reply['thread'].get('turns',[]) if t.get('status') == 'inProgress']
                if active: run['turn'] = active[-1]['id']
                if reply['thread']['status']['type'] != 'active': run['turn'] = None
            if run['turn']:
                try:
                    remote.request('turn/interrupt',{'threadId':run['thread'],'turnId':run['turn']})
                except RuntimeError as exc:
                    if 'not found' not in str(exc).lower() and 'no active turn' not in str(exc).lower(): raise
            self.store.save_run(run_id,run['project'],run['thread'],run['turn'],'interrupted',host)
            self.store.event(run_id,'operator_interrupt',{'host':host,'threadId':run['thread']})
            return {'run_id':run_id,'host':host,'status':'interrupted'}


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            self.connection.settimeout(65)
            raw = self.rfile.readline(1048577)
            if len(raw) > 1048576: raise ValueError('Request exceeds 1 MiB')
            request = json.loads(raw)
            result = self.server.bridge.call(request['action'],request.get('args',{}))
            reply = {'result':result}
        except Exception as exc:
            reply = {'error':f'{type(exc).__name__}: {exc}'}
        try: self.wfile.write((json.dumps(reply)+'\n').encode())
        except (BrokenPipeError,ConnectionResetError): pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def serve():
    os.umask(0o077)
    STATE.mkdir(mode=0o700,exist_ok=True)
    with open(STATE/'bridge.lock','w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('Bridge is already running')
        SOCKET.unlink(missing_ok=True)
        bridge = Bridge()
        server = Server(str(SOCKET),Handler)
        server.bridge = bridge
        (STATE/'bridge.pid').write_text(str(os.getpid()))
        def stop(*_): threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        try: server.serve_forever(poll_interval=0.2)
        finally:
            # Each SSH process has its own bounded shutdown waits. Close hosts
            # together so a slow connection does not multiply stop latency.
            with ThreadPoolExecutor(max_workers=len(bridge.remotes)) as pool:
                list(pool.map(lambda remote: remote.close(),bridge.remotes.values()))
            server.server_close()
            SOCKET.unlink(missing_ok=True)
            (STATE/'bridge.pid').unlink(missing_ok=True)
