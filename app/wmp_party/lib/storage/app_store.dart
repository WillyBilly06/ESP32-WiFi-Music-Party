/// Local persistence: known speakers and user EQ presets.
library;

import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../protocol/eq.dart';

class KnownSpeaker {
  const KnownSpeaker({
    required this.id,
    required this.advName,
    this.nickname,
    required this.lastConnected,
  });

  final String id;
  final String advName;
  final String? nickname;
  final DateTime lastConnected;

  KnownSpeaker copyWith({String? advName, String? nickname, bool clearNickname = false, DateTime? lastConnected}) =>
      KnownSpeaker(
        id: id,
        advName: advName ?? this.advName,
        nickname: clearNickname ? null : (nickname ?? this.nickname),
        lastConnected: lastConnected ?? this.lastConnected,
      );

  Map<String, Object?> toJson() => {
        'id': id,
        'advName': advName,
        'nickname': nickname,
        'last': lastConnected.millisecondsSinceEpoch,
      };

  static KnownSpeaker? fromJson(Object? j) {
    if (j is! Map) return null;
    final id = j['id'];
    final adv = j['advName'];
    final last = j['last'];
    if (id is! String || adv is! String) return null;
    final nick = j['nickname'];
    return KnownSpeaker(
      id: id,
      advName: adv,
      nickname: nick is String && nick.isNotEmpty ? nick : null,
      lastConnected: DateTime.fromMillisecondsSinceEpoch(last is int ? last : 0),
    );
  }
}

/// Storage interface (an in-memory version is used by tests).
abstract class AppStore {
  Future<List<KnownSpeaker>> loadSpeakers();
  Future<void> saveSpeakers(List<KnownSpeaker> speakers);
  Future<List<EqPreset>> loadPresets();
  Future<void> savePresets(List<EqPreset> presets);
}

class PrefsAppStore implements AppStore {
  static const _kSpeakers = 'known_speakers_v1';
  static const _kPresets = 'eq_presets_v1';

  Future<SharedPreferences> get _prefs => SharedPreferences.getInstance();

  @override
  Future<List<KnownSpeaker>> loadSpeakers() async {
    final raw = (await _prefs).getString(_kSpeakers);
    return _decodeList(raw, KnownSpeaker.fromJson);
  }

  @override
  Future<void> saveSpeakers(List<KnownSpeaker> speakers) async {
    await (await _prefs).setString(
        _kSpeakers, jsonEncode([for (final s in speakers) s.toJson()]));
  }

  @override
  Future<List<EqPreset>> loadPresets() async {
    final raw = (await _prefs).getString(_kPresets);
    return _decodeList(raw, EqPreset.fromJson);
  }

  @override
  Future<void> savePresets(List<EqPreset> presets) async {
    await (await _prefs).setString(
        _kPresets, jsonEncode([for (final p in presets) p.toJson()]));
  }

  static List<T> _decodeList<T>(String? raw, T? Function(Object?) f) {
    if (raw == null || raw.isEmpty) return [];
    try {
      final list = jsonDecode(raw);
      if (list is! List) return [];
      return [for (final e in list) ?f(e)];
    } on FormatException {
      return [];
    }
  }
}

class MemoryAppStore implements AppStore {
  MemoryAppStore({List<KnownSpeaker>? speakers, List<EqPreset>? presets})
      : speakers = speakers ?? [],
        presets = presets ?? [];

  List<KnownSpeaker> speakers;
  List<EqPreset> presets;

  @override
  Future<List<KnownSpeaker>> loadSpeakers() async => [...speakers];
  @override
  Future<void> saveSpeakers(List<KnownSpeaker> s) async => speakers = [...s];
  @override
  Future<List<EqPreset>> loadPresets() async => [...presets];
  @override
  Future<void> savePresets(List<EqPreset> p) async => presets = [...p];
}
