"""Pinned Codex App Server JSON-RPC client over SSH stdio."""
import json
import os
import queue
import shlex
import subprocess
import threading
from pathlib import Path


class Remote:
    def __init__(self, settings, socket_path, state, store):
        self.settings, self.socket_path, self.state, self.store = settings, socket_path, state, store
        self.process = None
        self.pending = {}
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.serial = 0
        self.loaded = set()
        self.last_error = None
        self.reader = None

    def connected(self):
        return self.process is not None and self.process.poll() is None

    def connect(self):
        with self.connect_lock:
            if self.connected(): return
            if self.reader and self.reader.is_alive():
                self.reader.join(timeout=3)
                if self.reader.is_alive(): raise RuntimeError('Previous SSH reader is still closing; retry after checking status')
            credential = os.environ.get(self.settings['credential_env'])
            if not credential:
                raise RuntimeError('AMSC_TOKEN is absent. Restart the bridge from an authenticated laptop shell.')
            root = self.settings['remote_root']
            self._cancel_forward()
            # Respect the user's SSH configuration, including its authenticated
            # multiplexed connection. Only remove our own stale socket inode.
            subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',self.settings['host'],
                            f'if test -S {shlex.quote(root+"/bridge.sock")}; then rm {shlex.quote(root+"/bridge.sock")}; fi'],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=20)
            cmd = ['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10',
                   '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',
                   '-o','ExitOnForwardFailure=yes','-o','StreamLocalBindUnlink=yes',
                   '-o','StreamLocalBindMask=0177',
                   '-R',f'{root}/bridge.sock:{self.socket_path}', self.settings['host'],
                   f'python3 {shlex.quote(root+"/code/atlas_cluster/remote_launch.py")}']
            self.loaded.clear()
            self.last_error = None
            with open(self.state/f'ssh-{self.settings["host"]}-stderr.log', 'a') as err:
                process = subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err)
            self.process = process
            process.stdin.write((json.dumps({'credential':credential})+'\n').encode())
            process.stdin.flush()
            del credential
            self.reader = threading.Thread(target=self._reader,args=(process,),daemon=True)
            self.reader.start()
            try:
                self.request('initialize',{'clientInfo':{'name':'atlas_bridge','version':'0.2.0'},
                                          'capabilities':{'experimentalApi':True}})
                self.send({'method':'initialized'})
            except Exception:
                self.close()
                raise

    def send(self, msg):
        with self.lock:
            if not self.connected(): raise RuntimeError('SSH app server disconnected')
            self.process.stdin.write((json.dumps(msg)+'\n').encode())
            self.process.stdin.flush()

    def request(self, method, params, timeout=45):
        with self.lock:
            self.serial += 1
            request_id = self.serial
            response = queue.Queue(maxsize=1)
            self.pending[request_id] = response
        try:
            self.send({'id':request_id,'method':method,'params':params})
            try: value = response.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f'{method} response timed out. Outcome may be unknown; inspect status before retrying.')
            if 'error' in value: raise RuntimeError(json.dumps(value['error']))
            return value.get('result', {})
        finally:
            with self.lock: self.pending.pop(request_id,None)

    def _reader(self, process):
        try:
            for line in process.stdout:
                msg = json.loads(line)
                if 'id' in msg and 'method' not in msg:
                    with self.lock: target = self.pending.get(msg['id'])
                    if target: target.put(msg)
                elif 'id' in msg:
                    # No implicit escalation or fabricated user replies.
                    method = msg.get('method','')
                    params = msg.get('params',{})
                    run = self.store.run_for_thread(params.get('threadId'),self.settings['host'])
                    self.store.event(run,'approval_or_input_blocked',{'host':self.settings['host'],'method':method,'threadId':params.get('threadId')})
                    if method in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):
                        self.send({'id':msg['id'],'result':{'decision':'decline'}})
                    else:
                        self.send({'id':msg['id'],'error':{'code':-32601,'message':'Interactive approval/input not supported by the bridge; ask the laptop coordinator.'}})
                else:
                    self._event(msg)
        except Exception as exc:
            self.last_error = f'{type(exc).__name__}: {exc}'
        finally:
            self.last_error = self.last_error or 'SSH app server connection closed'
            with self.lock:
                for target in self.pending.values():
                    try: target.put_nowait({'error':{'message':self.last_error}})
                    except queue.Full: pass
            if process.poll() is None: process.terminate()
            # A disconnected in-progress turn is indeterminate, never silently restarted.
            self.store.disconnect_host(self.settings['host'])

    def _event(self, msg):
        method, params = msg.get('method',''), msg.get('params',{})
        thread = params.get('threadId')
        host = self.settings['host']
        run = self.store.run_for_thread(thread,host)
        if method in ('turn/started','turn/completed'):
            turn = params['turn']
            status = 'running' if method == 'turn/started' else turn.get('status','completed')
            self.store.turn_event(thread,turn['id'],status,host)
            self.store.event(run,method,{'host':host,'threadId':thread,'turnId':turn['id'],'status':status,'error':turn.get('error')})
        elif method == 'item/completed':
            item = params.get('item',{})
            kind = item.get('type','')
            data = {'host':host,'threadId':thread,'turnId':params.get('turnId'),'type':kind}
            if kind == 'agentMessage':
                data['text'] = item.get('text','')[:32768]
            elif kind == 'commandExecution':
                data['exitCode'] = item.get('exitCode')
                data['status'] = item.get('status')
            elif 'collab' in kind.lower() or kind == 'subAgentActivity':
                data['details'] = item
            else:
                data['status'] = item.get('status')
            self.store.event(run,method,data)
        elif method == 'error':
            self.store.event(run,method,{**params,'host':host})

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=10)
            except subprocess.TimeoutExpired: self.process.kill()
        if self.reader and self.reader is not threading.current_thread(): self.reader.join(timeout=3)
        self._cancel_forward()

    def active_threads(self):
        active, cursor = [], None
        for _ in range(100):
            # History lists hide attached threads and this pinned server labels
            # its coordinator source as vscode. Inventory loaded sessions
            # directly so neither detail can hide active native children.
            params = {'limit':100}
            if cursor: params['cursor'] = cursor
            result = self.request('thread/loaded/list',params)
            for thread_id in result['data']:
                t = self.request('thread/read',{'threadId':thread_id,'includeTurns':False})['thread']
                if t['status']['type']=='active':
                    active.append({'thread_id':t['id'],'parent_thread_id':t.get('parentThreadId'),'status':t['status']})
            cursor = result.get('nextCursor')
            if not cursor: return active
        raise RuntimeError('Thread inventory exceeded 100 pages; refusing an uncertain capacity check')

    def _cancel_forward(self):
        try:
            subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','-O','cancel',
                            '-R',f'{self.settings["remote_root"]}/bridge.sock:{self.socket_path}',self.settings['host']],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        except subprocess.TimeoutExpired:
            pass


def user_input(text):
    return [{'type':'text','text':text}]
