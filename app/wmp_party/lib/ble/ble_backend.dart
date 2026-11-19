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

  Future<SpeakerLink> connect(
    String id, {
    void Function(ConnectStage stage)? onStage,
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
