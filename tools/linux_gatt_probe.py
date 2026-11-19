"""Linux BLE control load for one explicitly selected WMP board; no auto-reconnect."""
import argparse,asyncio,json,struct,time
from pathlib import Path
from bleak import BleakClient,BleakScanner

CONTROL='aefb0002-2c51-4b0d-b100-ec5b00000001'
STATUS='aefb0003-2c51-4b0d-b100-ec5b00000001'
VOLUME='aefb0004-2c51-4b0d-b100-ec5b00000001'
async def run(a):
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    row=dict(address=a.address,started=time.time(),writes=0,notifications=0,connected=False,complete=False,errors=[])
    def save():out.write_text(json.dumps(row,indent=2)+'\n')
    def notice(_,data):row['notifications']+=1;row['last_status']=bytes(data).hex();row['last_notification']=time.time()
    try:
        device=await BleakScanner.find_device_by_address(a.address,timeout=20)
        if not device:raise RuntimeError('Selected WMP BLE advertiser not found')
        # The board is dual-mode on one address. Without this, BlueZ tries GATT
        # over the existing BR/EDR (A2DP) link, finds no services, and the
        # probe's disconnect then tears down the A2DP audio link as well.
        path=(getattr(device,'details',None) or {}).get('path') if isinstance(getattr(device,'details',None),dict) else None
        if path:
            import subprocess
            r=subprocess.run(['busctl','set-property','org.bluez',path,'org.bluez.Device1','PreferredBearer','s','le'],capture_output=True,text=True,timeout=10)
            row['preferred_bearer']='le' if r.returncode==0 else 'unset: '+(r.stderr.strip() or r.stdout.strip())
        async with BleakClient(device,timeout=30) as client:
            row['connected']=True;row['services']=[s.uuid for s in client.services];save()
            required={'00001800-0000-1000-8000-00805f9b34fb','00001801-0000-1000-8000-00805f9b34fb','0000180a-0000-1000-8000-00805f9b34fb','aefb0001-2c51-4b0d-b100-ec5b00000001'}
            if not required.issubset(row['services']):raise RuntimeError('The four expected GATT services were not discovered')
            current=await client.read_gatt_char(VOLUME);await client.write_gatt_char(VOLUME,current,response=True)
            await client.start_notify(STATUS,notice);start=time.monotonic()
            while time.monotonic()-start<a.seconds:
                if not client.is_connected:raise RuntimeError('BLE link disconnected')
                packet=bytes([0x10])+struct.pack('<II',a.run,row['writes'])
                await client.write_gatt_char(CONTROL,packet,response=True);row['writes']+=1;row['elapsed']=time.monotonic()-start;save()
                await asyncio.sleep(a.interval)
            await client.stop_notify(STATUS);row['complete']=True
    except Exception as error:row['errors'].append(repr(error))
    finally:row['connected']=False;row['finished']=time.time();save();print(json.dumps(row),flush=True)
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--address',required=True);p.add_argument('--seconds',type=int,default=7200);p.add_argument('--run',type=lambda x:int(x,16),default=0xa020a020);p.add_argument('--output',required=True);p.add_argument('--interval',type=float,default=0.5,help='seconds between acknowledged CONTROL writes (0.01..2)');a=p.parse_args()
    if a.address.upper() not in ['6C:C8:40:42:BF:66','08:B6:1F:B8:66:26']:p.error('Only the two admitted WMP boards')
    if not 10<=a.seconds<=10800:p.error('10..10800seconds')
    if not 0.01<=a.interval<=2:p.error('--interval 0.01..2 seconds')
    asyncio.run(run(a))
