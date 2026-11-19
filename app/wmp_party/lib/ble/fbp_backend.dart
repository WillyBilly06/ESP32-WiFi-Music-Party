/// flutter_blue_plus implementation of [BleBackend].
library;

import 'dart:async';

import 'package:flutter_blue_plus/flutter_blue_plus.dart';

import '../protocol/wmp_protocol.dart';
import 'ble_backend.dart';

class FbpBackend implements BleBackend {
  FbpBackend() {
    FlutterBluePlus.setLogLevel(LogLevel.warning, color: false);
  }

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
              if (_isWmp(r))
                DiscoveredSpeaker(
                  id: r.device.remoteId.str,
                  advName: _name(r),
                  rssi: r.rssi,
                  seenAt: r.timeStamp,
                ),
          ]);

  static String _name(ScanResult r) {
    final a = r.advertisementData.advName;
    if (a.isNotEmpty) return a;
    if (r.device.advName.isNotEmpty) return r.device.advName;
    return r.device.platformName;
  }

  static bool _isWmp(ScanResult r) => isWmpAdvertisement(
        name: _name(r),
        serviceUuids: r.advertisementData.serviceUuids.map((g) => g.str128),
      );

  @override
  Stream<bool> get isScanning => FlutterBluePlus.isScanning;

  @override
  Future<void> startScan({Duration timeout = kScanBurst}) async {
    if (FlutterBluePlus.isScanningNow) return;
    // No platform filter: the 128-bit UUID byte order is not yet confirmed and
    // the name may sit in the scan response, so filter in Dart instead.
    // High rate with live RSSI, but only in short bursts (see kScanBurst):
    // AppState stops the burst early when it has what it needs.
    await FlutterBluePlus.startScan(
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
  }) async {
    final device = BluetoothDevice.fromId(id);
    onStage?.call(ConnectStage.connecting);
    // Personal / non-commercial project use of flutter_blue_plus.
    // mtu: null -> no MTU exchange; every payload fits in the default 20 bytes
    // and skipping it saves a slow round trip.
    await device.connect(
      license: License.nonprofit,
      timeout: _connectTimeout,
      mtu: null,
    );
    try {
      // Low power before anything else: every fast-interval period on this
      // link takes air time from the phone's A2DP stream to the same speaker.
      await _requestLowPower(device);
      onStage?.call(ConnectStage.discovering);
      // Exactly one service discovery per connection. flutter_blue_plus keeps
      // `servicesList` after a disconnect, but Android opens a new GATT client
      // for every connection and needs one discovery on it before any
      // read/write, so the list is only trusted within this connection.
      // subscribeToServicesChanged: false avoids an extra CCCD write.
      final services = await device.discoverServices(
        subscribeToServicesChanged: false,
        timeout: _discoverTimeoutS,
      );
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
        throw SpeakerConnectException(
            'This device has no Music Party control service');
      }
      final chars = <WmpChar, BluetoothCharacteristic>{};
      for (final c in WmpChar.values) {
        final want = WmpUuids.char(c, layout);
        for (final ch in control.characteristics) {
          if (ch.uuid.str128 == want) chars[c] = ch;
        }
      }
      for (final need in [WmpChar.status, WmpChar.volume]) {
        if (!chars.containsKey(need)) {
          throw SpeakerConnectException(
              'Speaker firmware is missing the ${need.name} characteristic');
        }
      }
      BluetoothCharacteristic? fw;
      if (info != null) {
        for (final ch in info.characteristics) {
          if (ch.uuid.str128 == WmpUuids.firmwareRevision) fw = ch;
        }
      }
      return _FbpLink(device, layout, chars, fw, discoveryCount: 1);
    } catch (_) {
      await device.disconnect().catchError((_) {});
      rethrow;
    }
  }
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
  _FbpLink(this._device, this.layout, this._chars, this._fw, {required this.discoveryCount}) {
    _connSub = _device.connectionState.listen((s) {
      if (s == BluetoothConnectionState.disconnected) _markDropped();
    });
  }

  final BluetoothDevice _device;
  @override
  final UuidLayout layout;
  final Map<WmpChar, BluetoothCharacteristic> _chars;
  final BluetoothCharacteristic? _fw;
  @override
  final int discoveryCount;
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
    final fw = _fw;
    if (fw == null) return null;
    final bytes = await _serial(() => fw.read(timeout: FbpBackend._opTimeoutS));
    return String.fromCharCodes(bytes.where((b) => b != 0)).trim();
  }

  @override
  Future<void> disconnect() async {
    await _connSub?.cancel();
    await _device.disconnect().catchError((_) {});
    _markDropped();
    await _closed.close();
  }
}
