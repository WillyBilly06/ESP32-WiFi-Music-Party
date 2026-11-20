/// Karaoke / effects characteristic aefb000b (pure Dart).
///
/// Read: 12 bytes = the 11 setting bytes + a status byte.
/// Write: exactly the 11 setting bytes (or Control opcode 7 + the 11 bytes).
/// Out-of-range values are rejected by the speaker, so everything here is
/// clamped before it is encoded.
library;

import 'dart:typed_data';

import 'wmp_protocol.dart';

const int kEffectsSettingsLength = 11;
const int kEffectsReadLength = 12;
const int kControlOpEffects = 0x07;

/// Inclusive range of one setting byte.
class EffectRange {
  const EffectRange(this.min, this.max);
  final int min;
  final int max;
  int clamp(int v) => v.clamp(min, max);
}

/// Ranges of bytes 1..10, in byte order.
const List<EffectRange> kEffectRanges = [
  EffectRange(0, 100), // 1 vocal remover amount %
  EffectRange(5, 25), // 2 echo delay, x10 ms
  EffectRange(0, 75), // 3 echo feedback %
  EffectRange(0, 100), // 4 echo mix %
  EffectRange(0, 100), // 5 reverb size
  EffectRange(0, 100), // 6 reverb damping
  EffectRange(0, 100), // 7 reverb mix %
  EffectRange(1, 50), // 8 chorus rate, x0.1 Hz
  EffectRange(0, 100), // 9 chorus depth %
  EffectRange(0, 100), // 10 chorus mix %
];

enum Effect {
  vocalRemover(0x01, 'Vocal remover'),
  echo(0x02, 'Echo'),
  reverb(0x04, 'Reverb'),
  chorus(0x08, 'Chorus');

  const Effect(this.bit, this.label);
  final int bit;
  final String label;
}

class EffectsSettings {
  const EffectsSettings({
    this.flags = 0,
    this.vocalAmount = 100,
    this.echoDelay = 25,
    this.echoFeedback = 40,
    this.echoMix = 35,
    this.reverbSize = 60,
    this.reverbDamping = 50,
    this.reverbMix = 30,
    this.chorusRate = 8,
    this.chorusDepth = 50,
    this.chorusMix = 50,
  });

  /// bit0 vocal remover, bit1 echo, bit2 reverb, bit3 chorus.
  final int flags;
  final int vocalAmount;

  /// Units of 10 ms (5..25 = 50..250 ms).
  final int echoDelay;
  final int echoFeedback;
  final int echoMix;
  final int reverbSize;
  final int reverbDamping;
  final int reverbMix;

  /// Units of 0.1 Hz (1..50).
  final int chorusRate;
  final int chorusDepth;
  final int chorusMix;

  static const EffectsSettings defaults = EffectsSettings();

  bool isOn(Effect e) => flags & e.bit != 0;
  bool get anyOn => flags & 0x0f != 0;

  List<int> get _values => [
        vocalAmount,
        echoDelay,
        echoFeedback,
        echoMix,
        reverbSize,
        reverbDamping,
        reverbMix,
        chorusRate,
        chorusDepth,
        chorusMix,
      ];

  static EffectsSettings _fromValues(int flags, List<int> v) => EffectsSettings(
        flags: flags & 0x0f,
        vocalAmount: v[0],
        echoDelay: v[1],
        echoFeedback: v[2],
        echoMix: v[3],
        reverbSize: v[4],
        reverbDamping: v[5],
        reverbMix: v[6],
        chorusRate: v[7],
        chorusDepth: v[8],
        chorusMix: v[9],
      );

  /// Every value forced into its range (the UI never sends a rejected value).
  EffectsSettings clamped() => _fromValues(
        flags,
        [for (var i = 0; i < kEffectRanges.length; i++) kEffectRanges[i].clamp(_values[i])],
      );

  /// The 11 setting bytes to write.
  Uint8List encode() {
    final c = clamped();
    return Uint8List.fromList([c.flags, ...c._values]);
  }

  factory EffectsSettings.decode(List<int> b) {
    if (b.length < kEffectsSettingsLength) {
      throw ProtocolException('Effects must be at least $kEffectsSettingsLength bytes, got ${b.length}');
    }
    return _fromValues(b[0], b.sublist(1, kEffectsSettingsLength));
  }

  EffectsSettings withEffect(Effect e, bool on) => copyWith(flags: on ? flags | e.bit : flags & ~e.bit);

  EffectsSettings copyWith({
    int? flags,
    int? vocalAmount,
    int? echoDelay,
    int? echoFeedback,
    int? echoMix,
    int? reverbSize,
    int? reverbDamping,
    int? reverbMix,
    int? chorusRate,
    int? chorusDepth,
    int? chorusMix,
  }) =>
      EffectsSettings(
        flags: flags ?? this.flags,
        vocalAmount: vocalAmount ?? this.vocalAmount,
        echoDelay: echoDelay ?? this.echoDelay,
        echoFeedback: echoFeedback ?? this.echoFeedback,
        echoMix: echoMix ?? this.echoMix,
        reverbSize: reverbSize ?? this.reverbSize,
        reverbDamping: reverbDamping ?? this.reverbDamping,
        reverbMix: reverbMix ?? this.reverbMix,
        chorusRate: chorusRate ?? this.chorusRate,
        chorusDepth: chorusDepth ?? this.chorusDepth,
        chorusMix: chorusMix ?? this.chorusMix,
      );

  @override
  bool operator ==(Object other) {
    if (other is! EffectsSettings || other.flags != flags) return false;
    final a = _values, b = other._values;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }

  @override
  int get hashCode => Object.hash(flags, Object.hashAll(_values));
}

/// Status byte (read only).
class EffectsStatus {
  const EffectsStatus(this.bits);
  final int bits;

  bool running(Effect e) => bits & e.bit != 0;

  /// This speaker follows the party host and plays the host's effects.
  bool get following => bits & 0x10 != 0;

  /// An enabled effect was refused for lack of memory.
  bool get outOfMemory => bits & 0x20 != 0;
}

class EffectsState {
  const EffectsState(this.settings, this.status);
  final EffectsSettings settings;
  final EffectsStatus status;

  /// Decodes the 12-byte read (a missing status byte reads as 0).
  factory EffectsState.decode(List<int> b) => EffectsState(
        EffectsSettings.decode(b),
        EffectsStatus(b.length > kEffectsSettingsLength ? b[kEffectsSettingsLength] : 0),
      );
}

/// Control characteristic form: [7, 11 bytes].
Uint8List effectsControlWrite(EffectsSettings s) => Uint8List.fromList([kControlOpEffects, ...s.encode()]);

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

class EffectsPreset {
  const EffectsPreset(this.id, this.name, this.apply);
  final String id;
  final String name;

  /// Applies the preset to the current settings (keeps unrelated values).
  final EffectsSettings Function(EffectsSettings current) apply;

  bool matches(EffectsSettings s) => apply(s) == s;
}

final List<EffectsPreset> kEffectsPresets = [
  EffectsPreset('off', 'Off', (s) => s.copyWith(flags: 0)),
  EffectsPreset(
    'karaoke',
    'Karaoke',
    (s) => s.copyWith(
      flags: Effect.vocalRemover.bit | Effect.reverb.bit,
      vocalAmount: 100,
      reverbSize: 40,
      reverbDamping: 50,
      reverbMix: 30,
    ),
  ),
  EffectsPreset(
    'echohall',
    'Echo hall',
    (s) => s.copyWith(
      flags: Effect.echo.bit | Effect.reverb.bit,
      echoDelay: 25,
      echoFeedback: 40,
      echoMix: 35,
      reverbSize: EffectsSettings.defaults.reverbSize,
      reverbDamping: EffectsSettings.defaults.reverbDamping,
      reverbMix: EffectsSettings.defaults.reverbMix,
    ),
  ),
  EffectsPreset(
    'chorus',
    'Chorus',
    (s) => s.copyWith(
      flags: Effect.chorus.bit,
      chorusRate: EffectsSettings.defaults.chorusRate,
      chorusDepth: EffectsSettings.defaults.chorusDepth,
      chorusMix: EffectsSettings.defaults.chorusMix,
    ),
  ),
];

/// Short summary for the speaker page, e.g. "Vocal remover + Reverb".
String describeEffects(EffectsSettings s) {
  final on = [for (final e in Effect.values) if (s.isOn(e)) e.label];
  return on.isEmpty ? 'Off' : on.join(' + ');
}
