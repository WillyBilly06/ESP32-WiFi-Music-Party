/// State and actions for one speaker.
library;

import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';

import '../ble/ble_backend.dart';
import '../protocol/debounced_writer.dart';
import '../protocol/effects.dart';
import '../protocol/identity.dart';
import '../protocol/wmp_protocol.dart';

enum LinkState { idle, connecting, ready, reconnecting, failed }

class SpeakerController extends ChangeNotifier {
  SpeakerController({
    required this.id,
    required this.advName,
    required this.backend,
    this.nickname,
    bool Function()? adapterOn,
    this.onConnected,
    required this.identityKey,
    Random? random,
    Duration debounce = const Duration(milliseconds: 250),
  })  : _adapterOn = adapterOn ?? (() => true),
        _random = random ?? Random.secure() {
    _volumeW = DebouncedWriter<int>(
      debounce: debounce,
      write: (v) => _requireLink().write(WmpChar.volume, encodeVolume(v)),
      readBack: () async => decodeVolume(await _requireLink().read(WmpChar.volume)),
      onSettled: (r) => _settled('volume', r, (v) => volume = v),
    );
    _eqW = DebouncedWriter<List<int>>(
      debounce: debounce,
      equals: listEquals,
      write: (g) => _requireLink().write(WmpChar.eq, encodeEq(g)),
      readBack: () async => decodeEq(await _requireLink().read(WmpChar.eq)),
      onSettled: (r) => _settled('equalizer', r, (g) => eq = g, equals: listEquals),
    );
    _latencyW = DebouncedWriter<int>(
      debounce: debounce,
      write: (ms) => _requireLink().write(WmpChar.latency, encodeLatency(ms)),
      readBack: () async => decodeLatency(await _requireLink().read(WmpChar.latency)),
      onSettled: (r) => _settled('party delay', r, (v) => latencyMs = v),
    );
    _channelW = DebouncedWriter<SpeakerChannel>(
      debounce: Duration.zero,
      write: (c) => _requireLink().write(WmpChar.channel, encodeChannelWrite(c)),
      readBack: () async {
        final info = ChannelInfo.decode(await _requireLink().read(WmpChar.channel));
        channel = info;
        return info.setting;
      },
      onSettled: (r) => _settled('stereo pair', r, (_) {}),
    );
    _broadcastW = DebouncedWriter<bool>(
      debounce: Duration.zero,
      write: (on) => _requireLink().write(WmpChar.broadcast, encodeBroadcastWrite(on)),
      readBack: () async {
        final info = BroadcastInfo.decode(await _requireLink().read(WmpChar.broadcast));
        _applyBroadcast(info);
        return info.on;
      },
      onSettled: (r) {
        _settled('broadcast', r, (_) {});
        _startBroadcastFollow(minNotifications: r.requested ? 3 : 0);
      },
    );
    _effectsW = DebouncedWriter<EffectsSettings>(
      // Sliders: at most one write per ~150 ms of movement, and the final
      // value right away on release (commitEffects).
      debounce: const Duration(milliseconds: 150),
      write: (s) => _requireLink().write(WmpChar.effects, s.encode()),
      readBack: () async {
        // Re-read so the status bits (running / following / memory) are current.
        final st = EffectsState.decode(await _requireLink().read(WmpChar.effects));
        effects = st;
        return st.settings;
      },
      onSettled: (r) => _settled('effects', r, (_) {}),
    );
    _codecW = DebouncedWriter<PartyCodec>(
      debounce: Duration.zero,
      // No dedicated characteristic: Control opcode 0x06.
      write: (c) => _requireLink().write(WmpChar.control, ControlOp.setBroadcastCodec(c)),
      readBack: () async {
        final info = BroadcastInfo.decode(await _requireLink().read(WmpChar.broadcast));
        _applyBroadcast(info);
        final codec = info.codec;
        if (codec == null) throw SpeakerConnectException('Speaker firmware has no party codec option');
        return codec;
      },
      onSettled: (r) => _settled('party codec', r, (_) {}),
    );
  }

  final String id;
  String advName;
  String? nickname;
  final BleBackend backend;
  final bool Function() _adapterOn;
  final void Function(SpeakerController c)? onConnected;

  /// Key this app proves itself with (never logged).
  final IdentityKey identityKey;
  final Random _random;

  /// Set once the speaker passed the identity handshake on this connection.
  bool identityVerified = false;

  /// Steps of the last connection attempt with their time offsets, shown in
  /// the "Connect log" so an abandoned connect always says why.
  final List<String> connectLog = [];
  Stopwatch? _connectClock;

  void _trace(String line) {
    final ms = _connectClock?.elapsedMilliseconds ?? 0;
    connectLog.add('${(ms / 1000).toStringAsFixed(2)} s  $line');
    if (connectLog.length > 40) connectLog.removeAt(0);
  }

  String get displayName => nickname ?? friendlySpeakerName(advName, id);

  LinkState state = LinkState.idle;
  ConnectStage? stage;
  String? error;
  SpeakerLink? _link;
  UuidLayout? get layout => _link?.layout;

  SpeakerStatus? status;
  DateTime? statusAt;
  int? volume;
  List<int>? eq;
  ChannelInfo? channel;
  BroadcastInfo? broadcast;
  int? latencyMs;
  String? firmware;

  /// Broadcast value requested by the user that is not confirmed yet.
  bool? broadcastRequested;

  late final DebouncedWriter<int> _volumeW;
  late final DebouncedWriter<List<int>> _eqW;
  late final DebouncedWriter<int> _latencyW;
  late final DebouncedWriter<SpeakerChannel> _channelW;
  late final DebouncedWriter<bool> _broadcastW;
  late final DebouncedWriter<PartyCodec> _codecW;
  late final DebouncedWriter<EffectsSettings> _effectsW;

  /// Karaoke / effects state; null until read (or on older firmware).
  EffectsState? effects;

  /// Set after a codec handover (or a pause caused by switching party mode
  /// on) until the phone streams again: the UI asks the user to press Play.
  bool playPrompt = false;
  bool _streamingAtToggle = false;

  StreamSubscription<List<int>>? _statusSub;
  StreamSubscription<bool>? _connSub;
  int _watchers = 0;

  // Broadcast follow-up after a toggle, driven by Status notifications
  // (no timers): remaining notifications, seen so far, minimum to watch.
  int _followLeft = 0;
  int _followSeen = 0;
  int _followMin = 0;
  bool _followReading = false;
  bool _detailsReading = false;
  DateTime? _detailsReadAt;
  bool _userDisconnected = false;
  bool _connecting = false;
  bool _disposed = false;
  int _reconnectAttempt = 0;
  Timer? _reconnectTimer;

  /// True if the link was up and dropped because Bluetooth turned off.
  bool resumeWhenAdapterOn = false;

  final _notices = StreamController<String>.broadcast();

  /// Short user-facing messages (write rejected, connection lost, ...).
  Stream<String> get notices => _notices.stream;

  bool get isReady => state == LinkState.ready;
  bool get isBusy => state == LinkState.connecting || state == LinkState.reconnecting;
  bool get volumeBusy => _volumeW.busy;
  bool get eqBusy => _eqW.busy;
  bool get channelBusy => _channelW.busy;
  bool get broadcastBusy => _broadcastW.busy || _followLeft > 0;
  bool get latencyBusy => _latencyW.busy;
  bool get codecBusy => _codecW.busy;
  bool get effectsBusy => _effectsW.busy;

  /// Whether this speaker's firmware has the effects characteristic.
  bool get hasEffects => isReady && (_link?.has(WmpChar.effects) ?? false);

  /// Effects follow the party host on this speaker (editing disabled).
  bool get effectsFollowing => effects?.status.following ?? false;

  /// The speaker is moving the phone's audio to SBC for party mode.
  bool get inHandover => broadcast?.inHandover ?? false;

  PartySize get partySize => channel?.party ?? broadcast?.party ?? const PartySize(0);

  // -------------------------------------------------------------------------
  // Connection
  // -------------------------------------------------------------------------

  Future<void> connect() async {
    if (_disposed || state == LinkState.connecting || state == LinkState.ready) return;
    _userDisconnected = false;
    _reconnectTimer?.cancel();
    if (state != LinkState.reconnecting) _reconnectAttempt = 0;
    final reconnecting = state == LinkState.reconnecting;
    state = reconnecting ? LinkState.reconnecting : LinkState.connecting;
    error = null;
    stage = ConnectStage.connecting;
    _connecting = true;
    connectLog.clear();
    _connectClock = Stopwatch()..start();
    _trace(reconnecting ? 'reconnect attempt $_reconnectAttempt' : 'connect');
    _notify();
    SpeakerLink? attempt;
    try {
      final link = await backend.connect(id, onStage: (s) {
        stage = s;
        _notify();
      }, onTrace: _trace);
      attempt = link;
      if (_disposed || _userDisconnected) {
        await link.disconnect();
        return;
      }
      _link = link;
      identityVerified = false;
      _connSub = link.connection.listen((up) {
        if (!up) _onLinkDropped();
      });
      // Identity first: before the proof the speaker rejects every write,
      // CCCD subscriptions included.
      stage = ConnectStage.verifying;
      _notify();
      try {
        await _proveIdentity(link);
      } catch (e) {
        // A wrong-handle error (not a bad tag) means Android used a stale GATT
        // cache: clear it, rediscover once and run the handshake again.
        if (e is IdentityException || !isStaleHandleError(e)) rethrow;
        _trace('identity failed with a stale handle: $e');
        if (!await link.refreshServices(onTrace: _trace)) {
          throw IdentityException(IdentityFailure.unsupported);
        }
        await _proveIdentity(link);
      }
      _trace('identity proven');
      stage = ConnectStage.subscribing;
      _notify();
      // Initial status read, then live notifications (~1 per second).
      await _readStatusOnce();
      final stream = await link.subscribe(WmpChar.status);
      _statusSub = stream.listen(_onStatusBytes, onError: (_) {});
      // Discovery and subscription are done: ask for low power again so the
      // phone leaves any fast interval it picked during setup.
      await link.relaxConnection();
      stage = ConnectStage.reading;
      _notify();
      await _readAllSettings();
      if (_link != link) throw SpeakerConnectException('Connection lost');
      state = LinkState.ready;
      stage = null;
      _trace('ready');
      _reconnectAttempt = 0;
      resumeWhenAdapterOn = false;
      _notify();
      onConnected?.call(this);
      unawaited(_readFirmware());
    } catch (e) {
      _trace('failed: $e');
      _teardownLink();
      // Never leave a half-set-up link open.
      await attempt?.disconnect().catchError((_) {});
      if (_disposed) return;
      final identity = e is IdentityException
          ? e
          : (isAuthorizationError(e) ? IdentityException(IdentityFailure.rejected) : null);
      if (identity != null) {
        // Not ours, or keys differ: report it and do not retry by ourselves.
        state = LinkState.failed;
        stage = null;
        error = identity.message;
        _notices.add('$displayName: ${identity.message}');
        _notify();
      } else if (reconnecting && !_userDisconnected) {
        _scheduleReconnect(e);
      } else {
        state = LinkState.failed;
        stage = null;
        error = switch (e) {
          SpeakerConnectException() => e.message,
          ConnectStepException() => 'Could not connect: ${e.message}',
          _ => 'Could not connect (${describeBleError(e)})',
        };
        // A connect the user asked for is never dropped silently.
        _notices.add('$displayName: $error');
        _notify();
      }
    } finally {
      _connecting = false;
    }
  }

  Future<void> disconnect() async {
    _userDisconnected = true;
    resumeWhenAdapterOn = false;
    _reconnectTimer?.cancel();
    final link = _link;
    _teardownLink();
    await link?.disconnect();
    if (_disposed) return;
    state = LinkState.idle;
    stage = null;
    error = null;
    _notify();
  }

  /// Called by the app when Bluetooth switches off.
  void adapterTurnedOff() {
    final wasUp = state == LinkState.ready || state == LinkState.reconnecting || state == LinkState.connecting;
    _reconnectTimer?.cancel();
    _teardownLink();
    if (wasUp) {
      resumeWhenAdapterOn = true;
      state = LinkState.failed;
      stage = null;
      error = 'Bluetooth is off';
      _notify();
    }
  }

  void _onLinkDropped() {
    if (_disposed) return;
    if (_connecting) {
      // connect() is still running and will see the failure itself.
      _teardownLink();
      return;
    }
    final wasReady = state == LinkState.ready;
    _teardownLink();
    if (_userDisconnected) return;
    if (!_adapterOn()) {
      adapterTurnedOff();
      return;
    }
    if (wasReady) _notices.add('$displayName disconnected, reconnecting...');
    _reconnectAttempt = 0;
    _scheduleReconnect(null);
  }

  /// Reconnect delays. At least 3 s: every connection attempt (and its service
  /// discovery) briefly takes air time from the phone's A2DP stream.
  static const reconnectBackoff = [Duration(seconds: 3), Duration(seconds: 6), Duration(seconds: 12)];

  void _scheduleReconnect(Object? lastError) {
    if (_reconnectAttempt >= reconnectBackoff.length) {
      state = LinkState.failed;
      stage = null;
      error = 'Connection lost';
      _notify();
      return;
    }
    state = LinkState.reconnecting;
    stage = null;
    _notify();
    final delay = reconnectBackoff[_reconnectAttempt++];
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(delay, () {
      if (!_disposed && !_userDisconnected && _adapterOn()) unawaited(connect());
    });
  }

  /// Drops all per-link state synchronously so a new link can start at once.
  void _teardownLink() {
    _followLeft = 0;
    _followReading = false;
    _detailsReading = false;
    for (final w in [_volumeW, _eqW, _latencyW, _channelW, _broadcastW, _codecW, _effectsW]) {
      w.cancel();
    }
    broadcastRequested = null;
    playPrompt = false;
    final statusSub = _statusSub;
    final connSub = _connSub;
    _statusSub = null;
    _connSub = null;
    _link = null;
    unawaited(statusSub?.cancel());
    unawaited(connSub?.cancel());
  }

  SpeakerLink _requireLink() {
    final l = _link;
    if (l == null) throw SpeakerConnectException('The speaker is not connected');
    return l;
  }

  /// Identity handshake: exactly one write, one read and one write.
  Future<void> _proveIdentity(SpeakerLink link) async {
    if (!link.has(WmpChar.identity)) {
      throw IdentityException(IdentityFailure.unsupported);
    }
    final appNonce = randomNonce(_random);
    await link.write(WmpChar.identity, challengeWrite(appNonce));
    final challenge = await link.read(WmpChar.identity);
    final proof = verifySpeakerAndProve(identityKey, appNonce, challenge);
    await link.write(WmpChar.identity, proof);
    identityVerified = true;
  }

  // -------------------------------------------------------------------------
  // Reads
  // -------------------------------------------------------------------------

  Future<void> _readStatusOnce() async {
    final bytes = await _requireLink().read(WmpChar.status);
    _onStatusBytes(bytes);
  }

  void _onStatusBytes(List<int> bytes) {
    try {
      final s = SpeakerStatus.decode(bytes);
      final prev = status;
      status = s;
      statusAt = DateTime.now();
      if (!_volumeW.busy) volume = s.volume;
      if (!_latencyW.busy) latencyMs = s.latencyMs;
      if (s.streaming) playPrompt = false;
      _notify();
      if (prev != null) _onStatusChange(prev, s);
    } on ProtocolException {
      // ignore malformed notification
    }
  }

  Future<T?> _tryRead<T>(WmpChar c, T Function(List<int>) decode) async {
    try {
      return decode(await _requireLink().read(c));
    } catch (_) {
      // Optional value: a missing characteristic, malformed bytes or a slow
      // read must not break the rest of the screen.
      return null;
    }
  }

  Future<void> _readAllSettings() async {
    final v = await _tryRead(WmpChar.volume, decodeVolume);
    if (v != null && !_volumeW.busy) volume = v;
    _notify();
    final g = await _tryRead(WmpChar.eq, decodeEq);
    if (g != null && !_eqW.busy) eq = g;
    _notify();
    await _readDetails();
  }

  /// Channel and broadcast: the party values that are not in Status.
  /// (Party delay is in every Status notification, so it is not read here.)
  Future<void> _readDetails() async {
    final c = await _tryRead(WmpChar.channel, ChannelInfo.decode);
    if (c != null && !_channelW.busy) channel = c;
    final b = await _tryRead(WmpChar.broadcast, BroadcastInfo.decode);
    if (b != null && !_broadcastW.busy && !_codecW.busy) _applyBroadcast(b);
    _detailsReadAt = DateTime.now();
    _notify();
  }

  Future<void> _readFirmware() async {
    try {
      firmware = await _requireLink().readFirmwareRevision();
      _notify();
    } catch (_) {
      // optional
    }
  }

  /// Manual refresh (pull to refresh on the speaker page).
  Future<void> refresh() async {
    if (!isReady) return;
    try {
      await _readStatusOnce();
      await _readAllSettings();
    } catch (e) {
      _notices.add('Refresh failed: ${describeBleError(e)}');
    }
  }

  /// Called when a screen showing party details opens. Channel, broadcast and
  /// party size are not in the Status notification, so they are read once
  /// here (unless just read). There is no periodic polling: every GATT read
  /// costs the phone radio time it also needs for the A2DP stream.
  void watchDetails() {
    _watchers++;
    final at = _detailsReadAt;
    final fresh = at != null && DateTime.now().difference(at) < const Duration(seconds: 5);
    if (_watchers == 1 && isReady && !fresh && !_anyWriteBusy) {
      unawaited(_readDetailsSafe());
    }
  }

  void unwatchDetails() {
    if (_watchers > 0) _watchers--;
  }

  bool get _anyWriteBusy =>
      _volumeW.busy ||
      _eqW.busy ||
      _latencyW.busy ||
      _channelW.busy ||
      _broadcastW.busy ||
      _codecW.busy ||
      _effectsW.busy;

  Future<void> _readDetailsSafe() async {
    if (_detailsReading) return;
    _detailsReading = true;
    try {
      await _readDetails();
    } catch (_) {
      // a failed read is not worth a message
    } finally {
      _detailsReading = false;
    }
  }

  static bool _partyChanged(SpeakerStatus a, SpeakerStatus b) =>
      a.role != b.role || a.followingParty != b.followingParty;

  static bool _linkChanged(SpeakerStatus a, SpeakerStatus b) =>
      _partyChanged(a, b) ||
      a.phoneConnected != b.phoneConnected ||
      a.streaming != b.streaming ||
      a.preparing != b.preparing;

  /// Status notifications (~1/s) drive the few extra reads the app needs:
  /// while a broadcast toggle settles, and once when the party role changes.
  void _onStatusChange(SpeakerStatus prev, SpeakerStatus s) {
    if (!isReady) return;
    if (_followLeft > 0) {
      _followLeft--;
      _followSeen++;
      if (!_followReading && (_linkChanged(prev, s) || _followSeen.isEven)) {
        unawaited(_followRead());
      } else if (_followLeft == 0) {
        _finishBroadcastFollow();
      }
      return;
    }
    if (_partyChanged(prev, s) && !_anyWriteBusy) unawaited(_readDetailsSafe());
  }

  /// Applies a Broadcast read and tracks the codec handover so the UI can
  /// show "Switching to party mode..." and then a Play prompt.
  void _applyBroadcast(BroadcastInfo b) {
    final wasHandover = broadcast?.inHandover ?? false;
    broadcast = b;
    if (b.inHandover) {
      playPrompt = false;
    } else if (wasHandover && b.on && !(status?.streaming ?? false)) {
      playPrompt = true;
    }
    if (!b.on) playPrompt = false;
  }

  bool _broadcastSettled(BroadcastInfo b) => b.wifiReady && !b.inHandover;

  /// Follows `wifi_ready` and `handover` after a toggle, reading Broadcast
  /// when a Status notification shows the link changing (and every other
  /// notification otherwise) for at most [_followMax] notifications. When
  /// turning on, at least [minNotifications] are watched even if Wi-Fi is
  /// already up, so a short codec handover is not missed.
  static const _followMax = 20;

  void _startBroadcastFollow({int minNotifications = 0}) {
    final b0 = broadcast;
    if (b0 == null || (minNotifications == 0 && _broadcastSettled(b0))) {
      _finishBroadcastFollow();
      return;
    }
    _followLeft = _followMax;
    _followSeen = 0;
    _followMin = minNotifications;
    _notify();
  }

  Future<void> _followRead() async {
    _followReading = true;
    final b = await _tryRead(WmpChar.broadcast, BroadcastInfo.decode);
    _followReading = false;
    if (!isReady || (_followLeft == 0 && broadcastRequested == null)) return;
    if (b != null) _applyBroadcast(b);
    final cur = broadcast;
    if ((cur != null && _broadcastSettled(cur) && _followSeen >= _followMin) || _followLeft == 0) {
      _followLeft = 0;
      final c = await _tryRead(WmpChar.channel, ChannelInfo.decode);
      if (c != null) channel = c;
      _finishBroadcastFollow();
      return;
    }
    _notify();
  }

  void _finishBroadcastFollow() {
    final b = broadcast;
    // Music was playing when party mode was switched on and is not anymore:
    // most phones pause when the speaker moves their stream to SBC.
    if (broadcastRequested == true &&
        _streamingAtToggle &&
        b != null &&
        b.on &&
        !b.inHandover &&
        !(status?.streaming ?? false)) {
      playPrompt = true;
    }
    broadcastRequested = null;
    _streamingAtToggle = false;
    _notify();
  }

  void dismissPlayPrompt() {
    playPrompt = false;
    _notify();
  }

  // -------------------------------------------------------------------------
  // Writes (optimistic UI, debounced, confirmed by read back)
  // -------------------------------------------------------------------------

  void setVolume(int v) {
    if (!isReady) return;
    volume = v.clamp(kVolumeMin, kVolumeMax);
    _volumeW.set(volume!);
    _notify();
  }

  void setEq(List<int> gains) {
    if (!isReady) return;
    eq = [for (final g in gains) clampEq(g)];
    _eqW.set(eq!);
    _notify();
  }

  void setEqBand(int band, int db) {
    final g = [...(eq ?? List.filled(kEqBands, 0))];
    g[band] = clampEq(db);
    setEq(g);
  }

  void setLatency(int ms) {
    if (!isReady) return;
    latencyMs = ms.clamp(kLatencyMinMs, kLatencyMaxMs);
    _latencyW.set(latencyMs!);
    _notify();
  }

  void setChannel(SpeakerChannel c) {
    if (!isReady) return;
    final cur = channel;
    channel = ChannelInfo(
      setting: c,
      output: cur?.output ?? SpeakerChannel.stereo,
      party: cur?.party ?? const PartySize(0),
    );
    _channelW.setNow(c);
    _notify();
  }

  void setBroadcast(bool on) {
    if (!isReady) return;
    broadcastRequested = on;
    _streamingAtToggle = on && (status?.streaming ?? false);
    playPrompt = false;
    final cur = broadcast ?? BroadcastInfo(on: on, wifiReady: false, party: const PartySize(0));
    broadcast = cur.copyWith(on: on, wifiReady: false);
    _broadcastW.setNow(on);
    _notify();
  }

  /// Party codec (SBC only, or also AAC-LC). Needs firmware that reports
  /// `aac_allowed` in the Broadcast characteristic.
  /// Reads the effects when the effects screen opens (not part of connect,
  /// to keep connection setup short).
  Future<void> loadEffects() async {
    if (!hasEffects || _effectsW.busy) return;
    final st = await _tryRead(WmpChar.effects, EffectsState.decode);
    if (st != null && !_effectsW.busy) {
      effects = st;
      _notify();
    }
  }

  /// Changes effect settings (sliders): debounced write, then re-read.
  void setEffects(EffectsSettings s) => _changeEffects(s, now: false);

  /// Switches and presets: written right away.
  void setEffectsNow(EffectsSettings s) => _changeEffects(s, now: true);

  /// Slider released: write the final value now instead of after the debounce.
  void commitEffects() {
    final st = effects;
    if (st != null && _effectsW.waiting) _effectsW.setNow(st.settings);
  }

  void _changeEffects(EffectsSettings s, {required bool now}) {
    final cur = effects;
    if (!hasEffects || cur == null || cur.status.following) return;
    final next = s.clamped();
    effects = EffectsState(next, cur.status);
    now ? _effectsW.setNow(next) : _effectsW.set(next);
    _notify();
  }

  void setPartyCodec(PartyCodec c) {
    final cur = broadcast;
    if (!isReady || cur == null || !cur.supportsCodecChoice) return;
    broadcast = cur.copyWith(aacAllowed: c == PartyCodec.aacLc);
    _codecW.setNow(c);
    _notify();
  }

  void _settled<T>(String what, WriteResult<T> r, void Function(T v) apply,
      {bool Function(T a, T b)? equals}) {
    if (_disposed) return;
    final actual = r.actual;
    if (actual != null) apply(actual);
    if (r.error != null) {
      _notices.add('Could not change $what: ${describeBleError(r.error!)}');
    } else if (actual != null && !(equals?.call(actual, r.requested) ?? actual == r.requested)) {
      _notices.add('The speaker kept a different $what value');
    }
    _notify();
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _reconnectTimer?.cancel();
    for (final w in [_volumeW, _eqW, _latencyW, _channelW, _broadcastW, _codecW, _effectsW]) {
      w.dispose();
    }
    final link = _link;
    _teardownLink();
    unawaited(link?.disconnect());
    unawaited(_notices.close());
    super.dispose();
  }
}
