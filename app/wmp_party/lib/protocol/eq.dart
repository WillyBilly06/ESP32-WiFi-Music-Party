/// EQ presets and an approximate frequency response for the 5-band speaker EQ.
///
/// Pure Dart (no Flutter) so it can be unit tested.
library;

import 'dart:math' as math;

import 'wmp_protocol.dart';

class EqPreset {
  const EqPreset({
    required this.id,
    required this.name,
    required this.gains,
    this.builtIn = false,
  });

  final String id;
  final String name;
  final List<int> gains;
  final bool builtIn;

  bool matches(List<int> other) {
    if (other.length != gains.length) return false;
    for (var i = 0; i < gains.length; i++) {
      if (gains[i] != other[i]) return false;
    }
    return true;
  }

  Map<String, Object> toJson() => {'id': id, 'name': name, 'gains': gains};

  static EqPreset? fromJson(Object? json) {
    if (json is! Map) return null;
    final id = json['id'];
    final name = json['name'];
    final gains = json['gains'];
    if (id is! String || name is! String || gains is! List) return null;
    if (gains.any((g) => g is! int)) return null;
    var values = [for (final g in gains) clampEq(g as int)];
    // Presets saved by the 5-band app version are migrated like the speakers.
    if (values.length == kLegacyEqBands) values = migrateEq5to7(values);
    if (values.length != kEqBands) return null;
    return EqPreset(id: id, name: name, gains: values);
  }
}

/// Built-in presets. Gains in dB for
/// 63 Hz, 160 Hz, 400 Hz, 1 kHz, 2.5 kHz, 6.3 kHz, 16 kHz.
const List<EqPreset> kBuiltInPresets = [
  EqPreset(id: 'flat', name: 'Flat', gains: [0, 0, 0, 0, 0, 0, 0], builtIn: true),
  EqPreset(id: 'bass', name: 'Bass Boost', gains: [7, 5, 2, 0, 0, 0, 1], builtIn: true),
  EqPreset(id: 'vocal', name: 'Vocal', gains: [-2, -1, 1, 3, 4, 2, 0], builtIn: true),
  EqPreset(id: 'bright', name: 'Bright', gains: [-1, 0, 0, 1, 3, 5, 6], builtIn: true),
  EqPreset(id: 'chill', name: 'Chill', gains: [3, 2, 1, -1, -2, -2, -3], builtIn: true),
  EqPreset(id: 'party', name: 'Party', gains: [6, 4, 0, -2, 1, 3, 5], builtIn: true),
];

/// All-zero gains.
List<int> flatGains() => List<int>.filled(kEqBands, 0);

/// Finds the first preset (built-in first, then custom) equal to [gains].
EqPreset? matchPreset(List<int> gains, [List<EqPreset> custom = const []]) {
  for (final p in [...kBuiltInPresets, ...custom]) {
    if (p.matches(gains)) return p;
  }
  return null;
}

/// Approximate magnitude response (dB) of the speaker EQ at [hz], using RBJ
/// biquads matching the firmware description: 63 Hz low shelf, five peaking
/// bands with Q 1.1 and a 16 kHz high shelf. Used only for drawing the curve.
/// Shelf slope is not specified by the firmware doc; Q 0.707 is assumed.
double eqResponseDb(List<int> gains, double hz, {double sampleRate = 44100}) {
  var total = 0.0;
  for (var i = 0; i < kEqBands; i++) {
    final g = gains[i].toDouble();
    if (g == 0) continue;
    final type = i == 0
        ? _BiquadType.lowShelf
        : i == kEqBands - 1
            ? _BiquadType.highShelf
            : _BiquadType.peaking;
    final q = type == _BiquadType.peaking ? kEqPeakQ : 0.707;
    total += _biquadDb(type, kEqBandHz[i].toDouble(), g, q, hz, sampleRate);
  }
  return total;
}

enum _BiquadType { lowShelf, peaking, highShelf }

double _biquadDb(_BiquadType type, double f0, double gainDb, double q,
    double f, double fs) {
  // Keep the high shelf below Nyquist for low sample rates.
  final fc = math.min(f0, fs * 0.45);
  final a = math.pow(10, gainDb / 40).toDouble();
  final w0 = 2 * math.pi * fc / fs;
  final cosW = math.cos(w0);
  final sinW = math.sin(w0);
  final alpha = sinW / (2 * q);
  double b0, b1, b2, a0, a1, a2;
  switch (type) {
    case _BiquadType.peaking:
      b0 = 1 + alpha * a;
      b1 = -2 * cosW;
      b2 = 1 - alpha * a;
      a0 = 1 + alpha / a;
      a1 = -2 * cosW;
      a2 = 1 - alpha / a;
    case _BiquadType.lowShelf:
      final s = 2 * math.sqrt(a) * alpha;
      b0 = a * ((a + 1) - (a - 1) * cosW + s);
      b1 = 2 * a * ((a - 1) - (a + 1) * cosW);
      b2 = a * ((a + 1) - (a - 1) * cosW - s);
      a0 = (a + 1) + (a - 1) * cosW + s;
      a1 = -2 * ((a - 1) + (a + 1) * cosW);
      a2 = (a + 1) + (a - 1) * cosW - s;
    case _BiquadType.highShelf:
      final s = 2 * math.sqrt(a) * alpha;
      b0 = a * ((a + 1) + (a - 1) * cosW + s);
      b1 = -2 * a * ((a - 1) + (a + 1) * cosW);
      b2 = a * ((a + 1) + (a - 1) * cosW - s);
      a0 = (a + 1) - (a - 1) * cosW + s;
      a1 = 2 * ((a - 1) - (a + 1) * cosW);
      a2 = (a + 1) - (a - 1) * cosW - s;
  }
  final w = 2 * math.pi * f / fs;
  // |H(e^jw)|^2 with z^-1 = e^-jw.
  final c1 = math.cos(w), s1 = math.sin(w);
  final c2 = math.cos(2 * w), s2 = math.sin(2 * w);
  final nr = b0 + b1 * c1 + b2 * c2;
  final ni = -(b1 * s1 + b2 * s2);
  final dr = a0 + a1 * c1 + a2 * c2;
  final di = -(a1 * s1 + a2 * s2);
  final mag2 = (nr * nr + ni * ni) / (dr * dr + di * di);
  return 10 * math.log(mag2) / math.ln10;
}
