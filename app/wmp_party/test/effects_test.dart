import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wmp_party/ble/demo_backend.dart';
import 'package:wmp_party/ble/identity_key.dart';
import 'package:wmp_party/protocol/effects.dart';
import 'package:wmp_party/protocol/wmp_protocol.dart';
import 'package:wmp_party/state/speaker_controller.dart';

const _id = 'AA:BB:CC:00:4A:2F';

void main() {
  group('Effects bytes', () {
    test('UUID and defaults', () {
      expect(WmpUuids.char(WmpChar.effects, UuidLayout.canonical), 'aefb000b-2c51-4b0d-b100-ec5b00000001');
      expect(EffectsSettings.defaults.encode(), [0, 100, 25, 40, 35, 60, 50, 30, 8, 50, 50]);
      expect(EffectsSettings.defaults.anyOn, isFalse);
    });

    test('decode 12-byte read: settings + status bits', () {
      final st = EffectsState.decode([0x05, 90, 10, 20, 30, 40, 50, 60, 12, 70, 80, 0x31]);
      expect(st.settings.isOn(Effect.vocalRemover), isTrue);
      expect(st.settings.isOn(Effect.echo), isFalse);
      expect(st.settings.isOn(Effect.reverb), isTrue);
      expect(st.settings.vocalAmount, 90);
      expect(st.settings.echoDelay, 10);
      expect(st.settings.chorusRate, 12);
      expect(st.settings.chorusMix, 80);
      expect(st.status.running(Effect.vocalRemover), isTrue);
      expect(st.status.running(Effect.reverb), isFalse);
      expect(st.status.following, isTrue);
      expect(st.status.outOfMemory, isTrue);
      expect(() => EffectsState.decode([0, 1, 2]), throwsA(isA<ProtocolException>()));
    });

    test('encode is exactly 11 bytes and clamps every range', () {
      const wild = EffectsSettings(
        flags: 0xff,
        vocalAmount: 150,
        echoDelay: 1,
        echoFeedback: 99,
        echoMix: -5,
        reverbSize: 101,
        reverbDamping: 100,
        reverbMix: 0,
        chorusRate: 0,
        chorusDepth: 200,
        chorusMix: 100,
      );
      final b = wild.encode();
      expect(b.length, 11);
      expect(b, [0x0f, 100, 5, 75, 0, 100, 100, 0, 1, 100, 100]);
      expect(effectsControlWrite(EffectsSettings.defaults), [7, 0, 100, 25, 40, 35, 60, 50, 30, 8, 50, 50]);
    });

    test('presets', () {
      final p = {for (final x in kEffectsPresets) x.id: x};
      expect(kEffectsPresets.map((e) => e.name), ['Off', 'Karaoke', 'Echo hall', 'Chorus']);
      final k = p['karaoke']!.apply(EffectsSettings.defaults);
      expect(k.isOn(Effect.vocalRemover) && k.isOn(Effect.reverb), isTrue);
      expect(k.isOn(Effect.echo) || k.isOn(Effect.chorus), isFalse);
      expect([k.vocalAmount, k.reverbSize, k.reverbDamping, k.reverbMix], [100, 40, 50, 30]);
      final e = p['echohall']!.apply(k);
      expect(e.isOn(Effect.echo) && e.isOn(Effect.reverb), isTrue);
      expect(e.isOn(Effect.vocalRemover), isFalse);
      expect([e.echoDelay, e.echoFeedback, e.echoMix], [25, 40, 35]);
      final c = p['chorus']!.apply(e);
      expect(c.flags, Effect.chorus.bit);
      expect(p['off']!.apply(c).anyOn, isFalse);
      expect(p['karaoke']!.matches(k), isTrue);
      expect(describeEffects(k), 'Vocal remover + Reverb');
      expect(describeEffects(EffectsSettings.defaults), 'Off');
    });
  });

  group('Effects on a speaker', () {
    late DemoBackend backend;

    SpeakerController connected(FakeAsync async) {
      final c = SpeakerController(id: _id, advName: 'WMP-Control-4A2F', backend: backend, identityKey: appIdentityKey);
      c.connect();
      async.elapse(const Duration(seconds: 10));
      expect(c.state, LinkState.ready);
      return c;
    }

    setUp(() => backend = DemoBackend(opDelay: const Duration(milliseconds: 100)));

    test('read on screen open, not during connect', () {
      fakeAsync((async) {
        final c = connected(async);
        expect(c.hasEffects, isTrue);
        expect(backend.log, isNot(contains('read:effects')));
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        expect(c.effects!.settings, EffectsSettings.defaults);
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('slider drag: one write after 150 ms of quiet, then a re-read', () {
      fakeAsync((async) {
        final c = connected(async);
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        backend.log.clear();
        for (var v = 10; v <= 90; v += 10) {
          c.setEffects(c.effects!.settings.copyWith(reverbMix: v));
          async.elapse(const Duration(milliseconds: 40));
        }
        async.elapse(const Duration(seconds: 2));
        expect(backend.log.where((l) => l.startsWith('effects:')).length, 1);
        expect(backend.log.last, 'read:effects');
        expect(backend.speaker(_id).effects[7], 90);
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('slider release writes the final value immediately', () {
      fakeAsync((async) {
        final c = connected(async);
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        backend.log.clear();
        c.setEffects(c.effects!.settings.copyWith(echoMix: 70));
        c.commitEffects();
        async.elapse(const Duration(milliseconds: 120)); // less than the debounce
        expect(backend.log.where((l) => l.startsWith('effects:')).length, 1);
        async.elapse(const Duration(seconds: 1));
        expect(backend.log.where((l) => l.startsWith('effects:')).length, 1, reason: 'no duplicate');
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('preset writes now; status shows a refused effect', () {
      fakeAsync((async) {
        backend.speaker(_id).effectsMemoryShort = true;
        final c = connected(async);
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        final karaoke = kEffectsPresets.firstWhere((p) => p.id == 'karaoke');
        c.setEffectsNow(karaoke.apply(c.effects!.settings));
        async.elapse(const Duration(seconds: 1));
        expect(backend.speaker(_id).effects[0], Effect.vocalRemover.bit | Effect.reverb.bit);
        expect(c.effects!.status.running(Effect.vocalRemover), isTrue);
        expect(c.effects!.status.running(Effect.reverb), isFalse);
        expect(c.effects!.status.outOfMemory, isTrue);
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('a party follower cannot edit', () {
      fakeAsync((async) {
        backend.speaker(_id).following = true;
        final c = connected(async);
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        expect(c.effectsFollowing, isTrue);
        backend.log.clear();
        c.setEffectsNow(c.effects!.settings.withEffect(Effect.echo, true));
        async.elapse(const Duration(seconds: 1));
        expect(backend.log.where((l) => l.startsWith('effects:')), isEmpty);
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('older firmware without aefb000b: no effects', () {
      fakeAsync((async) {
        backend.speaker(_id).hasEffects = false;
        final c = connected(async);
        expect(c.hasEffects, isFalse);
        c.loadEffects();
        async.elapse(const Duration(seconds: 1));
        expect(c.effects, isNull);
        c.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });
  });
}
