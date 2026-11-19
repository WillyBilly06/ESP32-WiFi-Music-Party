"""Check a speaker's BLE identity gate from this PC (needs `pip install bleak`).

    python tools/ble_identity_check.py            # first WMP speaker found
    python tools/ble_identity_check.py --name BF64

1. Scans for the control service and checks the `WM` manufacturer data.
2. Before the handshake: a volume write must fail.
3. Wrong proof: must fail.
4. Correct handshake (public default key): the speaker's tag must verify, then the same write
   must succeed. The volume written is the value read back, so nothing changes audibly.
5. --idle: connect without proving and report whether the speaker drops the link in ~10 s.
"""
import argparse, asyncio, hashlib, hmac, os, time
from bleak import BleakClient, BleakScanner

BASE = '-2c51-4b0d-b100-ec5b00000001'
SERVICE = 'aefb0001' + BASE
VOLUME = 'aefb0004' + BASE
IDENTITY = 'aefb0009' + BASE
KEY = b'WMP development identity key v01'  # firmware/app/main/identity_key.h


def tag(who, first, second):
    return hmac.new(KEY, b'WMP-' + who + first + second, hashlib.sha256).digest()[:10]


async def find(name):
    found = await BleakScanner.discover(timeout=8, return_adv=True, service_uuids=[SERVICE])
    for device, adv in found.values():
        data = adv.manufacturer_data.get(0xFFFF, b'')
        ok = len(data) >= 5 and data[:2] == b'WM' and data[2] == 1 and data[3] == 1
        print(f'seen {device.address} {adv.local_name} manufacturer={data.hex()} wmp={ok}')
        if ok and (not name or (adv.local_name or '').endswith(name)):
            return device
    raise SystemExit('no WMP speaker found')


async def expect_fail(coro, what):
    try:
        await coro
    except Exception as error:  # ATT error surfaces as an exception
        print(f'PASS {what} refused: {str(error)[:80]}')
        return True
    print(f'FAIL {what} was accepted')
    return False


async def main():
    p = argparse.ArgumentParser()
    p.add_argument('--name', default='')
    p.add_argument('--idle', action='store_true')
    a = p.parse_args()
    device = await find(a.name)
    async with BleakClient(device) as client:
        volume = await client.read_gatt_char(VOLUME)
        results = []
        if a.idle:
            start = time.monotonic()
            while client.is_connected and time.monotonic() - start < 20:
                await asyncio.sleep(0.5)
            dropped = not client.is_connected
            print(('PASS' if dropped else 'FAIL') + f' unproven link dropped={dropped} after {time.monotonic() - start:.1f} s')
            return
        results.append(await expect_fail(client.write_gatt_char(VOLUME, volume, response=True), 'write before proof'))
        nonce = os.urandom(8)
        await client.write_gatt_char(IDENTITY, b'\x01' + nonce, response=True)
        reply = await client.read_gatt_char(IDENTITY)
        speaker_nonce = bytes(reply[2:10])
        results.append(await expect_fail(client.write_gatt_char(IDENTITY, b'\x02' + bytes(10), response=True), 'wrong proof'))
        # a fresh challenge after the failed attempt
        await client.write_gatt_char(IDENTITY, b'\x01' + nonce, response=True)
        reply = await client.read_gatt_char(IDENTITY)
        speaker_nonce = bytes(reply[2:10])
        good = reply[0] == 1 and hmac.compare_digest(bytes(reply[10:20]), tag(b'S', nonce, speaker_nonce))
        print(('PASS' if good else 'FAIL') + f' speaker proof key_id={reply[1]}')
        results.append(good)
        await client.write_gatt_char(IDENTITY, b'\x02' + tag(b'A', speaker_nonce, nonce), response=True)
        state = await client.read_gatt_char(IDENTITY)
        results.append(state[0] == 2)
        print(('PASS' if state[0] == 2 else 'FAIL') + f' app proof accepted state={state[0]}')
        try:
            await client.write_gatt_char(VOLUME, volume, response=True)
            print(f'PASS write after proof (volume {volume[0]}%)')
            results.append(True)
        except Exception as error:
            print(f'FAIL write after proof: {error}')
            results.append(False)
        print('ALL PASS' if all(results) else 'SOME FAILED')


asyncio.run(main())
