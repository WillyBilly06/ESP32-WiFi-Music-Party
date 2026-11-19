/// Pure-Dart encoding/decoding of the WMP speaker BLE control protocol.
///
/// Contract: `docs/ble-control-protocol.md` (firmware 2026-11-18).
/// This file must not import Flutter or any BLE plugin so it can be unit tested
/// on the plain Dart VM.
library;

import 'dart:typed_data';

/// BLE advertising name prefix of every speaker (`WMP-Control-XXXX`).
const String kAdvertisingNamePrefix = 'WMP-Control-';

/// Bluetooth Classic (A2DP sink) name prefix of the same speaker.
const String kClassicNamePrefix = 'WMP-Sink-';

/// Characteristics of the control service.
enum WmpChar {
  control(0x02),
  status(0x03),
  volume(0x04),
  latency(0x05),
  channel(0x06),
  eq(0x07),
  broadcast(0x08);

  const WmpChar(this.index16);

  /// The `N` in `aefb000N-...`.
  final int index16;
}

/// Byte order of the 128-bit UUIDs as seen by the phone.
///
/// The firmware doc writes the UUIDs big-endian (`aefb0001-...`), but asks to
/// verify against a real scan because a reversed byte order would make the
/// service appear as `01000000-5bec-00b1-0d4b-512c0100fbae`. The app accepts
/// both so it works either way without a rebuild.
enum UuidLayout { canonical, reversed }

/// UUID helpers for the WMP control service.
class WmpUuids {
  WmpUuids._();

  static const int serviceIndex = 0x01;

  /// Canonical (as documented) lowercase UUID string for index `n`
  /// (1 = service, 2..8 = characteristics).
  static String canonical(int n) {
    final hex = n.toRadixString(16).padLeft(2, '0');
    return 'aefb00$hex-2c51-4b0d-b100-ec5b00000001';
  }

  /// The same UUID with its 16 bytes reversed.
  static String reversed(int n) => reverseUuidBytes(canonical(n));

  static String forLayout(int n, UuidLayout layout) =>
      layout == UuidLayout.canonical ? canonical(n) : reversed(n);

  static String get service => canonical(serviceIndex);
  static String get serviceReversed => reversed(serviceIndex);

  static String char(WmpChar c, UuidLayout layout) =>
      forLayout(c.index16, layout);

  /// Which layout (if any) a service UUID string matches.
  static UuidLayout? layoutOfService(String uuid) {
    final u = normalizeUuid(uuid);
    if (u == service) return UuidLayout.canonical;
    if (u == serviceReversed) return UuidLayout.reversed;
    return null;
  }

  /// Standard Device Information service and its firmware revision string.
  static const String deviceInfoService =
      '0000180a-0000-1000-8000-00805f9b34fb';
  static const String firmwareRevision =
      '00002a26-0000-1000-8000-00805f9b34fb';
  static const String serialNumber = '00002a25-0000-1000-8000-00805f9b34fb';
  static const String modelNumber = '00002a24-0000-1000-8000-00805f9b34fb';
}

/// Lowercase, dashed 128-bit form. Accepts 16-bit short forms (`180a`).
String normalizeUuid(String uuid) {
  final s = uuid.trim().toLowerCase();
  final hex = s.replaceAll('-', '');
  if (hex.length == 4) {
    return '0000$hex-0000-1000-8000-00805f9b34fb';
  }
  if (hex.length == 8) {
    return '$hex-0000-1000-8000-00805f9b34fb';
  }
  if (hex.length != 32 || !RegExp(r'^[0-9a-f]{32}$').hasMatch(hex)) {
    throw FormatException('Not a UUID: $uuid');
  }
  return _dash(hex);
}

/// Reverses the 16 bytes of a 128-bit UUID string.
String reverseUuidBytes(String uuid) {
  final hex = normalizeUuid(uuid).replaceAll('-', '');
  final bytes = <String>[
    for (var i = 0; i < 32; i += 2) hex.substring(i, i + 2),
  ];
  return _dash(bytes.reversed.join());
}

String _dash(String hex) =>
    '${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-'
    '${hex.substring(16, 20)}-${hex.substring(20)}';

/// Thrown when a speaker returns bytes that do not match the contract.
class ProtocolException implements Exception {
  ProtocolException(this.message);
  final String message;
  @override
  String toString() => 'ProtocolException: $message';
}

// ---------------------------------------------------------------------------
// Value ranges
// ---------------------------------------------------------------------------

const int kVolumeMin = 0;
const int kVolumeMax = 100;
const int kLatencyMinMs = 120;
const int kLatencyMaxMs = 300;
const int kEqBands = 7;
const int kEqMinDb = -12;
const int kEqMaxDb = 12;
const int kNotifyPeriodMinMs = 10;
const int kNotifyPeriodMaxMs = 1000;
const int kStatusLength = 20;

/// EQ band centre frequencies (Hz), in band order: 63 Hz low shelf,
/// 160 Hz .. 6.3 kHz peaking (Q 1.1), 16 kHz high shelf.
const List<int> kEqBandHz = [63, 160, 400, 1000, 2500, 6300, 16000];

/// Short band labels for the UI.
const List<String> kEqBandLabels = ['63', '160', '400', '1k', '2.5k', '6.3k', '16k'];

/// Peaking band Q (bands 1..5).
const double kEqPeakQ = 1.1;

/// Band count of firmware before 2026-11-19 (60/230/910/3600/14000 Hz).
const int kLegacyEqBands = 5;

// ---------------------------------------------------------------------------
// Status (20 bytes)
// ---------------------------------------------------------------------------

/// Role byte (offset 1).
enum SpeakerRole {
  idle(0),
  playing(1),
  partyMaster(2),
  unknown(-1);

  const SpeakerRole(this.code);
  final int code;

  static SpeakerRole fromCode(int c) => switch (c) {
        0 => SpeakerRole.idle,
        1 => SpeakerRole.playing,
        2 => SpeakerRole.partyMaster,
        _ => SpeakerRole.unknown,
      };
}

/// What the speaker is doing in party terms, derived from role + flags.
enum PartyRole { master, following, solo }

class SpeakerStatus {
  const SpeakerStatus({
    required this.version,
    required this.role,
    required this.phoneConnected,
    required this.bleConnected,
    required this.streaming,
    required this.followingParty,
    required this.preparing,
    required this.sampleRateHz,
    required this.latencyMs,
    required this.volume,
    required this.audioRunning,
    required this.concealedGroups,
    required this.lateGroups,
  });

  final int version;
  final SpeakerRole role;
  final bool phoneConnected;
  final bool bleConnected;
  final bool streaming;
  final bool followingParty;
  final bool preparing;
  final int sampleRateHz;
  final int latencyMs;
  final int volume;
  final bool audioRunning;

  /// Stutter counter: groups the speaker had to conceal.
  final int concealedGroups;
  final int lateGroups;

  /// Decodes the Status characteristic (also returned by reading Control).
  ///
  /// Extra trailing bytes (future firmware) are ignored; fewer than 20 bytes
  /// is an error.
  factory SpeakerStatus.decode(List<int> bytes) {
    if (bytes.length < kStatusLength) {
      throw ProtocolException(
          'Status must be $kStatusLength bytes, got ${bytes.length}');
    }
    final b = ByteData.sublistView(Uint8List.fromList(bytes));
    final link = b.getUint8(2);
    final play = b.getUint8(3);
    return SpeakerStatus(
      version: b.getUint8(0),
      role: SpeakerRole.fromCode(b.getUint8(1)),
      phoneConnected: link & 0x01 != 0,
      bleConnected: link & 0x02 != 0,
      streaming: play & 0x01 != 0,
      followingParty: play & 0x02 != 0,
      preparing: play & 0x04 != 0,
      sampleRateHz: b.getUint32(4, Endian.little),
      latencyMs: b.getUint16(8, Endian.little),
      volume: b.getUint8(10),
      audioRunning: b.getUint8(11) != 0,
      concealedGroups: b.getUint32(12, Endian.little),
      lateGroups: b.getUint32(16, Endian.little),
    );
  }

  /// Encodes back to 20 bytes (used by tests and the demo/fake speaker).
  Uint8List encode() {
    final b = ByteData(kStatusLength);
    b.setUint8(0, version);
    b.setUint8(1, role == SpeakerRole.unknown ? 0xff : role.code);
    b.setUint8(2, (phoneConnected ? 1 : 0) | (bleConnected ? 2 : 0));
    b.setUint8(
        3, (streaming ? 1 : 0) | (followingParty ? 2 : 0) | (preparing ? 4 : 0));
    b.setUint32(4, sampleRateHz, Endian.little);
    b.setUint16(8, latencyMs, Endian.little);
    b.setUint8(10, volume);
    b.setUint8(11, audioRunning ? 1 : 0);
    b.setUint32(12, concealedGroups, Endian.little);
    b.setUint32(16, lateGroups, Endian.little);
    return b.buffer.asUint8List();
  }

  bool get isKnownVersion => version == 1;

  PartyRole get partyRole {
    if (role == SpeakerRole.partyMaster) return PartyRole.master;
    if (followingParty) return PartyRole.following;
    return PartyRole.solo;
  }

  SpeakerStatus copyWith({
    SpeakerRole? role,
    bool? phoneConnected,
    bool? streaming,
    bool? followingParty,
    bool? preparing,
    int? sampleRateHz,
    int? latencyMs,
    int? volume,
    bool? audioRunning,
    int? concealedGroups,
    int? lateGroups,
  }) =>
      SpeakerStatus(
        version: version,
        role: role ?? this.role,
        phoneConnected: phoneConnected ?? this.phoneConnected,
        bleConnected: bleConnected,
        streaming: streaming ?? this.streaming,
        followingParty: followingParty ?? this.followingParty,
        preparing: preparing ?? this.preparing,
        sampleRateHz: sampleRateHz ?? this.sampleRateHz,
        latencyMs: latencyMs ?? this.latencyMs,
        volume: volume ?? this.volume,
        audioRunning: audioRunning ?? this.audioRunning,
        concealedGroups: concealedGroups ?? this.concealedGroups,
        lateGroups: lateGroups ?? this.lateGroups,
      );
}

/// "44.1 kHz", "48 kHz", or null when no stream.
String? formatSampleRate(int hz) {
  if (hz <= 0) return null;
  final k = hz / 1000.0;
  final s = k == k.roundToDouble() ? k.toStringAsFixed(0) : k.toStringAsFixed(1);
  return '$s kHz';
}

// ---------------------------------------------------------------------------
// Speaker channel (stereo pair)
// ---------------------------------------------------------------------------

enum SpeakerChannel {
  stereo(0),
  left(1),
  right(2);

  const SpeakerChannel(this.code);
  final int code;

  static SpeakerChannel fromCode(int c) => switch (c) {
        0 => SpeakerChannel.stereo,
        1 => SpeakerChannel.left,
        2 => SpeakerChannel.right,
        _ => throw ProtocolException('Unknown channel code $c'),
      };
}

/// Party size byte: 1, 2, 3 (= 3 or more) or 0 (unknown / not in a party).
class PartySize {
  const PartySize(this.code);
  final int code;

  bool get known => code > 0;
  bool get isExactlyTwo => code == 2;
  bool get threeOrMore => code >= 3;

  String get label => switch (code) {
        0 => 'Not in a party',
        1 => '1 speaker',
        2 => '2 speakers',
        _ => '3+ speakers',
      };

  @override
  bool operator ==(Object other) => other is PartySize && other.code == code;
  @override
  int get hashCode => code.hashCode;
}

class ChannelInfo {
  const ChannelInfo({
    required this.setting,
    required this.output,
    required this.party,
  });

  /// What the user chose (saved on the speaker).
  final SpeakerChannel setting;

  /// What the speaker plays now.
  final SpeakerChannel output;
  final PartySize party;

  factory ChannelInfo.decode(List<int> bytes) {
    if (bytes.length < 3) {
      throw ProtocolException('Channel must be 3 bytes, got ${bytes.length}');
    }
    return ChannelInfo(
      setting: SpeakerChannel.fromCode(bytes[0]),
      output: SpeakerChannel.fromCode(bytes[1]),
      party: PartySize(bytes[2]),
    );
  }

  Uint8List encode() =>
      Uint8List.fromList([setting.code, output.code, party.code]);

  /// Left/Right only takes effect while the party has exactly 2 speakers.
  bool get pairActive => party.isExactlyTwo && setting != SpeakerChannel.stereo;
}

Uint8List encodeChannelWrite(SpeakerChannel c) => Uint8List.fromList([c.code]);

// ---------------------------------------------------------------------------
// Broadcast
// ---------------------------------------------------------------------------

/// Party codec (Control opcode 0x06).
enum PartyCodec {
  /// SBC only (default).
  sbc(0),

  /// The phone may also use AAC-LC while broadcasting.
  aacLc(1);

  const PartyCodec(this.code);
  final int code;

  String get label => this == PartyCodec.sbc ? 'SBC' : 'AAC-LC';
}

class BroadcastInfo {
  const BroadcastInfo({
    required this.on,
    required this.wifiReady,
    required this.party,
    this.aacAllowed,
    this.handover = 0,
  });

  final bool on;

  /// Whether the Wi-Fi side has finished starting or stopping.
  final bool wifiReady;
  final PartySize party;

  /// Byte 3: whether AAC-LC is allowed in broadcast. Null on older firmware
  /// that only returns 3 bytes (the codec option is then hidden).
  final bool? aacAllowed;

  /// Byte 4: nonzero while the speaker is moving the phone's A2DP stream to
  /// SBC after broadcast turned on (codec handover). 0 on older firmware.
  final int handover;

  factory BroadcastInfo.decode(List<int> bytes) {
    if (bytes.length < 3) {
      throw ProtocolException('Broadcast must be at least 3 bytes, got ${bytes.length}');
    }
    return BroadcastInfo(
      on: bytes[0] != 0,
      wifiReady: bytes[1] != 0,
      party: PartySize(bytes[2]),
      aacAllowed: bytes.length > 3 ? bytes[3] != 0 : null,
      handover: bytes.length > 4 ? bytes[4] : 0,
    );
  }

  /// Encodes the 5-byte read form (or the 3-byte legacy form when
  /// [aacAllowed] is unknown).
  Uint8List encode() => Uint8List.fromList([
        on ? 1 : 0,
        wifiReady ? 1 : 0,
        party.code,
        if (aacAllowed != null) ...[aacAllowed! ? 1 : 0, handover],
      ]);

  /// True while Wi-Fi is still starting/stopping after a toggle.
  bool get transitioning => !wifiReady;

  bool get inHandover => handover != 0;

  /// Whether the firmware supports the party codec option.
  bool get supportsCodecChoice => aacAllowed != null;

  PartyCodec? get codec =>
      aacAllowed == null ? null : (aacAllowed! ? PartyCodec.aacLc : PartyCodec.sbc);

  BroadcastInfo copyWith({bool? on, bool? wifiReady, PartySize? party, bool? aacAllowed, int? handover}) =>
      BroadcastInfo(
        on: on ?? this.on,
        wifiReady: wifiReady ?? this.wifiReady,
        party: party ?? this.party,
        aacAllowed: aacAllowed ?? this.aacAllowed,
        handover: handover ?? this.handover,
      );
}

Uint8List encodeBroadcastWrite(bool on) => Uint8List.fromList([on ? 1 : 0]);

// ---------------------------------------------------------------------------
// Volume / latency / EQ
// ---------------------------------------------------------------------------

void _checkRange(String what, int v, int min, int max) {
  if (v < min || v > max) {
    throw RangeError.range(v, min, max, what);
  }
}

Uint8List encodeVolume(int volume) {
  _checkRange('volume', volume, kVolumeMin, kVolumeMax);
  return Uint8List.fromList([volume]);
}

int decodeVolume(List<int> bytes) {
  if (bytes.isEmpty) throw ProtocolException('Volume is empty');
  return bytes[0];
}

Uint8List encodeLatency(int ms) {
  _checkRange('latency', ms, kLatencyMinMs, kLatencyMaxMs);
  return Uint8List.fromList([ms & 0xff, (ms >> 8) & 0xff]);
}

int decodeLatency(List<int> bytes) {
  if (bytes.length < 2) {
    throw ProtocolException('Latency must be 2 bytes, got ${bytes.length}');
  }
  return bytes[0] | (bytes[1] << 8);
}

/// 7 x int8 dB.
Uint8List encodeEq(List<int> gains) {
  if (gains.length != kEqBands) {
    throw ArgumentError('EQ needs $kEqBands bands, got ${gains.length}');
  }
  for (final g in gains) {
    _checkRange('eq gain', g, kEqMinDb, kEqMaxDb);
  }
  return Uint8List.fromList([for (final g in gains) g & 0xff]);
}

/// Decodes the EQ characteristic. A 5-byte value from older firmware is
/// mapped to 7 bands the same way the speakers migrated their saved setting.
List<int> decodeEq(List<int> bytes) {
  if (bytes.length >= kEqBands) {
    return [for (var i = 0; i < kEqBands; i++) _int8(bytes[i])];
  }
  if (bytes.length == kLegacyEqBands) {
    return migrateEq5to7([for (final b in bytes) _int8(b)]);
  }
  throw ProtocolException('EQ must be $kEqBands bytes, got ${bytes.length}');
}

/// Old bands 60/230/910/3600/14000 Hz become new bands 0/1/3/4/6; new bands
/// 2 (400 Hz) and 5 (6.3 kHz) are the average of their neighbours, truncated
/// toward zero like C integer division.
List<int> migrateEq5to7(List<int> old) {
  if (old.length != kLegacyEqBands) {
    throw ArgumentError('Legacy EQ needs $kLegacyEqBands bands, got ${old.length}');
  }
  return [
    old[0],
    old[1],
    (old[1] + old[2]) ~/ 2,
    old[2],
    old[3],
    (old[3] + old[4]) ~/ 2,
    old[4],
  ];
}

int _int8(int b) => (b & 0x80) != 0 ? (b & 0xff) - 256 : b & 0xff;

int clampEq(int db) => db.clamp(kEqMinDb, kEqMaxDb);

// ---------------------------------------------------------------------------
// Control opcodes (write to ...0002)
// ---------------------------------------------------------------------------

class ControlOp {
  ControlOp._();
  static const int volume = 0x01;
  static const int latency = 0x02;
  static const int channel = 0x03;
  static const int eq = 0x04;
  static const int broadcast = 0x05;
  static const int broadcastCodec = 0x06;
  static const int notifyPeriod = 0x11;

  static Uint8List setVolume(int v) =>
      Uint8List.fromList([volume, ...encodeVolume(v)]);
  static Uint8List setLatency(int ms) =>
      Uint8List.fromList([latency, ...encodeLatency(ms)]);
  static Uint8List setChannel(SpeakerChannel c) =>
      Uint8List.fromList([channel, c.code]);
  static Uint8List setEq(List<int> gains) =>
      Uint8List.fromList([eq, ...encodeEq(gains)]);
  static Uint8List setBroadcast(bool on) =>
      Uint8List.fromList([broadcast, on ? 1 : 0]);
  static Uint8List setBroadcastCodec(PartyCodec c) =>
      Uint8List.fromList([broadcastCodec, c.code]);
  static Uint8List setNotifyPeriod(int ms) {
    _checkRange('notify period', ms, kNotifyPeriodMinMs, kNotifyPeriodMaxMs);
    return Uint8List.fromList([notifyPeriod, ms & 0xff, (ms >> 8) & 0xff]);
  }
}

/// ATT error 0x13: value not allowed (invalid value rejected by the speaker).
const int kAttValueNotAllowed = 0x13;

/// ATT error 0x0D: invalid attribute value length (e.g. an EQ write that is
/// not 7 bytes).
const int kAttInvalidLength = 0x0D;

// ---------------------------------------------------------------------------
// Discovery helpers
// ---------------------------------------------------------------------------

/// Whether an advertisement looks like a WMP speaker.
bool isWmpAdvertisement({required String name, required Iterable<String> serviceUuids}) {
  if (name.startsWith(kAdvertisingNamePrefix)) return true;
  for (final u in serviceUuids) {
    try {
      if (WmpUuids.layoutOfService(u) != null) return true;
    } on FormatException {
      // ignore malformed entries
    }
  }
  return false;
}

/// `WMP-Control-4A2F` -> `4A2F`; null when the name has no WMP suffix.
String? speakerSuffix(String name) {
  if (!name.startsWith(kAdvertisingNamePrefix)) return null;
  final s = name.substring(kAdvertisingNamePrefix.length).trim();
  return s.isEmpty ? null : s;
}

/// Friendly default name: `Speaker 4A2F`.
String friendlySpeakerName(String advName) {
  final suffix = speakerSuffix(advName);
  if (suffix != null) return 'Speaker $suffix';
  return advName.isEmpty ? 'Unnamed speaker' : advName;
}
