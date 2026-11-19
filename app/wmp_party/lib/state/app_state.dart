/// App-wide state: permissions, adapter, scanning, speakers, presets.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import '../ble/ble_backend.dart';
import '../ble/identity_key.dart';
import '../ble/permissions.dart';
import '../protocol/identity.dart';
import '../protocol/eq.dart';
import '../protocol/wmp_protocol.dart';
import '../storage/app_store.dart';
import 'speaker_controller.dart';

/// One row on the home screen.
class SpeakerEntry {
  const SpeakerEntry({
    required this.id,
    required this.name,
    this.controller,
    this.seen,
    this.known,
  });

  final String id;
  final String name;
  final SpeakerController? controller;
  final DiscoveredSpeaker? seen;
  final KnownSpeaker? known;

  bool get isNearby => seen != null;
  bool get isActive =>
      controller != null && controller!.state != LinkState.idle;
}

class AppState extends ChangeNotifier {
  AppState({
    required this.backend,
    required this.permissions,
    required this.store,
    this.controllerDebounce = const Duration(milliseconds: 250),
    IdentityKey? identityKey,
  }) : identityKey = identityKey ?? appIdentityKey;

  /// Key used for the identity handshake with every speaker.
  final IdentityKey identityKey;

  final BleBackend backend;
  final PermissionGate permissions;
  final AppStore store;
  final Duration controllerDebounce;

  PermissionState permission = PermissionState.unknown;
  bool needsLocation = false;
  AdapterStatus adapter = AdapterStatus.unknown;
  bool scanning = false;
  bool initialized = false;
  String? scanError;

  final Map<String, DiscoveredSpeaker> _seen = {};
  final Map<String, SpeakerController> _controllers = {};
  List<KnownSpeaker> known = [];
  List<EqPreset> customPresets = [];

  StreamSubscription<AdapterStatus>? _adapterSub;
  StreamSubscription<List<DiscoveredSpeaker>>? _scanSub;
  StreamSubscription<bool>? _scanningSub;
  bool _disposed = false;

  bool get ready =>
      permission == PermissionState.granted && adapter == AdapterStatus.on;

  Future<void> init() async {
    known = await store.loadSpeakers();
    customPresets = await store.loadPresets();
    needsLocation = await permissions.needsLocation();
    permission = await permissions.check();
    adapter = backend.adapterNow;
    _adapterSub = backend.adapterStatus.listen(_onAdapter);
    _scanSub = backend.scanResults.listen(_onScan);
    _scanningSub = backend.isScanning.listen(_onScanning);
    initialized = true;
    _notify();
    unawaited(scan());
  }

  void _onScanning(bool s) {
    final ended = scanning && !s;
    scanning = s;
    if (!s) _scanActive = false;
    if (ended && !_scanStoppedEarly && _scanStartedAt != null) {
      // A full scan ran: drop speakers that were not heard during it.
      _seen.removeWhere((_, d) => d.seenAt.isBefore(_scanStartedAt!));
    }
    _notify();
  }

  void _onAdapter(AdapterStatus s) {
    final wasOn = adapter == AdapterStatus.on;
    adapter = s;
    if (wasOn && s != AdapterStatus.on) {
      for (final c in _controllers.values) {
        c.adapterTurnedOff();
      }
      _seen.clear();
    }
    if (!wasOn && s == AdapterStatus.on && permission == PermissionState.granted) {
      unawaited(scan());
      for (final c in _controllers.values) {
        if (c.resumeWhenAdapterOn) unawaited(c.connect());
      }
    }
    _notify();
  }

  void _onScan(List<DiscoveredSpeaker> list) {
    for (final d in list) {
      _seen[d.id] = d;
      final c = _controllers[d.id];
      if (c != null && d.advName.isNotEmpty) c.advName = d.advName;
    }
    // Stop as soon as every remembered speaker has been heard.
    if ((scanning || _scanActive) &&
        known.isNotEmpty &&
        known.every((k) => _seen.containsKey(k.id) || (_controllers[k.id]?.isReady ?? false))) {
      unawaited(_stopScanEarly());
    }
    _notify();
  }

  Future<void> requestPermission() async {
    permission = await permissions.request();
    _notify();
    unawaited(scan());
  }

  Future<void> recheckPermission() async {
    final before = permission;
    permission = await permissions.check();
    if (permission != before) _notify();
    if (before != PermissionState.granted) unawaited(scan());
  }

  Future<void> openSettings() => permissions.openSettings();

  Future<void> turnOnBluetooth() async {
    try {
      await backend.requestTurnOn();
    } catch (_) {
      // user declined; the adapter stream keeps the screen up to date
    }
  }

  DateTime? _scanStartedAt;
  bool _scanStoppedEarly = false;

  /// Set synchronously when a burst starts (the backend's scanning stream
  /// reports a little later), so a Connect tap right after can still stop it.
  bool _scanActive = false;

  /// One high-rate scan burst (at most [kScanBurst]). Used at start-up, when
  /// Bluetooth comes on, by the scan button, pull to refresh and when the home
  /// screen comes back. It stops early once every saved speaker is heard and
  /// before any connection.
  Future<void> scan() async {
    if (!ready || _scanActive || scanning) return;
    // Never scan while a connection is being set up.
    if (_controllers.values.any((c) => c.isBusy)) return;
    scanError = null;
    _scanStartedAt = DateTime.now();
    _scanStoppedEarly = false;
    _scanActive = true;
    try {
      await backend.startScan(timeout: kScanBurst);
    } catch (e) {
      _scanActive = false;
      scanError = needsLocation
          ? 'Scan failed. On this Android version Location must be switched on to find speakers.'
          : 'Scan failed: ${describeBleError(e)}';
    }
    _notify();
  }

  Future<void> stopScan() => _stopScanEarly();

  Future<void> _stopScanEarly() async {
    if (!scanning && !_scanActive) return;
    _scanStoppedEarly = true;
    _scanActive = false;
    await backend.stopScan();
  }

  /// The home screen is visible again (app resumed, or back from a speaker).
  void homeResumed() => unawaited(scan());

  // -------------------------------------------------------------------------
  // Speakers
  // -------------------------------------------------------------------------

  KnownSpeaker? knownById(String id) {
    for (final k in known) {
      if (k.id == id) return k;
    }
    return null;
  }

  SpeakerController? controller(String id) => _controllers[id];

  SpeakerController controllerFor(String id) {
    return _controllers.putIfAbsent(id, () {
      final k = knownById(id);
      final c = SpeakerController(
        id: id,
        advName: _seen[id]?.advName ?? k?.advName ?? '',
        nickname: k?.nickname,
        backend: backend,
        debounce: controllerDebounce,
        adapterOn: () => adapter == AdapterStatus.on,
        onConnected: _remember,
        identityKey: identityKey,
      );
      c.addListener(_notify);
      return c;
    });
  }

  Future<void> connect(String id) async {
    final c = controllerFor(id);
    // Never scan and connect at once: the scan would keep loading the radio
    // (and slows the connection on many Android phones).
    await _stopScanEarly();
    _notify();
    await c.connect();
  }

  Future<void> disconnect(String id) async {
    await _controllers[id]?.disconnect();
    _notify();
  }

  List<SpeakerController> get connected => [
        for (final c in _controllers.values)
          if (c.isReady) c,
      ];

  /// Home screen rows: active speakers first, then nearby, then remembered.
  List<SpeakerEntry> get entries {
    final ids = <String>{
      ..._controllers.entries
          .where((e) => e.value.state != LinkState.idle)
          .map((e) => e.key),
      ..._seen.keys,
      ...known.map((k) => k.id),
    };
    final list = [
      for (final id in ids)
        SpeakerEntry(
          id: id,
          name: _nameFor(id),
          controller: _controllers[id],
          seen: _seen[id],
          known: knownById(id),
        ),
    ];
    int rank(SpeakerEntry e) => e.isActive ? 0 : (e.isNearby ? 1 : 2);
    list.sort((a, b) {
      final r = rank(a).compareTo(rank(b));
      if (r != 0) return r;
      return a.name.toLowerCase().compareTo(b.name.toLowerCase());
    });
    return list;
  }

  String _nameFor(String id) {
    final c = _controllers[id];
    if (c != null) return c.displayName;
    final k = knownById(id);
    if (k?.nickname != null) return k!.nickname!;
    return friendlySpeakerName(_seen[id]?.advName ?? k?.advName ?? '', id);
  }

  void _remember(SpeakerController c) {
    final existing = knownById(c.id);
    final updated = (existing ??
            KnownSpeaker(id: c.id, advName: c.advName, lastConnected: DateTime.now()))
        .copyWith(
      advName: c.advName.isEmpty ? null : c.advName,
      lastConnected: DateTime.now(),
    );
    known = [
      for (final k in known)
        if (k.id != c.id) k,
      updated,
    ];
    unawaited(store.saveSpeakers(known));
    _notify();
  }

  Future<void> rename(String id, String nickname) async {
    final nick = nickname.trim();
    final c = _controllers[id];
    if (c != null) c.nickname = nick.isEmpty ? null : nick;
    final k = knownById(id);
    if (k != null) {
      known = [
        for (final e in known)
          if (e.id == id)
            e.copyWith(nickname: nick, clearNickname: nick.isEmpty)
          else
            e,
      ];
      await store.saveSpeakers(known);
    }
    _notify();
  }

  Future<void> forget(String id) async {
    final c = _controllers.remove(id);
    if (c != null) {
      await c.disconnect();
      c.removeListener(_notify);
      c.dispose();
    }
    known = [
      for (final k in known)
        if (k.id != id) k,
    ];
    await store.saveSpeakers(known);
    _notify();
  }

  /// Turns broadcast on/off on every connected speaker (party mode button).
  void setPartyAll(bool on) {
    for (final c in connected) {
      c.setBroadcast(on);
    }
  }

  bool get anyBroadcasting => connected.any((c) => c.broadcast?.on ?? false);

  // -------------------------------------------------------------------------
  // Presets
  // -------------------------------------------------------------------------

  List<EqPreset> get allPresets => [...kBuiltInPresets, ...customPresets];

  Future<EqPreset> savePreset(String name, List<int> gains) async {
    final trimmed = name.trim().isEmpty ? 'My preset' : name.trim();
    final preset = EqPreset(
      id: 'u${DateTime.now().microsecondsSinceEpoch}',
      name: trimmed,
      gains: [...gains],
    );
    customPresets = [...customPresets, preset];
    await store.savePresets(customPresets);
    _notify();
    return preset;
  }

  Future<void> deletePreset(String id) async {
    customPresets = [
      for (final p in customPresets)
        if (p.id != id) p,
    ];
    await store.savePresets(customPresets);
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    unawaited(_adapterSub?.cancel());
    unawaited(_scanSub?.cancel());
    unawaited(_scanningSub?.cancel());
    for (final c in _controllers.values) {
      c.removeListener(_notify);
      c.dispose();
    }
    super.dispose();
  }
}
