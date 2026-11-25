"""Explicit bounded live integration test; uses the configured MAG provider."""
import json
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from teamctl import call
from atlas_cluster.settings import load_settings, coordinator_mailbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--host',default='dell1')
    parser.add_argument('--peer',help='Also exchange evidence with this host running the same smoke run')
    parser.add_argument('--compute',action='store_true',help='Also test native sandboxed Python and credential exclusion.')
    args = parser.parse_args()
    run = args.run_id
    host = args.host
    mailbox = coordinator_mailbox(host,load_settings(ROOT)['default_host'])
    laptop = 'laptop-smoke:'+host
    worker = host+':smoke-worker'
    artifact = host+':bridge-smoke'
    def invoke(action,**args): return call(ROOT/'.state/bridge.sock',action,{'run_id':run,**args})
    prompt = f'''This is only a bounded infrastructure smoke test. Do not read or change research files. You are explicitly authorized to spawn exactly one native subagent. The run_id is {run}; you are remote-coordinator.
Send REMOTE_READY to laptop-coordinator using team_message. Spawn exactly one native subagent with identity remote-smoke-worker and these instructions: use the atlas_team MCP to claim task worker-smoke for 600 seconds, publish bridge-smoke with content REMOTE_REVISION_1 at expected_revision=0, send WORKER_READY to laptop-coordinator, then poll its own inbox (wait_seconds=30, at most 4 waits) for CHECK_REVISION_2. When received, read bridge-smoke, verify LAPTOP_REVISION_2, acknowledge the message, finish its task with its claim token, send WORKER_ROUNDTRIP_OK to laptop-coordinator, and return. If coordination fails or times out report the exact failure and end. Do not delegate further.
Meanwhile check your own inbox for STEER_NONCE_9184. Acknowledge it and send STEERING_OK to laptop-coordinator. A turn/steer notification and its inbox copy are the same message; process it only once. Wait for the worker using native tools, collect its result and send SMOKE_COMPLETE to laptop-coordinator only if both steering and the worker roundtrip succeeded. End the turn; do not start any scientific work.'''
    if args.compute:
        prompt += '''\nBefore REMOTE_READY, use your native terminal/exec tool with login=false to execute exactly this harmless read-only computation:
python3 -c "import os; assert 'AMSC_TOKEN' not in os.environ, 'credential unexpectedly inherited'; print(sum(range(1,10001)))"
Verify exit status zero and output 50005000, then send COMPUTE_OK to laptop-coordinator. Do not print any environment values or credentials. If the command fails, report COMPUTE_FAILED with the error and end without delegating. Do not bypass the sandbox.'''
    prompt = prompt.replace('remote-coordinator',mailbox).replace('laptop-coordinator',laptop).replace('remote-smoke-worker',worker).replace('worker-smoke',host+':worker-smoke').replace('bridge-smoke',artifact)
    if args.peer:
        prompt += f'''\nAdditional worker requirement: after publishing {artifact}, send PEER_READY to {args.peer}:smoke-worker. In the worker inbox loop also wait for PEER_READY from that peer; upon receipt read {args.peer}:bridge-smoke and verify its content is REMOTE_REVISION_1 or LAPTOP_REVISION_2, acknowledge the message and send CROSS_HOST_OK to {laptop}. Allow at most 8 inbox waits of 30 seconds. Require both the laptop revision roundtrip AND peer evidence exchange before finishing. Pass this full extra requirement to your one worker. Do not create another worker.'''
    result = invoke('team_dispatch',prompt=prompt,host=host)
    print(json.dumps({'dispatched':result}),flush=True)
    steer = invoke('team_message',sender=laptop,recipient=mailbox,
                   body=f'STEER_NONCE_9184: acknowledge and send STEERING_OK to {laptop}.',steer=True)
    print(json.dumps({'steering':steer}),flush=True)
    seen=[];updated=False;deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        inbox=invoke('team_inbox',recipient=laptop,wait_seconds=10)
        for message in inbox['messages']:
            print(json.dumps({'from':message['sender'],'body':message['body']}),flush=True)
            seen.append(message['body'])
            if 'WORKER_READY' in message['body'] and not updated:
                first=invoke('team_artifact_get',artifact_id=artifact)
                assert first['content']=='REMOTE_REVISION_1',first
                second=invoke('team_artifact_put',artifact_id=artifact,author=laptop,
                              content='LAPTOP_REVISION_2',expected_revision=first['revision'])
                invoke('team_message',sender=laptop,recipient=worker,
                       body='CHECK_REVISION_2: verify LAPTOP_REVISION_2 and finish your smoke task.')
                print(json.dumps({'artifact_roundtrip':second}),flush=True)
                updated=True
            invoke('team_ack',recipient=laptop,message_ids=[message['id']])
        status=invoke('team_status',host=host)
        current=next(r for r in status['runs'] if r['run']==run and r['host']==host)
        if current['status'] not in ('running','dispatching'):
            expected=['STEERING_OK','WORKER_ROUNDTRIP_OK','SMOKE_COMPLETE']
            if args.compute: expected.append('COMPUTE_OK')
            if args.peer: expected.append('CROSS_HOST_OK')
            success=updated and all(any(key in m for m in seen) for key in expected) and current['status']=='completed'
            report={'success':success,'run_id':run,'host':host,'steering':steer,'messages':seen,'status':status}
            (ROOT/'.state'/f'{run}-{host}-report.json').write_text(json.dumps(report,indent=2)+'\n')
            print(json.dumps({'success':success,'status':current['status']}),flush=True)
            if not success: raise SystemExit(1)
            return
    invoke('team_interrupt',host=host)
    raise SystemExit('Smoke test timed out and coordinator was interrupted; inspect child state.')


if __name__=='__main__':main()
