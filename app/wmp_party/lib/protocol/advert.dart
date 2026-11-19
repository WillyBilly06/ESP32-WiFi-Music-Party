/// Music Party advertisement filter (pure Dart).
///
/// A speaker advertises flags, the complete 128-bit control service UUID and
/// manufacturer data with company id 0xFFFF and the payload
/// `['W', 'M', version, product, status]`; its name is in the scan response.
/// A device is a Music Party speaker only when all of that matches. The name
/// is never used to decide.
library;

import 'wmp_protocol.dart';

/// Company id used in the manufacturer data (0xFFFF: reserved for testing /
/// not assigned, as the firmware uses).
const int kWmpCompanyId = 0xFFFF;
const int kWmpAdvertVersion = 1;
const int kWmpAdvertMinLength = 5;

/// Known product ids.
enum WmpProduct {
  /// ESP32 + PCM5102A speaker.
  esp32Pcm5102a(1);

  const WmpProduct(this.code);
  final int code;

  static WmpProduct? fromCode(int c) {
    for (final p in values) {
      if (p.code == c) return p;
    }
    return null;
  }
}

/// Status byte of the advert.
class AdvertStatus {
  const AdvertStatus(this.bits);
  final int bits;

  bool get streaming => bits & 0x01 != 0;
  bool get broadcastOn => bits & 0x02 != 0;
  bool get partyMaster => bits & 0x04 != 0;
  bool get following => bits & 0x08 != 0;
  bool get phoneConnected => bits & 0x10 != 0;

  /// Another BLE app is already connected to this speaker.
  bool get appConnected => bits & 0x20 != 0;

  /// Short description for the scan list, e.g. "Playing · Party host".
  String describe() => [
        if (partyMaster) 'Party host' else if (following) 'In a party' else if (broadcastOn) 'Broadcast on',
        if (streaming) 'Playing' else if (phoneConnected) 'Phone connected',
        if (appConnected) 'In use by another app',
      ].join(' · ');
}

class WmpAdvert {
  const WmpAdvert({required this.version, required this.product, required this.status});
  final int version;
  final WmpProduct product;
  final AdvertStatus status;
}

/// Returns the parsed advert, or null when this is not a Music Party speaker.
///
/// [manufacturerData] is keyed by company id with the payload after the id
/// (as flutter_blue_plus reports it).
WmpAdvert? parseWmpAdvert({
  required Iterable<String> serviceUuids,
  required Map<int, List<int>> manufacturerData,
}) {
  var hasService = false;
  for (final u in serviceUuids) {
    try {
      if (WmpUuids.layoutOfService(u) != null) hasService = true;
    } on FormatException {
      // malformed entry: ignore
    }
  }
  if (!hasService) return null;
  final m = manufacturerData[kWmpCompanyId];
  if (m == null || m.length < kWmpAdvertMinLength) return null;
  if (m[0] != 0x57 || m[1] != 0x4D) return null; // 'W' 'M'
  if (m[2] != kWmpAdvertVersion) return null;
  final product = WmpProduct.fromCode(m[3]);
  if (product == null) return null;
  return WmpAdvert(version: m[2], product: product, status: AdvertStatus(m[4]));
}

/// Builds the manufacturer payload (used by the demo speakers and tests).
List<int> buildWmpAdvertPayload({int version = kWmpAdvertVersion, int product = 1, int status = 0}) =>
    [0x57, 0x4D, version, product, status];
