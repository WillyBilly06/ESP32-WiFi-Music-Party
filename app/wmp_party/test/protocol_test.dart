import 'package:flutter_test/flutter_test.dart';
import 'package:wmp_party/ble/identity_key.dart';
import 'package:wmp_party/protocol/advert.dart';
import 'package:wmp_party/protocol/eq.dart';
import 'package:wmp_party/protocol/identity.dart';
import 'package:wmp_party/protocol/wmp_protocol.dart';

List<int> _hex(String h) => [for (var i = 0; i < h.length; i += 2) int.parse(h.substring(i, i + 2), radix: 16)];

void main() {
  group('UUIDs', () {
    test('canonical strings match the doc', () {
      expect(WmpUuids.service, 'aefb0001-2c51-4b0d-b100-ec5b00000001');
      expect(WmpUuids.char(WmpChar.control, UuidLayout.canonical),
          'aefb0002-2c51-4b0d-b100-ec5b00000001');
      expect(WmpUuids.char(WmpChar.broadcast, UuidLayout.canonical),
          'aefb0008-2c51-4b0d-b100-ec5b00000001');
    });

    test('reversed form matches the doc alternative', () {
      expect(WmpUuids.serviceReversed, '01000000-5bec-00b1-0d4b-512c0100fbae');
      expect(WmpUuids.char(WmpChar.status, UuidLayout.reversed),
          '01000000-5bec-00b1-0d4b-512c0300fbae');
      expect(reverseUuidBytes(WmpUuids.serviceReversed), WmpUuids.service);
    });

    test('layoutOfService accepts both forms in any case', () {
      expect(WmpUuids.layoutOfService('AEFB0001-2C51-4B0D-B100-EC5B00000001'),
          UuidLayout.canonical);
      expect(WmpUuids.layoutOfService('01000000-5bec-00b1-0d4b-512c0100fbae'),
          UuidLayout.reversed);
      expect(WmpUuids.layoutOfService('180a'), isNull);
    });

    test('normalizeUuid expands short forms and rejects junk', () {
      expect(normalizeUuid('180A'), '0000180a-0000-1000-8000-00805f9b34fb');
      expect(() => normalizeUuid('xyz'), throwsFormatException);
    });
  });

  group('Status', () {
    final sample = <int>[
      1, // version
      2, // role master
      0x03, // phone + BLE connected
      0x05, // streaming + preparing
      0x44, 0xac, 0x00, 0x00, // 44100
      0x96, 0x00, // 150 ms
      42, // volume
      1, // audio running
      0x10, 0x27, 0x00, 0x00, // 10000 concealed
      0x07, 0x00, 0x00, 0x01, // 16777223 late
    ];

    test('decodes every field', () {
      final s = SpeakerStatus.decode(sample);
      expect(s.version, 1);
      expect(s.isKnownVersion, isTrue);
      expect(s.role, SpeakerRole.partyMaster);
      expect(s.partyRole, PartyRole.master);
      expect(s.phoneConnected, isTrue);
      expect(s.bleConnected, isTrue);
      expect(s.streaming, isTrue);
      expect(s.followingParty, isFalse);
      expect(s.preparing, isTrue);
      expect(s.sampleRateHz, 44100);
      expect(s.latencyMs, 150);
      expect(s.volume, 42);
      expect(s.audioRunning, isTrue);
      expect(s.concealedGroups, 10000);
      expect(s.lateGroups, 16777223);
    });

    test('round trips through encode', () {
      expect(SpeakerStatus.decode(sample).encode(), sample);
    });

    test('following and solo roles', () {
      final following = [...sample]
        ..[1] = 1
        ..[3] = 0x02;
      expect(SpeakerStatus.decode(following).partyRole, PartyRole.following);
      final solo = [...sample]
        ..[1] = 0
        ..[3] = 0;
      expect(SpeakerStatus.decode(solo).partyRole, PartyRole.solo);
      expect(SpeakerStatus.decode([...sample]..[1] = 9).role,
          SpeakerRole.unknown);
    });

    test('rejects short payloads, ignores extra bytes', () {
      expect(() => SpeakerStatus.decode(sample.sublist(0, 19)),
          throwsA(isA<ProtocolException>()));
      expect(SpeakerStatus.decode([...sample, 0xff, 0xee]).volume, 42);
    });

    test('sample rate formatting', () {
      expect(formatSampleRate(44100), '44.1 kHz');
      expect(formatSampleRate(48000), '48 kHz');
      expect(formatSampleRate(0), isNull);
    });
  });

  group('Channel / broadcast', () {
    test('channel decode', () {
      final c = ChannelInfo.decode([1, 0, 3]);
      expect(c.setting, SpeakerChannel.left);
      expect(c.output, SpeakerChannel.stereo);
      expect(c.party.threeOrMore, isTrue);
      expect(c.pairActive, isFalse);
      expect(ChannelInfo.decode([2, 2, 2]).pairActive, isTrue);
      expect(ChannelInfo.decode([0, 0, 2]).pairActive, isFalse);
      expect(() => ChannelInfo.decode([5, 0, 0]),
          throwsA(isA<ProtocolException>()));
      expect(() => ChannelInfo.decode([0, 0]),
          throwsA(isA<ProtocolException>()));
      expect(encodeChannelWrite(SpeakerChannel.right), [2]);
    });

    test('party size labels', () {
      expect(const PartySize(0).label, 'Not in a party');
      expect(const PartySize(2).isExactlyTwo, isTrue);
      expect(const PartySize(3).label, '3+ speakers');
    });

    test('broadcast decode/encode', () {
      final b = BroadcastInfo.decode([1, 0, 2]);
      expect(b.on, isTrue);
      expect(b.wifiReady, isFalse);
      expect(b.transitioning, isTrue);
      expect(b.party.code, 2);
      expect(b.encode(), [1, 0, 2]);
      expect(b.aacAllowed, isNull, reason: 'legacy 3-byte form');
      expect(b.handover, 0);
      expect(b.supportsCodecChoice, isFalse);
      expect(b.codec, isNull);
      expect(encodeBroadcastWrite(true), [1]);
      expect(encodeBroadcastWrite(false), [0]);
    });

    test('broadcast 5-byte form: aac_allowed and handover', () {
      final b = BroadcastInfo.decode([1, 1, 3, 1, 2]);
      expect(b.on, isTrue);
      expect(b.party.threeOrMore, isTrue);
      expect(b.aacAllowed, isTrue);
      expect(b.codec, PartyCodec.aacLc);
      expect(b.handover, 2);
      expect(b.inHandover, isTrue);
      expect(b.encode(), [1, 1, 3, 1, 2]);
      final sbc = BroadcastInfo.decode([0, 1, 0, 0, 0, 0xff]);
      expect(sbc.codec, PartyCodec.sbc);
      expect(sbc.inHandover, isFalse);
      expect(() => BroadcastInfo.decode([1, 1]), throwsA(isA<ProtocolException>()));
    });
  });

  group('Scalars', () {
    test('volume', () {
      expect(encodeVolume(0), [0]);
      expect(encodeVolume(100), [100]);
      expect(() => encodeVolume(101), throwsRangeError);
      expect(() => encodeVolume(-1), throwsRangeError);
      expect(decodeVolume([77]), 77);
    });

    test('latency u16 LE 120..300', () {
      expect(encodeLatency(300), [0x2c, 0x01]);
      expect(decodeLatency([0x2c, 0x01]), 300);
      expect(() => encodeLatency(119), throwsRangeError);
      expect(() => encodeLatency(301), throwsRangeError);
    });

    test('eq is 7 x int8', () {
      expect(kEqBands, 7);
      expect(kEqBandHz, [63, 160, 400, 1000, 2500, 6300, 16000]);
      expect(kEqBandLabels, ['63', '160', '400', '1k', '2.5k', '6.3k', '16k']);
      expect(encodeEq([-12, -1, 0, 1, 12, 5, -5]), [0xf4, 0xff, 0, 1, 12, 5, 0xfb]);
      expect(decodeEq([0xf4, 0xff, 0, 1, 12, 5, 0xfb]), [-12, -1, 0, 1, 12, 5, -5]);
      expect(() => encodeEq([0, 0, 0, 0, 0]), throwsArgumentError);
      expect(() => encodeEq([13, 0, 0, 0, 0, 0, 0]), throwsRangeError);
      expect(() => decodeEq([0, 0, 0]), throwsA(isA<ProtocolException>()));
    });

    test('5-band values migrate like the speakers did', () {
      // old 60/230/910/3600/14000 -> new 0/1/3/4/6; 2 and 5 average neighbours.
      expect(migrateEq5to7([1, 2, 4, 6, 8]), [1, 2, 3, 4, 6, 7, 8]);
      // C-style truncation toward zero for odd sums, both signs.
      expect(migrateEq5to7([0, 3, 0, -3, 0]), [0, 3, 1, 0, -3, -1, 0]);
      expect(decodeEq([7, 4, 0, 0, 1]), [7, 4, 2, 0, 0, 0, 1]);
      expect(() => migrateEq5to7([0, 0, 0]), throwsArgumentError);
    });
  });

  group('Control opcodes', () {
    test('match the doc table', () {
      expect(ControlOp.setVolume(55), [0x01, 55]);
      expect(ControlOp.setLatency(200), [0x02, 0xc8, 0x00]);
      expect(ControlOp.setChannel(SpeakerChannel.left), [0x03, 1]);
      expect(ControlOp.setEq([1, -2, 3, -4, 5, -6, 7]),
          [0x04, 1, 0xfe, 3, 0xfc, 5, 0xfa, 7]);
      expect(ControlOp.setEq(List.filled(7, 0)).length, 8);
      expect(ControlOp.setBroadcast(true), [0x05, 1]);
      expect(ControlOp.setBroadcastCodec(PartyCodec.sbc), [0x06, 0]);
      expect(ControlOp.setBroadcastCodec(PartyCodec.aacLc), [0x06, 1]);
      expect(ControlOp.setNotifyPeriod(1000), [0x11, 0xe8, 0x03]);
      expect(() => ControlOp.setNotifyPeriod(5), throwsRangeError);
    });
  });

  group('Advert filter', () {
    final service = ['aefb0001-2c51-4b0d-b100-ec5b00000001'];
    WmpAdvert? parse({List<String>? uuids, Map<int, List<int>>? md}) => parseWmpAdvert(
          serviceUuids: uuids ?? service,
          manufacturerData: md ?? {0xFFFF: [0x57, 0x4D, 1, 1, 0x15]},
        );

    test('accepts a correct advert and decodes the status bits', () {
      final a = parse()!;
      expect(a.version, 1);
      expect(a.product, WmpProduct.esp32Pcm5102a);
      // 0x15 = streaming | party master | phone connected
      expect(a.status.streaming, isTrue);
      expect(a.status.broadcastOn, isFalse);
      expect(a.status.partyMaster, isTrue);
      expect(a.status.following, isFalse);
      expect(a.status.phoneConnected, isTrue);
      expect(a.status.appConnected, isFalse);
      expect(a.status.describe(), 'Party host · Playing');
      expect(const AdvertStatus(0x2A).describe(), 'In a party · In use by another app');
      // longer payloads (future fields) are fine
      expect(parse(md: {0xFFFF: [0x57, 0x4D, 1, 1, 0, 9, 9]}), isNotNull);
    });

    test('rejects a wrong company id', () {
      expect(parse(md: {0x0059: [0x57, 0x4D, 1, 1, 0]}), isNull);
    });

    test('rejects a payload without WM', () {
      expect(parse(md: {0xFFFF: [0x57, 0x58, 1, 1, 0]}), isNull);
      expect(parse(md: {0xFFFF: [0x00, 0x4D, 1, 1, 0]}), isNull);
    });

    test('rejects an unknown product, a wrong version or a short payload', () {
      expect(parse(md: {0xFFFF: [0x57, 0x4D, 1, 2, 0]}), isNull);
      expect(parse(md: {0xFFFF: [0x57, 0x4D, 1, 0, 0]}), isNull);
      expect(parse(md: {0xFFFF: [0x57, 0x4D, 2, 1, 0]}), isNull);
      expect(parse(md: {0xFFFF: [0x57, 0x4D, 1, 1]}), isNull);
    });

    test('rejects the service UUID without manufacturer data', () {
      expect(parse(md: {}), isNull);
    });

    test('rejects manufacturer data without the service UUID; names never count', () {
      expect(parse(uuids: []), isNull);
      expect(parse(uuids: ['0000180a-0000-1000-8000-00805f9b34fb']), isNull);
    });

    test('display names', () {
      expect(speakerSuffix('WMP-Control-4A2F'), '4A2F');
      expect(friendlySpeakerName('WMP-Control-4A2F'), 'Speaker 4A2F');
      expect(friendlySpeakerName('', 'AA:BB:CC:00:4A:2F'), 'Speaker 4A2F');
      expect(friendlySpeakerName(''), 'Unnamed speaker');
    });
  });

  group('Firmware revision', () {
    test('aefb000a UUID and UTF-8 decode', () {
      expect(WmpUuids.char(WmpChar.firmware, UuidLayout.canonical), 'aefb000a-2c51-4b0d-b100-ec5b00000001');
      expect(decodeFirmwareRevision([0x62, 0x31, 0x00, 0x00]), 'b1'); // trailing NULs dropped
      expect(decodeFirmwareRevision([0xc3, 0xa9, 0x20]), 'é');
      expect(decodeFirmwareRevision([]), '');
    });
  });

  group('Identity', () {
    final appNonce = [0, 1, 2, 3, 4, 5, 6, 7];
    final speakerNonce = [8, 9, 10, 11, 12, 13, 14, 15];

    test('app key is the public "WMP development identity key v01", id 0', () {
      expect(String.fromCharCodes(appIdentityKey.key), 'WMP development identity key v01');
      expect(appIdentityKey.key.length, 32);
      expect(appIdentityKey.id, 0);
      expect(wmpIdentityKeyId, 0);
      expect(wmpIdentityKey, appIdentityKey.key);
      expect(appIdentityKey.toString(), isNot(contains('0x')), reason: 'never print key bytes');
    });

    test('test vectors (public key)', () {
      expect(speakerTag(appIdentityKey.key, appNonce, speakerNonce), _hex('086bf272ac312b22ac54'));
      expect(appTag(appIdentityKey.key, speakerNonce, appNonce), _hex('34a37db566c82c07dc18'));
    });

    test('handshake bytes', () {
      expect(challengeWrite(appNonce), [0x01, ...appNonce]);
      final challenge = [1, 0, ...speakerNonce, ..._hex('086bf272ac312b22ac54')];
      expect(challenge.length, 20);
      expect(verifySpeakerAndProve(appIdentityKey, appNonce, challenge),
          [0x02, ..._hex('34a37db566c82c07dc18')]);
    });

    test('a wrong tag, wrong state or short read is not our speaker', () {
      final good = [1, 0, ...speakerNonce, ..._hex('086bf272ac312b22ac54')];
      Matcher fails(IdentityFailure f) =>
          throwsA(isA<IdentityException>().having((e) => e.failure, 'failure', f));
      final badTag = [...good]..[19] ^= 1;
      expect(() => verifySpeakerAndProve(appIdentityKey, appNonce, badTag), fails(IdentityFailure.notOurSpeaker));
      final badState = [...good]..[0] = 2;
      expect(() => verifySpeakerAndProve(appIdentityKey, appNonce, badState), fails(IdentityFailure.notOurSpeaker));
      expect(() => verifySpeakerAndProve(appIdentityKey, appNonce, good.sublist(0, 19)),
          fails(IdentityFailure.notOurSpeaker));
      final otherKeyId = [...good]..[1] = 1;
      expect(() => verifySpeakerAndProve(appIdentityKey, appNonce, otherKeyId), fails(IdentityFailure.keyMismatch));
      expect(IdentityException(IdentityFailure.notOurSpeaker).message, 'Not a Music Party speaker');
      expect(IdentityException(IdentityFailure.keyMismatch).message, 'Speaker needs a firmware update');
    });

    test('constant-time compare', () {
      expect(constantTimeEquals([1, 2, 3], [1, 2, 3]), isTrue);
      expect(constantTimeEquals([1, 2, 3], [1, 2, 4]), isFalse);
      expect(constantTimeEquals([1, 2, 3], [1, 2]), isFalse);
    });

    test('nonces are 8 random bytes', () {
      final a = randomNonce();
      final b = randomNonce();
      expect(a.length, 8);
      expect(a, isNot(equals(b)));
    });
  });

  group('EQ presets', () {
    test('all built-ins are in range', () {
      for (final p in kBuiltInPresets) {
        expect(() => encodeEq(p.gains), returnsNormally, reason: p.name);
      }
      expect(kBuiltInPresets.map((p) => p.name),
          containsAll(['Flat', 'Bass Boost', 'Vocal', 'Bright', 'Chill']));
    });

    test('matchPreset', () {
      expect(matchPreset(flatGains())?.id, 'flat');
      expect(matchPreset(List.filled(7, 1)), isNull);
      final custom = EqPreset(id: 'c1', name: 'Mine', gains: List.filled(7, 1));
      expect(matchPreset(List.filled(7, 1), [custom])?.name, 'Mine');
      for (final p in kBuiltInPresets) {
        expect(p.gains.length, 7, reason: p.name);
      }
    });

    test('json round trip and validation', () {
      const p = EqPreset(id: 'c1', name: 'Mine', gains: [1, -2, 3, -4, 5, -6, 7]);
      final back = EqPreset.fromJson(p.toJson())!;
      expect(back.gains, p.gains);
      expect(EqPreset.fromJson({'id': 'x', 'name': 'y', 'gains': [1]}), isNull);
      // A preset saved by the 5-band app is migrated, not dropped.
      final old = EqPreset.fromJson({'id': 'o', 'name': 'Old', 'gains': [1, 2, 4, 6, 8]})!;
      expect(old.gains, [1, 2, 3, 4, 6, 7, 8]);
      expect(EqPreset.fromJson('nope'), isNull);
    });

    test('response curve follows the gains', () {
      expect(eqResponseDb(flatGains(), 1000), closeTo(0, 1e-9));
      expect(eqResponseDb([12, 0, 0, 0, 0, 0, 0], 20), greaterThan(10));
      expect(eqResponseDb([0, 0, 0, 6, 0, 0, 0], 1000), closeTo(6, 0.3));
      expect(eqResponseDb([0, 0, 0, 0, 0, 6, 0], 6300), closeTo(6, 0.5));
      expect(eqResponseDb([0, 0, 0, 0, 0, 0, -12], 19000), lessThan(-6));
    });
  });
}
