/// flutter_blue_plus implementation of [BleBackend].
library;

import 'dart:async';

import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../protocol/advert.dart';
import '../protocol/wmp_protocol.dart';
import 'ble_backend.dart';

class FbpBackend implements BleBackend {
  FbpBackend() {
    FlutterBluePlus.setLogLevel(LogLevel.warning, color: false);
  }

  /// MTU requested once per connection, before service discovery.
  static const kRequestedMtu = 517; // ATT maximum; the speaker accepts up to 1017

  // The speaker requests a 60-75 ms connection interval, so a GATT round trip
  // is ~100-150 ms. Timeouts leave headroom for a busy radio (A2DP + Wi-Fi).
  static const _connectTimeout = Duration(seconds: 15);
  static const _discoverTimeoutS = 20;
  static const _opTimeoutS = 6;

  @override
  AdapterStatus get adapterNow => _map(FlutterBluePlus.adapterStateNow);

  @override
  Stream<AdapterStatus> get adapterStatus =>
      FlutterBluePlus.adapterState.map(_map);

  static AdapterStatus _map(BluetoothAdapterState s) => switch (s) {
        BluetoothAdapterState.unknown => AdapterStatus.unknown,
        BluetoothAdapterState.unavailable => AdapterStatus.unavailable,
        BluetoothAdapterState.unauthorized => AdapterStatus.unauthorized,
        BluetoothAdapterState.turningOn => AdapterStatus.turningOn,
        BluetoothAdapterState.on => AdapterStatus.on,
        BluetoothAdapterState.turningOff => AdapterStatus.turningOff,
        BluetoothAdapterState.off => AdapterStatus.off,
      };

  @override
  Future<void> requestTurnOn() => FlutterBluePlus.turnOn();

  @override
  Stream<List<DiscoveredSpeaker>> get scanResults =>
      FlutterBluePlus.scanResults.map((list) => [
            for (final r in list)
              if (_advert(r) case final advert?)
                DiscoveredSpeaker(
                  id: r.device.remoteId.str,
                  advName: _name(r),
                  rssi: r.rssi,
                  seenAt: r.timeStamp,
                  advert: advert,
                ),
          ]);

  static String _name(ScanResult r) {
    final a = r.advertisementData.advName;
    if (a.isNotEmpty) return a;
    if (r.device.advName.isNotEmpty) return r.device.advName;
    return r.device.platformName;
  }

  /// Service UUID + manufacturer data check; the name is never used.
  static WmpAdvert? _advert(ScanResult r) => parseWmpAdvert(
        serviceUuids: r.advertisementData.serviceUuids.map((g) => g.str128),
        manufacturerData: r.advertisementData.manufacturerData,
      );

  @override
  Stream<bool> get isScanning => FlutterBluePlus.isScanning;

  @override
  Future<void> startScan({Duration timeout = kScanBurst}) async {
    if (FlutterBluePlus.isScanningNow) return;
    // Platform filter on the control service UUID (the advert carries the
    // complete 128-bit UUID), then parseWmpAdvert checks the manufacturer
    // data in Dart. High rate with live RSSI, but only in short bursts (see
    // kScanBurst): AppState stops the burst early when it has what it needs.
    await FlutterBluePlus.startScan(
      withServices: [Guid(WmpUuids.service)],
      timeout: timeout > kScanBurst ? kScanBurst : timeout,
      androidScanMode: AndroidScanMode.lowLatency,
      continuousUpdates: true,
    );
  }

  @override
  Future<void> stopScan() => FlutterBluePlus.stopScan();

  @override
  Future<SpeakerLink> connect(
    String id, {
    void Function(ConnectStage stage)? onStage,
    void Function(String line)? onTrace,
  }) async {
    final device = BluetoothDevice.fromId(id);
    void trace(String s) => onTrace?.call(s);
    onStage?.call(ConnectStage.connecting);
    // Personal / non-commercial project use of flutter_blue_plus.
    // mtu: null -> flutter_blue_plus does no MTU exchange of its own; the one
    // exchange is requested explicitly below, before discovery.
    try {
      await device.connect(
        license: License.nonprofit,
        timeout: _connectTimeout,
        mtu: null,
      );
    } catch (e) {
      trace('connect failed: $e');
      throw ConnectStepException('Connection', e);
    }
    trace('link up');
    // Why the link went down, if it did during setup (flutter_blue_plus keeps
    // the last disconnect reason per device).
    String dropReason() {
      final r = device.disconnectReason;
      return r == null ? 'the link dropped' : 'the link dropped: ${r.description ?? 'code ${r.code}'}';
    }

    try {
      // Low power before anything else: every fast-interval period on this
      // link takes air time from the phone's A2DP stream to the same speaker.
      await _requestLowPower(device);
      // One MTU exchange before discovery: Android forces a 7.5 ms interval
      // for the whole discovery, and at the default 23-byte MTU the 128-bit
      // characteristics take ~10 responses instead of 1-2. connect() is called
      // with mtu: null so this is the only exchange. Failure is not fatal.
      try {
        final mtu = await device.requestMtu(kRequestedMtu);
        trace('MTU $mtu');
      } catch (e) {
        trace('MTU request failed (kept default): $e');
      }
      if (!device.isConnected) {
        trace(dropReason());
        throw ConnectStepException('Connection', dropReason());
      }
      onStage?.call(ConnectStage.discovering);
      // One service discovery per connection, plus at most one more after
      // clearing Android's GATT cache when the first result lacks something
      // required (a cache recorded for older speaker firmware). flutter_blue_plus
      // keeps `servicesList` after a disconnect, but Android opens a new GATT
      // client for every connection and needs a discovery on it before any
      // read/write, so the list is only trusted within this connection.
      var discoveries = 0;
      final _Gatt gatt;
      try {
        gatt = await discoverWithCacheRecovery<List<BluetoothService>, _Gatt>(
          discover: () {
            discoveries++;
            return _discover(device, trace);
          },
          resolve: _resolve,
          clearCache: device.clearGattCache,
          serviceCount: (s) => s.length,
          onTrace: trace,
        );
      } on SpeakerConnectException {
        rethrow;
      } catch (e) {
        final why = device.isConnected ? e : dropReason();
        trace('discovery failed: $why');
        throw ConnectStepException('Service discovery', why);
      }
      return _FbpLink(device, gatt, discoveryCount: discoveries);
    } catch (_) {
      await device.disconnect().catchError((_) {});
      rethrow;
    }
  }
}

/// subscribeToServicesChanged: false avoids an extra CCCD write. Android can
/// refuse the request while its own stack is still busy right after
/// connecting; that case is retried instead of giving up.
Future<List<BluetoothService>> _discover(BluetoothDevice device, void Function(String) trace) =>
    retryWhenBusy(
      () => device.discoverServices(
        subscribeToServicesChanged: false,
        timeout: FbpBackend._discoverTimeoutS,
      ),
      stillConnected: () => device.isConnected,
      onTrace: trace,
    );

/// What the app uses from one discovery.
class _Gatt {
  _Gatt(this.layout, this.chars, this.fw);
  final UuidLayout layout;
  final Map<WmpChar, BluetoothCharacteristic> chars;
  final BluetoothCharacteristic? fw;
}

GattResolution<_Gatt> _resolve(List<BluetoothService> services) {
  UuidLayout? layout;
  BluetoothService? control;
  BluetoothService? info;
  for (final s in services) {
    final l = WmpUuids.layoutOfService(s.uuid.str128);
    if (l != null) {
      layout = l;
      control = s;
    }
    if (s.uuid.str128 == WmpUuids.deviceInfoService) info = s;
  }
  if (control == null || layout == null) {
    return const GattResolution(null, missing: 'the control service');
  }
  final chars = <WmpChar, BluetoothCharacteristic>{};
  for (final c in WmpChar.values) {
    final want = WmpUuids.char(c, layout);
    for (final ch in control.characteristics) {
      if (ch.uuid.str128 == want) chars[c] = ch;
    }
  }
  BluetoothCharacteristic? fw;
  if (info != null) {
    for (final ch in info.characteristics) {
      if (ch.uuid.str128 == WmpUuids.firmwareRevision) fw = ch;
    }
  }
  final missing = [for (final c in kRequiredChars) if (!chars.containsKey(c)) c.name];
  // Without status/volume nothing works: no link. Without identity the
  // controller reports a firmware update.
  final usable = chars.containsKey(WmpChar.status) && chars.containsKey(WmpChar.volume);
  return GattResolution(usable ? _Gatt(layout, chars, fw) : null,
      missing: missing.isEmpty ? null : missing.join(', '));
}

Future<void> _requestLowPower(BluetoothDevice device) async {
  try {
    await device.requestConnectionPriority(
        connectionPriorityRequest: ConnectionPriority.lowPower);
  } catch (_) {
    // Best effort: the speaker also asks for 60 ms itself.
  }
}

class _FbpLink implements SpeakerLink {
  _FbpLink(this._device, _Gatt gatt, {required this.discoveryCount})
      : layout = gatt.layout,
        _chars = gatt.chars,
        _fw = gatt.fw {
    _connSub = _device.connectionState.listen((s) {
      if (s == BluetoothConnectionState.disconnected) _markDropped();
    });
  }

  final BluetoothDevice _device;
  @override
  UuidLayout layout;
  Map<WmpChar, BluetoothCharacteristic> _chars;
  BluetoothCharacteristic? _fw;
  @override
  int discoveryCount;
  final _closed = StreamController<bool>.broadcast();
  StreamSubscription<BluetoothConnectionState>? _connSub;
  bool _dropped = false;

  void _markDropped() {
    if (_dropped) return;
    _dropped = true;
    _closed.add(false);
  }

  // Android allows one outstanding GATT operation per device; keep ours in
  // order so read-backs follow their writes.
  Future<void> _queue = Future.value();

  Future<T> _serial<T>(Future<T> Function() op) {
    final c = Completer<T>();
    _queue = _queue.then((_) async {
      try {
        c.complete(await op());
      } catch (e, st) {
        c.completeError(e, st);
      }
    });
    return c.future;
  }

  @override
  String get id => _device.remoteId.str;

  @override
  Stream<bool> get connection => _closed.stream;

  @override
  bool has(WmpChar c) => _chars.containsKey(c);

  @override
  Future<bool> refreshServices({void Function(String line)? onTrace}) => _serial(() async {
        try {
          await _device.clearGattCache();
          onTrace?.call('cleared GATT cache');
        } catch (e) {
          onTrace?.call('clearing GATT cache failed: $e');
        }
        final services = await _discover(_device, (l) => onTrace?.call(l));
        discoveryCount++;
        onTrace?.call('rediscovered ${services.length} services');
        final r = _resolve(services);
        final g = r.value;
        if (g != null) {
          layout = g.layout;
          _chars = g.chars;
          _fw = g.fw;
        }
        if (r.missing != null) onTrace?.call('still missing ${r.missing}');
        return r.missing == null;
      });

  BluetoothCharacteristic _char(WmpChar c) {
    final ch = _chars[c];
    if (ch == null) {
      throw SpeakerConnectException('Speaker has no ${c.name} characteristic');
    }
    return ch;
  }

  @override
  Future<List<int>> read(WmpChar c) =>
      _serial(() => _char(c).read(timeout: FbpBackend._opTimeoutS));

  @override
  Future<void> write(WmpChar c, List<int> value) => _serial(
      () => _char(c).write(value, timeout: FbpBackend._opTimeoutS));

  @override
  Future<Stream<List<int>>> subscribe(WmpChar c) async {
    final ch = _char(c);
    final stream = ch.onValueReceived;
    await _serial(() => ch.setNotifyValue(true, timeout: FbpBackend._opTimeoutS));
    return stream;
  }

  @override
  Future<void> relaxConnection() => _serial(() => _requestLowPower(_device));

  @override
  Future<String?> readFirmwareRevision() async {
    // Current firmware: aefb000a in the control service. Older firmware:
    // Device Information 2a26. Neither is required.
    final fw = _chars[WmpChar.firmware] ?? _fw;
    if (fw == null) return null;
    final bytes = await _serial(() => fw.read(timeout: FbpBackend._opTimeoutS));
    return decodeFirmwareRevision(bytes);
  }

  @override
  Future<void> disconnect() async {
    await _connSub?.cancel();
    await _device.disconnect().catchError((_) {});
    _markDropped();
    await _closed.close();
  }
}
