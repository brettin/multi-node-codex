#!/usr/bin/env python3
"""CLI and MCP entry point. Run `python3 teamctl.py --help`."""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def call(socket_path, action, args):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
        sock.settimeout(60)
        try: sock.connect(str(socket_path))
        except OSError as exc:
            raise RuntimeError('Bridge unavailable. On the laptop run: python3 teamctl.py start. Check SSH and MAG environment.') from exc
        sock.sendall((json.dumps({'action':action,'args':args})+'\n').encode())
        with sock.makefile('rb') as stream:
            line = stream.readline(8388609)
        if not line or len(line)>8388608: raise RuntimeError('Invalid bridge response')
        response = json.loads(line)
        if 'error' in response: raise RuntimeError(response['error'])
        return response['result']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',default=str(ROOT/'.state/bridge.sock'))
    sub = parser.add_subparsers(dest='command',required=True)
    sub.add_parser('serve');sub.add_parser('start');sub.add_parser('stop')
    for action in ('connect','disconnect','status'):
        host_command = sub.add_parser(action)
        host_command.add_argument('--host')
    mcp = sub.add_parser('mcp');mcp.add_argument('--worker',action='store_true')
    req = sub.add_parser('call');req.add_argument('action');req.add_argument('json',nargs='?',default='{}')
    req.add_argument('--file',help='Read arguments from JSON file instead of a command-line literal; use - for stdin.')
    args = parser.parse_args()
    invoke = lambda action,data: call(args.socket,action,data)
    if args.command == 'serve':
        from atlas_cluster.bridge import serve
        serve();return
    if args.command == 'mcp':
        from atlas_cluster.mcp import serve_mcp
        serve_mcp(invoke,args.worker);return
    if args.command == 'start':
        try:
            print(json.dumps(invoke('team_status',{}),indent=2));return
        except RuntimeError: pass
        if not os.environ.get('AMSC_TOKEN'): raise RuntimeError('AMSC_TOKEN is not available in this shell; no credential is saved by the bridge.')
        state = ROOT/'.state';state.mkdir(mode=0o700,exist_ok=True)
        os.umask(0o077)
        with open(state/'bridge.log','a') as log:
            subprocess.Popen([sys.executable,str(ROOT/'teamctl.py'),'serve'],
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,cwd=ROOT)
        for _ in range(50):
            time.sleep(.1)
            try:
                print(json.dumps(invoke('team_status',{}),indent=2));return
            except RuntimeError: pass
        raise RuntimeError('Bridge did not start; inspect .state/bridge.log')
    if args.command == 'stop':
        pidfile = ROOT/'.state/bridge.pid'
        if not pidfile.exists(): print('Bridge already stopped');return
        pid = int(pidfile.read_text())
        # Verify the process command before signaling a potentially recycled PID.
        command = subprocess.check_output(['ps','-p',str(pid),'-o','command='],text=True).strip()
        if str(ROOT/'teamctl.py') not in command or ' serve' not in command:
            raise RuntimeError('PID ownership mismatch; refusing to signal it')
        os.kill(pid,signal.SIGTERM)
        for _ in range(550):
            if not pidfile.exists(): print('Bridge stopped; SSH app server disconnected');return
            time.sleep(.1)
        raise RuntimeError('Stop is still pending; inspect the bridge process')
    if args.command == 'status': value = invoke('team_status',{'host':args.host} if args.host else {})
    elif args.command in ('connect','disconnect'): value = invoke('_'+args.command,{'host':args.host} if args.host else {})
    else:
        raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text() if args.file else args.json
        value = invoke(args.action,json.loads(raw))
    print(json.dumps(value,indent=2))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        sys.exit(1)
