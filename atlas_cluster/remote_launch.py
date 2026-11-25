"""Read a credential once over SSH stdin, then exec Codex. No credential file."""
import json
import os
from pathlib import Path


def main():
    # Read exactly one line without buffering subsequent JSON-RPC messages.
    line = bytearray()
    while not line.endswith(b'\n'):
        char = os.read(0, 1)
        if not char or len(line) > 65536:
            raise SystemExit('Missing or oversized bootstrap envelope')
        line.extend(char)
    bootstrap = json.loads(line)
    os.environ['AMSC_TOKEN'] = bootstrap['credential']
    del bootstrap, line
    os.environ['PATH'] = str(Path.home()/'.local/bin') + ':' + os.environ.get('PATH','/usr/bin:/bin')
    binary = str(Path.home()/'.local/bin/codex')
    os.execv(binary, [binary, 'app-server', '--stdio'])


if __name__ == '__main__': main()
