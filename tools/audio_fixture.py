"""Explicit source selection; a Linux replacement never grants Samsung access."""
import json
from pathlib import Path
from board_io import ROOT
from adb_local import SERIAL,SAMSUNG,require_testing

FIXTURES=('redmagic-samsung','redmagic-linux')

def require(name,root=ROOT):
    if name not in FIXTURES:raise ValueError('Unknown audio fixture')
    require_testing(SERIAL)
    if name=='redmagic-samsung':
        require_testing(SAMSUNG)
        return dict(name=name,sources=[SERIAL,SAMSUNG],description='Actual REDMAGIC and Samsung A2DP streams plus BLE; Samsung media20 percent',two_real_phones=True)
    r=json.loads((Path(root)/'hardware/admitted-linux-host.json').read_text())
    if not r.get('testing_enabled') or r.get('bluetooth_controller','').upper()!='E8:47:3A:EC:51:9A':raise PermissionError('Linux replacement host/controller is not admitted for current testing')
    return dict(name=name,sources=[SERIAL,'linux'],description='Actual REDMAGIC phone and identity-admitted Linux A2DP music streams plus BLE; Samsung excluded',two_real_phones=False,linux_controller=r['bluetooth_controller'])

def check_sources(fixture,sources):
    if set(sources)!=set(fixture['sources']) or len(sources)!=2:raise RuntimeError('Connected audio sources do not match selected fixture '+fixture['name'])
