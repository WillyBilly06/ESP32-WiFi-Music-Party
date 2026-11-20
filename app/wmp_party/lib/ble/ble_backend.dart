/// BLE abstraction used by the app state, so screens and controllers can be
/// tested with a fake speaker and the real implementation can use
/// flutter_blue_plus.
library;

import '../protocol/advert.dart';
import '../protocol/wmp_protocol.dart';

enum AdapterStatus { unknown, unavailable, unauthorized, turningOn, on, turningOff, off }

/// One advertising Music Party speaker seen during a scan (already checked
/// with [parseWmpAdvert]; anything else is dropped silently).
class DiscoveredSpeaker {
  const DiscoveredSpeaker({
    required this.id,
    required this.advName,
    required this.rssi,
    required this.seenAt,
    required this.advert,
  });

  /// Platform remote id (MAC address on Android).
  final String id;

  /// Name from the scan response; may be empty.
  final String advName;
  final int rssi;
  final DateTime seenAt;
  final WmpAdvert advert;
}

/// Hard cap of one scan burst. Scans are high duty (Android low-latency mode)
/// so speakers, which advertise every 200 ms, show up within a few hundred
/// ms; bursts stay short because a continuous low-latency scan starves the
/// phone's own A2DP stream (measured: 124 lost RTP packets per 30 s).
const Duration kScanBurst = Duration(seconds: 6);

/// Progress of a connection attempt, for UI feedback.
enum ConnectStage { connecting, discovering, verifying, subscribing, reading }

class SpeakerConnectException implements Exception {
  SpeakerConnectException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// A connection-setup step failed. [step] is shown to the user ("Service
/// discovery failed ..."), [cause] is the platform error.
class ConnectStepException implements Exception {
  ConnectStepException(this.step, this.cause);
  final String step;
  final Object cause;

  String get message => '$step failed (${describeBleError(cause)})';

  @override
  String toString() => '$step failed: $cause';
}

/// Result of matching a discovery against what the app needs.
class GattResolution<T> {
  const GattResolution(this.value, {this.missing});

  /// What could be used (null when even the control service is missing).
  final T? value;

  /// Human-readable list of what is missing, null when complete.
  final String? missing;
}

/// Characteristics without which the app cannot work.
const List<WmpChar> kRequiredChars = [WmpChar.status, WmpChar.volume, WmpChar.identity];

/// Message for a speaker whose GATT table lacks what this app needs.
const String kFirmwareUpdateMessage = 'Speaker needs a firmware update';

/// Discovery is this fast only when Android answered from its own cache.
const Duration kCachedDiscoveryHint = Duration(milliseconds: 200);

/// Discovers services and, if the result lacks something required, assumes
/// Android answered from a stale GATT cache (e.g. one recorded for older
/// speaker firmware): clears the cache and discovers ONCE more. Returns the
/// complete result, or the second incomplete one when it still has a value
/// (the caller reports what is missing); throws [kFirmwareUpdateMessage] when
/// not even the control service is there.
Future<T> discoverWithCacheRecovery<D, T>({
  required Future<D> Function() discover,
  required GattResolution<T> Function(D discovered) resolve,
  required Future<void> Function() clearCache,
  required int Function(D discovered) serviceCount,
  void Function(String line)? onTrace,
  void Function()? onRediscover,
}) async {
  Future<GattResolution<T>> run(String verb) async {
    final sw = Stopwatch()..start();
    final d = await discover();
    sw.stop();
    final cached = sw.elapsed < kCachedDiscoveryHint ? ', from cache?' : '';
    onTrace?.call('$verb ${serviceCount(d)} services (${sw.elapsedMilliseconds} ms$cached)');
    return resolve(d);
  }

  final first = await run('discovered');
  if (first.missing == null) return first.value as T;
  onTrace?.call('missing ${first.missing}: GATT cache may be stale');
  try {
    await clearCache();
    onTrace?.call('cleared GATT cache');
  } catch (e) {
    onTrace?.call('clearing GATT cache failed: $e');
  }
  onRediscover?.call();
  final second = await run('rediscovered');
  if (second.missing == null) return second.value as T;
  onTrace?.call('still missing ${second.missing}');
  final value = second.value;
  if (value != null) return value;
  throw SpeakerConnectException(kFirmwareUpdateMessage);
}

/// A GATT error that points at a wrong attribute handle (stale cache) rather
/// than at a refused value: ATT 0x01 invalid handle, 0x0A attribute not found,
/// or flutter_blue_plus/Android not finding the characteristic.
bool isStaleHandleError(Object e) {
  final lower = e.toString().toLowerCase();
  return lower.contains('invalid handle') ||
      lower.contains('attribute not found') ||
      lower.contains('attr_not_found') ||
      lower.contains('characteristic not found') ||
      lower.contains('service not found') ||
      lower.contains('not found in services') ||
      RegExp(r'\b0x0?1\b|\b0x0a\b').hasMatch(lower) ||
      RegExp(r'code: (1|10)(?!\d)').hasMatch(lower);
}

/// Android refuses a GATT request while its stack is busy with another one
/// (flutter_blue_plus reports "gatt.xxx() returned false").
bool isBusyError(Object e) {
  final lower = e.toString().toLowerCase();
  return lower.contains('returned false') || lower.contains('busy');
}

/// Runs [op], retrying up to [attempts] times while it fails with
/// [isBusyError] and [stillConnected] holds. Other errors are rethrown at once.
Future<T> retryWhenBusy<T>(
  Future<T> Function() op, {
  int attempts = 3,
  Duration delay = const Duration(milliseconds: 400),
  bool Function()? stillConnected,
  void Function(String line)? onTrace,
}) async {
  for (var i = 1;; i++) {
    try {
      return await op();
    } catch (e) {
      final connected = stillConnected?.call() ?? true;
      if (i >= attempts || !isBusyError(e) || !connected) rethrow;
      onTrace?.call('busy, retry $i: $e');
      await Future<void>.delayed(delay);
    }
  }
}

/// A connected speaker's GATT link.
abstract class SpeakerLink {
  String get id;

  /// Which UUID byte order the speaker actually exposes.
  UuidLayout get layout;

  /// Emits `false` once when the link drops (remote, adapter off, or local).
  Stream<bool> get connection;

  Future<List<int>> read(WmpChar c);

  /// Write with response, so ATT errors (0x13 value not allowed) surface.
  Future<void> write(WmpChar c, List<int> value);

  /// Enables notifications and returns the value stream.
  Future<Stream<List<int>>> subscribe(WmpChar c);

  /// Asks the phone for its low-power connection parameters. Android bursts
  /// to a 7.5 ms interval during service discovery, which leaves the
  /// speaker's single radio no room for the phone's A2DP packets.
  Future<void> relaxConnection();

  /// Number of GATT service discoveries run on this connection (always 1).
  int get discoveryCount;

  /// Whether the speaker exposes this characteristic.
  bool has(WmpChar c);

  /// Clears Android's GATT cache for this device and discovers once more
  /// (stale-cache recovery). Returns whether everything required is present.
  Future<bool> refreshServices({void Function(String line)? onTrace});

  /// Device Information firmware revision, null when unavailable.
  Future<String?> readFirmwareRevision();

  Future<void> disconnect();
}

abstract class BleBackend {
  AdapterStatus get adapterNow;
  Stream<AdapterStatus> get adapterStatus;

  /// Asks the OS to switch Bluetooth on (Android shows a system dialog).
  Future<void> requestTurnOn();

  /// Latest WMP speakers seen (already filtered).
  Stream<List<DiscoveredSpeaker>> get scanResults;
  Stream<bool> get isScanning;

  /// Starts a high-rate scan burst that stops by itself after [timeout].
  Future<void> startScan({Duration timeout = kScanBurst});
  Future<void> stopScan();

  /// [onTrace] receives one line per setup step (shown in the connect log).
  Future<SpeakerLink> connect(
    String id, {
    void Function(ConnectStage stage)? onStage,
    void Function(String line)? onTrace,
  });
}

/// ATT 0x08 (insufficient authorization): a write before the identity proof
/// was accepted.
bool isAuthorizationError(Object e) {
  final lower = e.toString().toLowerCase();
  return lower.contains('0x08') ||
      lower.contains('insufficient authorization') ||
      lower.contains('insufficient_authorization') ||
      RegExp(r'code: 8(?!\d)').hasMatch(lower);
}

/// Turns platform exceptions into short user-facing text.
String describeBleError(Object e) {
  final s = e.toString();
  final lower = s.toLowerCase();
  // ATT 0x13 (value not allowed) or 0x0D (invalid length). Match whole codes
  // so Android's GATT error 133 is not mistaken for 13.
  if (isAuthorizationError(e)) return 'The speaker did not accept this app';
  if (lower.contains('0x13') || lower.contains('value not allowed') ||
      lower.contains('0x0d') || lower.contains('invalid attribute value length') ||
      RegExp(r'code: (13|19)(?!\d)').hasMatch(s)) {
    return 'The speaker rejected that value';
  }
  if (s.toLowerCase().contains('timeout') || s.toLowerCase().contains('timed out')) {
    return 'The speaker did not answer in time';
  }
  if (s.toLowerCase().contains('not connected') ||
      s.toLowerCase().contains('disconnected')) {
    return 'The speaker is not connected';
  }
  if (e is SpeakerConnectException) return e.message;
  return 'Bluetooth error';
}
