"""Resolve host-specific settings while accepting the original dell1 layout."""
import json
from pathlib import Path
from .store import name


def load_settings(root):
    settings = json.loads((Path(root)/'settings.json').read_text())
    default = settings.get('default_host', settings.get('host','dell1'))
    if 'hosts' not in settings:
        settings['hosts'] = {default:{k:settings[k] for k in ('remote_root','remote_codex','projects') if k in settings}}
    if default not in settings['hosts']:
        raise ValueError('default_host must be configured in hosts')
    settings['default_host'] = default
    for host in settings['hosts']: name(host)
    return settings


def for_host(settings, host=None):
    host = host or settings['default_host']
    if host not in settings['hosts']: raise ValueError(f'Unknown host: {host}')
    return {**{k:v for k,v in settings.items() if k != 'hosts'}, **settings['hosts'][host], 'host':host}


def coordinator_mailbox(host, default_host='dell1'):
    return 'remote-coordinator' if host == default_host else f'remote-coordinator:{host}'
