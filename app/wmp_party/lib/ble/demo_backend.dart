/// In-memory speakers that follow the BLE contract. Used by widget tests and by
/// the opt-in demo build (`--dart-define=WMP_DEMO=true`). Never used against
/// real hardware.
library;

import 'dart:async';
import 'dart:convert';

import '../protocol/advert.dart';
import '../protocol/identity.dart';
import '../protocol/wmp_protocol.dart';
import 'ble_backend.dart';
import 'identity_key.dart';

class DemoSpeaker {
  DemoSpeaker({
    required this.id,
    required this.advName,
    this.rssi = -55,
    this.role = SpeakerRole.idle,
    this.phoneConnected = false,
    this.streaming = false,
    this.following = false,
    this.sampleRateHz = 0,
    this.volume = 40,
    List<int>? eq,
    this.channelSetting = SpeakerChannel.stereo,
    this.partyCode = 0,
    this.broadcastOn = false,
    this.latencyMs = 150,
    this.concealed = 0,
    this.late = 0,
    this.firmware = 'demo-2026-11-18',
    this.hasService = true,
    this.phoneCodec = 'SBC',
    this.aacAllowed = false,
    this.legacyBroadcast = false,
    this.identityKey = appIdentityKey,
  }) : eq = eq ?? List<int>.filled(kEqBands, 0);

  final String id;
  final String advName;
  int rssi;
  SpeakerRole role;
  bool phoneConnected;
  bool streaming;
  bool following;
  int sampleRateHz;
  int volume;
  List<int> eq;
  SpeakerChannel channelSetting;
  int partyCode;
  bool broadcastOn;
  bool wifiReady = true;
  int latencyMs;
  int concealed;
  int late;
  /// Firmware revision string; null = the speaker exposes none.
  String? firmware;

  /// true: firmware revision is aefb000a in the control service (current
  /// firmware); false: only Device Information 2a26 (older firmware).
  bool firmwareInControl = true;
  bool hasService;
  bool advertising = true;

  /// Codec the phone streams with: 'SBC', 'AAC', 'aptX', 'aptX HD'.
  String phoneCodec;
  bool aacAllowed;
  int handover = 0;

  /// Firmware before 2026-11-19: 3-byte Broadcast read, no opcode 0x06.
  bool legacyBroadcast;

  /// What the phone does when the speaker reconnects it as SBC.
  bool phoneResumesAfterHandover = false;

  // --- identity / advert simulation -------------------------------------

  /// Key the speaker firmware was built with.
  IdentityKey identityKey;

  /// Firmware without the identity characteristic.
  bool hasIdentity = true;

  /// A device that copies the advert but cannot compute the tags.
  bool impostor = false;

  /// How many discovery requests Android refuses as busy before one works.
  int busyDiscoveries = 0;

  /// Android's GATT cache still holds this speaker's older table (no
  /// identity characteristic) until it is cleared.
  bool staleGattCache = false;

  /// The cached handles are wrong: the first identity op fails with ATT
  /// "invalid handle" until the cache is cleared.
  bool staleHandleOnce = false;

  /// Speaker nonce used for the challenge (fixed so tests are repeatable).
  List<int> speakerNonce = const [8, 9, 10, 11, 12, 13, 14, 15];

  /// Advert contents; tests change these to check the scan filter.
  int companyId = kWmpCompanyId;
  List<int>? advertPayload;
  bool advertHasService = true;

  int advertStatusBits({required bool appConnected}) =>
      (streaming ? 0x01 : 0) |
      (broadcastOn ? 0x02 : 0) |
      (role == SpeakerRole.partyMaster ? 0x04 : 0) |
      (following ? 0x08 : 0) |
      (phoneConnected ? 0x10 : 0) |
      (appConnected ? 0x20 : 0);

  SpeakerChannel get channelOutput =>
      partyCode == 2 ? channelSetting : SpeakerChannel.stereo;

  SpeakerStatus get status => SpeakerStatus(
        version: 1,
        role: role,
        phoneConnected: phoneConnected,
        bleConnected: true,
        streaming: streaming,
        followingParty: following,
        preparing: false,
        sampleRateHz: sampleRateHz,
        latencyMs: latencyMs,
        volume: volume,
        audioRunning: streaming,
        concealedGroups: concealed,
        lateGroups: late,
      );
}

/// Exception carrying the ATT error the real stack would surface.
class DemoAttError implements Exception {
  DemoAttError(this.code);
  final int code;
  @override
  String toString() => 'ATT error 0x${code.toRadixString(16).padLeft(2, '0')} (${switch (code) {
        kAttInvalidLength => 'invalid attribute value length',
        kAttInsufficientAuthorization => 'insufficient authorization',
        _ => 'value not allowed',
      }})';
}

class DemoBackend implements BleBackend {
  DemoBackend({
    List<DemoSpeaker>? speakers,
    this.opDelay = const Duration(milliseconds: 60),
    this.connectDelay = const Duration(milliseconds: 200),
    this.wifiStartDelay = const Duration(seconds: 2),
    this.handoverDelay = const Duration(seconds: 1),
    this.statusPeriod = const Duration(seconds: 1),
    AdapterStatus initialAdapter = AdapterStatus.on,
  })  : speakers = speakers ?? defaultSpeakers(),
        _adapter = initialAdapter;

  static List<DemoSpeaker> defaultSpeakers() => [
        DemoSpeaker(
          id: 'AA:BB:CC:00:4A:2F',
          advName: 'WMP-Control-4A2F',
          role: SpeakerRole.playing,
          phoneConnected: true,
          streaming: true,
          sampleRateHz: 44100,
          volume: 35,
        ),
        DemoSpeaker(
          id: 'AA:BB:CC:00:91:C3',
          advName: 'WMP-Control-91C3',
          volume: 50,
          eq: [6, 4, 0, -2, 1, 3, 5],
        ),
      ];

  final List<DemoSpeaker> speakers;
  final Duration opDelay;
  final Duration connectDelay;
  final Duration wifiStartDelay;
  final Duration handoverDelay;
  final Duration statusPeriod;
  AdapterStatus _adapter;

  final _adapterCtl = StreamController<AdapterStatus>.broadcast();
  final _scanCtl = StreamController<List<DiscoveredSpeaker>>.broadcast();
  final _scanningCtl = StreamController<bool>.broadcast();
  final Map<String, _DemoLink> _links = {};
  Timer? _scanTimer;
  bool _scanning = false;

  /// Number of GATT writes received (tests check debouncing with it).
  int writeCount = 0;

  /// Number of GATT reads (tests check that idle screens do not poll).
  int readCount = 0;

  /// Every scan started, for tests of the scan rules.
  final List<Duration> scans = [];
  bool get scanningNow => _scanning;
  final List<String> log = [];

  DemoSpeaker speaker(String id) => speakers.firstWhere((s) => s.id == id);

  void setAdapter(AdapterStatus s) {
    _adapter = s;
    _adapterCtl.add(s);
    if (s != AdapterStatus.on) {
      for (final l in [..._links.values]) {
        l.drop();
      }
      _stopScanning();
    }
  }

  /// Simulates the user pressing Play on the phone.
  void playOnPhone(String id) {
    final s = speaker(id);
    if (!s.phoneConnected) return;
    s.streaming = true;
    if (s.sampleRateHz == 0) s.sampleRateHz = 44100;
    if (s.broadcastOn && s.wifiReady) s.role = SpeakerRole.partyMaster;
  }

  /// Simulates the speaker going out of range / rebooting.
  void dropLink(String id) => _links[id]?.drop();

  @override
  AdapterStatus get adapterNow => _adapter;

  @override
  Stream<AdapterStatus> get adapterStatus => _adapterCtl.stream;

  @override
  Future<void> requestTurnOn() async {
    setAdapter(AdapterStatus.on);
  }

  @override
  Stream<List<DiscoveredSpeaker>> get scanResults => _scanCtl.stream;

  @override
  Stream<bool> get isScanning => _scanningCtl.stream;

  /// What a real scan would report: only adverts that pass [parseWmpAdvert].
  List<DiscoveredSpeaker> get _visible => [
        for (final s in speakers)
          if (s.advertising)
            if (parseWmpAdvert(
              serviceUuids: [if (s.advertHasService) WmpUuids.service],
              manufacturerData: {
                s.companyId: s.advertPayload ??
                    buildWmpAdvertPayload(status: s.advertStatusBits(appConnected: _links.containsKey(s.id))),
              },
            )
                case final advert?)
              DiscoveredSpeaker(
                id: s.id,
                advName: s.advName,
                rssi: s.rssi,
                seenAt: DateTime.now(),
                advert: advert,
              ),
      ];

  @override
  Future<void> startScan({Duration timeout = kScanBurst}) async {
    if (_adapter != AdapterStatus.on) throw StateError('Bluetooth must be on');
    if (_scanning) return;
    scans.add(timeout);
    _scanning = true;
    _scanningCtl.add(true);
    Timer(opDelay, () {
      if (_scanning) _scanCtl.add(_visible);
    });
    _scanTimer = Timer(timeout, _stopScanning);
  }

  void _stopScanning() {
    _scanTimer?.cancel();
    if (_scanning) {
      _scanning = false;
      _scanningCtl.add(false);
    }
  }

  @override
  Future<void> stopScan() async {
    if (_scanning) log.add('scan-stop');
    _stopScanning();
  }

  @override
  Future<SpeakerLink> connect(
    String id, {
    void Function(ConnectStage stage)? onStage,
    void Function(String line)? onTrace,
  }) async {
    if (_adapter != AdapterStatus.on) throw StateError('Bluetooth is off');
    log.add('connect:$id${_scanning ? ' (while scanning)' : ''}');
    onStage?.call(ConnectStage.connecting);
    await Future<void>.delayed(connectDelay);
    final s = speakers.where((e) => e.id == id).firstOrNull;
    if (s == null || !s.advertising) throw SpeakerConnectException('Speaker not found');
    onStage?.call(ConnectStage.discovering);
    await Future<void>.delayed(connectDelay);
    if (!s.hasService) {
      throw SpeakerConnectException('This device has no Music Party control service');
    }
    log.add('priority:lowPower');
    log.add('mtu:517');
    // Same discovery flow as the real backend, including the stale-cache
    // recovery: a stale cache hides the identity characteristic until it is
    // cleared.
    bool identityVisible;
    try {
      identityVisible = await discoverWithCacheRecovery<bool, bool>(
        discover: () async {
          await retryWhenBusy(() async {
            await Future<void>.delayed(opDelay);
            if (s.busyDiscoveries > 0) {
              s.busyDiscoveries--;
              throw Exception('gatt.discoverServices() returned false');
            }
          }, onTrace: onTrace);
          log.add('discover:$id');
          return s.hasIdentity && !s.staleGattCache;
        },
        resolve: (ok) => GattResolution(ok, missing: ok ? null : 'identity'),
        clearCache: () async {
          log.add('clear-cache');
          s.staleGattCache = false;
        },
        serviceCount: (ok) => ok ? 4 : 3,
        onTrace: onTrace,
      );
    } on SpeakerConnectException {
      rethrow;
    } catch (e) {
      throw ConnectStepException('Service discovery', e);
    }
    final link = _DemoLink(this, s, identityVisible: identityVisible);
    _links[id] = link;
    return link;
  }

  void _forget(_DemoLink l) {
    if (_links[l.id] == l) _links.remove(l.id);
  }

  void dispose() {
    for (final l in [..._links.values]) {
      l.drop();
    }
    _stopScanning();
  }
}

class _DemoLink implements SpeakerLink {
  _DemoLink(this._b, this._s, {required bool identityVisible})
      : _identityVisible = identityVisible, // ignore: prefer_initializing_formals
        _staleHandle = _s.staleHandleOnce;

  bool _identityVisible;

  /// Handles from a stale cache: identity ops fail until refreshServices.
  bool _staleHandle;

  final DemoBackend _b;
  final DemoSpeaker _s;
  final _conn = StreamController<bool>.broadcast();
  StreamController<List<int>>? _status;
  Timer? _statusTimer;
  bool _up = true;

  // Identity state of this connection: 0 idle, 1 challenged, 2 proven.
  int _idState = 0;
  List<int>? _appNonce;

  List<int> get _speakerKey => _s.impostor ? [for (final b in _s.identityKey.key) b ^ 0x5a] : _s.identityKey.key;

  List<int> _identityRead() {
    final s = _s;
    if (_idState == 1) {
      return [1, s.identityKey.id, ...s.speakerNonce, ...speakerTag(_speakerKey, _appNonce!, s.speakerNonce)];
    }
    return [_idState, s.identityKey.id, ...List.filled(kNonceLength + kTagLength, 0)];
  }

  void _identityWrite(List<int> v) {
    if (v.isEmpty) throw DemoAttError(kAttValueNotAllowed);
    if (v[0] == kIdentityOpChallenge && v.length == 1 + kNonceLength) {
      _appNonce = v.sublist(1);
      _idState = 1;
      return;
    }
    if (v[0] == kIdentityOpProof && v.length == 1 + kTagLength && _idState == 1) {
      final ok = constantTimeEquals(v.sublist(1), appTag(_speakerKey, _s.speakerNonce, _appNonce!));
      // One proof attempt per challenge.
      _idState = ok ? 2 : 0;
      if (!ok) throw DemoAttError(kAttInsufficientAuthorization);
      return;
    }
    throw DemoAttError(kAttValueNotAllowed);
  }

  @override
  String get id => _s.id;

  @override
  UuidLayout get layout => UuidLayout.canonical;

  @override
  Stream<bool> get connection => _conn.stream;

  void drop() {
    if (!_up) return;
    _up = false;
    _statusTimer?.cancel();
    _b._forget(this);
    _conn.add(false);
  }

  Future<void> _op() async {
    await Future<void>.delayed(_b.opDelay);
    if (!_up) throw StateError('device is not connected');
  }

  @override
  Future<List<int>> read(WmpChar c) async {
    await _op();
    _b.readCount++;
    _b.log.add('read:${c.name}');
    final s = _s;
    return switch (c) {
      WmpChar.status || WmpChar.control => s.status.encode(),
      WmpChar.volume => encodeVolume(s.volume),
      WmpChar.latency => encodeLatency(s.latencyMs),
      WmpChar.channel => [s.channelSetting.code, s.channelOutput.code, s.partyCode],
      WmpChar.eq => encodeEq(s.eq),
      WmpChar.broadcast => [
          s.broadcastOn ? 1 : 0,
          s.wifiReady ? 1 : 0,
          s.partyCode,
          if (!s.legacyBroadcast) ...[s.aacAllowed ? 1 : 0, s.handover],
        ],
      WmpChar.firmware => s.firmwareInControl && s.firmware != null
          ? utf8.encode(s.firmware!)
          : throw StateError('no firmware characteristic'),
      WmpChar.identity => _staleHandle
          ? throw Exception('GATT error: invalid handle (0x01)')
          : (s.hasIdentity ? _identityRead() : throw StateError('no identity characteristic')),
    };
  }

  @override
  Future<void> write(WmpChar c, List<int> v) async {
    await _op();
    _b.writeCount++;
    _b.log.add('${c.name}:${v.join(',')}');
    final s = _s;
    void reject() => throw DemoAttError(kAttValueNotAllowed);
    if (c == WmpChar.identity && _staleHandle) {
      throw Exception('GATT error: invalid handle (0x01)');
    }
    if (c == WmpChar.identity) {
      if (!s.hasIdentity) throw StateError('no identity characteristic');
      _identityWrite(v);
      return;
    }
    // Before the identity proof every write is refused.
    if (s.hasIdentity && _idState != 2) throw DemoAttError(kAttInsufficientAuthorization);
    switch (c) {
      case WmpChar.volume:
        if (v.length != 1 || v[0] > 100) reject();
        s.volume = v[0];
      case WmpChar.latency:
        if (v.length != 2 || s.role != SpeakerRole.partyMaster) reject();
        final ms = decodeLatency(v);
        if (ms < kLatencyMinMs || ms > kLatencyMaxMs) reject();
        s.latencyMs = ms;
      case WmpChar.channel:
        if (v.length != 1 || v[0] > 2) reject();
        s.channelSetting = SpeakerChannel.fromCode(v[0]);
      case WmpChar.eq:
        if (v.length != kEqBands) throw DemoAttError(kAttInvalidLength);
        final g = decodeEq(v);
        if (g.any((x) => x < kEqMinDb || x > kEqMaxDb)) reject();
        s.eq = g;
      case WmpChar.broadcast:
        if (v.length != 1 || v[0] > 1) reject();
        final on = v[0] == 1;
        if (on != s.broadcastOn) {
          s.broadcastOn = on;
          s.wifiReady = false;
          final needsHandover = on &&
              !s.legacyBroadcast &&
              s.streaming &&
              (s.phoneCodec.startsWith('aptX') || (s.phoneCodec == 'AAC' && !s.aacAllowed));
          if (needsHandover) {
            // Speaker drops the phone's A2DP and reconnects it as SBC.
            s.handover = 1;
            s.streaming = false;
            s.phoneConnected = false;
            s.sampleRateHz = 0;
            Timer(_b.handoverDelay, () {
              s.handover = 0;
              s.phoneConnected = true;
              s.phoneCodec = 'SBC';
              if (s.phoneResumesAfterHandover) {
                s.streaming = true;
                s.sampleRateHz = 44100;
              }
            });
          }
          Timer(_b.wifiStartDelay, () {
            s.wifiReady = true;
            if (on) {
              s.role = s.phoneConnected && s.streaming ? SpeakerRole.partyMaster : s.role;
              s.partyCode = 1;
            } else {
              s.role = s.phoneConnected ? SpeakerRole.playing : SpeakerRole.idle;
              s.partyCode = 0;
              s.following = false;
            }
          });
        }
      case WmpChar.control:
        if (v.isEmpty) reject();
        if (v[0] == ControlOp.broadcastCodec && !s.legacyBroadcast) {
          if (v.length != 2 || v[1] > 1) reject();
          s.aacAllowed = v[1] == 1;
        } else {
          reject();
        }
      case WmpChar.status:
      case WmpChar.identity:
      case WmpChar.firmware:
        reject();
    }
  }

  @override
  Future<Stream<List<int>>> subscribe(WmpChar c) async {
    await _op();
    _b.log.add('subscribe:${c.name}');
    // CCCD write: refused before the identity proof too.
    if (_s.hasIdentity && _idState != 2) throw DemoAttError(kAttInsufficientAuthorization);
    _status ??= StreamController<List<int>>.broadcast();
    _statusTimer?.cancel();
    _statusTimer = Timer.periodic(_b.statusPeriod, (_) {
      if (_up) _status!.add(_s.status.encode());
    });
    return _status!.stream;
  }

  @override
  int get discoveryCount => 1;

  @override
  bool has(WmpChar c) => switch (c) {
        WmpChar.identity => _s.hasIdentity && _identityVisible,
        WmpChar.firmware => _s.firmwareInControl && _s.firmware != null,
        _ => true,
      };

  @override
  Future<bool> refreshServices({void Function(String line)? onTrace}) async {
    await _op();
    _b.log.add('clear-cache');
    onTrace?.call('cleared GATT cache');
    _s.staleGattCache = false;
    _s.staleHandleOnce = false;
    _staleHandle = false;
    _b.log.add('discover:${_s.id}');
    _identityVisible = _s.hasIdentity;
    onTrace?.call('rediscovered 4 services');
    return _identityVisible;
  }

  @override
  Future<void> relaxConnection() async {
    _b.log.add('priority:lowPower');
  }

  @override
  Future<String?> readFirmwareRevision() async {
    if (has(WmpChar.firmware)) return decodeFirmwareRevision(await read(WmpChar.firmware));
    // Older firmware: Device Information 2a26 (null when that is gone too).
    await _op();
    if (_s.firmware != null) _b.log.add('read:2a26');
    return _s.firmware;
  }

  @override
  Future<void> disconnect() async {
    drop();
  }
}
