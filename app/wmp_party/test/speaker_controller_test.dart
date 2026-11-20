import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wmp_party/ble/ble_backend.dart';
import 'package:wmp_party/ble/demo_backend.dart';
import 'package:wmp_party/ble/identity_key.dart';
import 'package:wmp_party/ble/permissions.dart';
import 'package:wmp_party/protocol/identity.dart';
import 'package:wmp_party/protocol/wmp_protocol.dart';
import 'package:wmp_party/state/app_state.dart';
import 'package:wmp_party/state/speaker_controller.dart';
import 'package:wmp_party/storage/app_store.dart';

class _Granted implements PermissionGate {
  @override
  Future<PermissionState> check() async => PermissionState.granted;
  @override
  Future<PermissionState> request() async => PermissionState.granted;
  @override
  Future<void> openSettings() async {}
  @override
  Future<bool> needsLocation() async => false;
}

const _id = 'AA:BB:CC:00:4A:2F';

void main() {
  late DemoBackend backend;

  // A slow link like the real speaker: every GATT op takes ~0.8 s.
  DemoBackend slowBackend() => DemoBackend(
        opDelay: const Duration(milliseconds: 800),
        connectDelay: const Duration(seconds: 1),
      );

  SpeakerController controller({bool Function()? adapterOn, IdentityKey key = appIdentityKey}) => SpeakerController(
        id: _id,
        advName: 'WMP-Control-4A2F',
        backend: backend,
        adapterOn: adapterOn,
        identityKey: key,
      );

  /// Log with the random identity payloads replaced by their step names.
  List<String> steps() => [
        for (final l in backend.log)
          l.startsWith('identity:1,')
              ? 'identity:challenge'
              : l.startsWith('identity:2,')
                  ? 'identity:proof'
                  : l,
      ];

  test('connect reads status, settings and firmware', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      final stages = <ConnectStage?>[];
      c.addListener(() => stages.add(c.stage));
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(c.status!.streaming, isTrue);
      expect(c.status!.sampleRateHz, 44100);
      expect(c.volume, 35);
      expect(c.eq, List.filled(7, 0));
      expect(c.channel!.setting, SpeakerChannel.stereo);
      expect(c.broadcast!.on, isFalse);
      expect(c.firmware, 'demo-2026-11-18');
      expect(stages, containsAll([ConnectStage.discovering, ConnectStage.reading]));
      c.dispose();
      async.flushTimers();
    });
  });

  test('connect: low power first, one discovery, low power again, minimal reads', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(steps(), [
        'connect:$_id',
        'priority:lowPower', // right after connect, before discovery
        'mtu:517', // one MTU exchange, before discovery
        'discover:$_id', // exactly once
        'identity:challenge', // identity: 1 write + 1 read + 1 write,
        'read:identity', //   before any other write or CCCD
        'identity:proof',
        'read:status',
        'subscribe:status',
        'priority:lowPower', // after discovery and subscription
        'read:volume',
        'read:eq',
        'read:channel',
        'read:broadcast',
        'read:firmware', // aefb000a, after the UI is ready
      ]);
      expect(backend.log.where((l) => l.startsWith('discover:')).length, 1);
      expect(backend.log.where((l) => l.startsWith('mtu:')).length, 1);
      expect(backend.log, isNot(contains('read:latency')), reason: 'latency is in Status');
      c.dispose();
      async.flushTimers();
    });
  });

  test('identity handshake verifies the speaker before anything else', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(c.identityVerified, isTrue);
      final id = steps().where((l) => l.contains('identity')).toList();
      expect(id, ['identity:challenge', 'read:identity', 'identity:proof']);
      c.dispose();
      async.flushTimers();
    });
  });

  test('an impostor is disconnected with "Not a Music Party speaker", no retry', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).impostor = true;
      final c = controller();
      final notices = <String>[];
      c.notices.listen(notices.add);
      c.connect();
      async.elapse(const Duration(seconds: 60));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Not a Music Party speaker');
      expect(c.identityVerified, isFalse);
      // Challenge written and read, no proof sent, nothing else written.
      expect(steps().where((l) => !l.startsWith('read:') && !l.startsWith('priority') && !l.startsWith('discover') && !l.startsWith('mtu:')).toList(),
          ['connect:$_id', 'identity:challenge']);
      expect(backend.log.where((l) => l.startsWith('connect:')).length, 1, reason: 'no silent retry');
      expect(notices.single, contains('Not a Music Party speaker'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('a different key id asks for a firmware update', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).identityKey = IdentityKey(1, List.filled(32, 7));
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 30));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Speaker needs a firmware update');
      expect(backend.log.where((l) => l.startsWith('connect:')).length, 1);
      c.dispose();
      async.flushTimers();
    });
  });

  test('firmware without the identity characteristic asks for an update', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).hasIdentity = false;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 30));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Speaker needs a firmware update');
      c.dispose();
      async.flushTimers();
    });
  });

  test('a proof the speaker refuses (ATT 0x08) is reported, no retry', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller(key: IdentityKey(0, List.filled(32, 1)));
      // Same key id, different secret: the speaker tag check fails first.
      c.connect();
      async.elapse(const Duration(seconds: 30));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Not a Music Party speaker');
      expect(describeBleError(DemoAttError(kAttInsufficientAuthorization)), 'The speaker did not accept this app');
      c.dispose();
      async.flushTimers();
    });
  });

  test('writes before the proof are refused by the demo speaker like the firmware', () {
    fakeAsync((async) {
      backend = slowBackend();
      Object? err;
      backend.connect(_id).then((link) => link.write(WmpChar.volume, [10])).catchError((Object e) {
        err = e;
      });
      async.elapse(const Duration(seconds: 10));
      expect(isAuthorizationError(err!), isTrue);
      backend.dispose();
      async.flushTimers();
    });
  });

  test('firmware revision comes from aefb000a when present', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.firmware, 'demo-2026-11-18');
      expect(backend.log, contains('read:firmware'));
      expect(backend.log, isNot(contains('read:2a26')));
      c.dispose();
      async.flushTimers();
    });
  });

  test('older firmware: falls back to Device Information 2a26', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).firmwareInControl = false;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(c.firmware, 'demo-2026-11-18');
      expect(backend.log, contains('read:2a26'));
      expect(backend.log, isNot(contains('read:firmware')));
      c.dispose();
      async.flushTimers();
    });
  });

  test('a speaker without Device Information still connects', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).firmware = null;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(c.firmware, isNull);
      expect(c.eq, isNotNull);
      c.dispose();
      async.flushTimers();
    });
  });

  test('discovery refused as busy by Android is retried, not abandoned', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).busyDiscoveries = 2;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 20));
      expect(c.state, LinkState.ready);
      expect(c.connectLog.where((l) => l.contains('busy, retry')).length, 2);
      expect(c.connectLog.last, endsWith('ready'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('an abandoned manual connect always says why (message + log)', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).busyDiscoveries = 10;
      final c = controller();
      final notices = <String>[];
      c.notices.listen(notices.add);
      c.connect();
      async.elapse(const Duration(seconds: 20));
      expect(c.state, LinkState.failed);
      expect(c.error, startsWith('Could not connect: Service discovery failed'));
      expect(notices.single, contains('Service discovery failed'));
      expect(c.connectLog.first, endsWith('connect'));
      expect(c.connectLog.last, contains('failed'));
      // Manual connects are not retried behind the user's back.
      expect(backend.log.where((l) => l.startsWith('connect:')).length, 1);
      c.dispose();
      async.flushTimers();
    });
  });

  test('a stale GATT cache is cleared and discovery repeated once', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).staleGattCache = true;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 20));
      expect(c.state, LinkState.ready);
      expect(c.identityVerified, isTrue);
      expect(backend.log.where((l) => l == 'clear-cache').length, 1);
      expect(backend.log.where((l) => l.startsWith('discover:')).length, 2);
      final log = c.connectLog.join('\n');
      expect(log, contains('missing identity'));
      expect(log, contains('cleared GATT cache'));
      expect(log, contains('rediscovered'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('without a stale cache there is no cache clear and one discovery', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.state, LinkState.ready);
      expect(backend.log, isNot(contains('clear-cache')));
      expect(backend.log.where((l) => l.startsWith('discover:')).length, 1);
      c.dispose();
      async.flushTimers();
    });
  });

  test('still missing after the cache clear: firmware update, tried once', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).hasIdentity = false;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 30));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Speaker needs a firmware update');
      expect(backend.log.where((l) => l == 'clear-cache').length, 1);
      expect(backend.log.where((l) => l.startsWith('discover:')).length, 2);
      expect(c.connectLog.join('\n'), contains('still missing identity'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('identity hitting a stale handle clears the cache and retries once', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).staleHandleOnce = true;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 20));
      expect(c.state, LinkState.ready);
      expect(c.identityVerified, isTrue);
      expect(backend.log.where((l) => l == 'clear-cache').length, 1);
      expect(c.connectLog.join('\n'), contains('stale handle'));
      c.dispose();
      async.flushTimers();
    });
  });

  group('stale-cache helpers', () {
    test('isStaleHandleError', () {
      expect(isStaleHandleError(Exception('GATT error: invalid handle (0x01)')), isTrue);
      expect(isStaleHandleError(Exception('ATT error 0x0a attribute not found')), isTrue);
      expect(isStaleHandleError(Exception('android-code: 1 GATT_INVALID_HANDLE')), isTrue);
      expect(isStaleHandleError(Exception('characteristic not found')), isTrue);
      expect(isStaleHandleError(Exception('android-code: 133 GATT_ERROR')), isFalse);
      expect(isStaleHandleError(DemoAttError(kAttValueNotAllowed)), isFalse);
      expect(isStaleHandleError(DemoAttError(kAttInsufficientAuthorization)), isFalse);
      expect(isStaleHandleError(IdentityException(IdentityFailure.notOurSpeaker)), isFalse);
    });

    test('control service missing twice throws the firmware-update message', () {
      fakeAsync((async) {
        var clears = 0;
        var discoveries = 0;
        Object? err;
        discoverWithCacheRecovery<int, String>(
          discover: () async {
            discoveries++;
            return 2;
          },
          resolve: (_) => const GattResolution(null, missing: 'the control service'),
          clearCache: () async => clears++,
          serviceCount: (n) => n,
        ).catchError((Object e) {
          err = e;
          return '';
        });
        async.flushMicrotasks();
        expect(discoveries, 2);
        expect(clears, 1);
        expect(err.toString(), 'Speaker needs a firmware update');
      });
    });
  });

  test('reconnect waits at least 3 s between attempts', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(SpeakerController.reconnectBackoff.first, const Duration(seconds: 3));
      backend.log.clear();
      backend.dropLink(_id);
      async.elapse(const Duration(milliseconds: 2900));
      expect(backend.log.where((l) => l.startsWith('connect:')), isEmpty);
      async.elapse(const Duration(milliseconds: 200));
      expect(backend.log.where((l) => l.startsWith('connect:')).length, 1);
      c.dispose();
      async.flushTimers();
    });
  });

  test('slider drag sends one write and ignores stale status volume', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      final before = backend.writeCount;
      for (var v = 36; v <= 80; v += 2) {
        c.setVolume(v);
        async.elapse(const Duration(milliseconds: 60));
        // Status notifications (still reporting 35) must not move the slider.
        expect(c.volume, v);
      }
      async.elapse(const Duration(seconds: 5));
      expect(backend.writeCount - before, 1);
      expect(backend.speaker(_id).volume, 80);
      expect(c.volume, 80);
      expect(c.volumeBusy, isFalse);
      c.dispose();
      async.flushTimers();
    });
  });

  test('a rejected write is reported and the read-back value restored', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      final notices = <String>[];
      c.notices.listen(notices.add);
      c.connect();
      async.elapse(const Duration(seconds: 15));
      // Latency is writable only on the party master; this speaker is not.
      c.setLatency(250);
      expect(c.latencyMs, 250);
      async.elapse(const Duration(seconds: 5));
      expect(c.latencyMs, 150);
      expect(notices.single, contains('rejected'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('broadcast toggle follows wifi_ready until the party is up', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setBroadcast(true);
      expect(c.broadcastRequested, isTrue);
      expect(c.broadcastBusy, isTrue);
      async.elapse(const Duration(seconds: 12));
      expect(c.broadcast!.on, isTrue);
      expect(c.broadcast!.wifiReady, isTrue);
      expect(c.broadcastBusy, isFalse);
      expect(c.broadcastRequested, isNull);
      expect(c.status!.partyRole, PartyRole.master);
      expect(c.partySize.code, 1);
      c.dispose();
      async.flushTimers();
    });
  });

  test('EQ writes 7 bands; a wrong length is rejected with a message', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      final notices = <String>[];
      c.notices.listen(notices.add);
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setEqBand(6, 4);
      c.setEqBand(2, -3);
      async.elapse(const Duration(seconds: 4));
      expect(backend.speaker(_id).eq, [0, 0, -3, 0, 0, 0, 4]);
      expect(backend.log.where((l) => l.startsWith('eq:')).length, 1);
      expect(c.eq, [0, 0, -3, 0, 0, 0, 4]);
      expect(notices, isEmpty);
      c.dispose();
      async.flushTimers();
    });
  });

  test('ATT 0x0D (invalid length) is described as a rejected value', () {
    expect(describeBleError(DemoAttError(kAttInvalidLength)), 'The speaker rejected that value');
    expect(describeBleError(Exception('android-code: 13 GATT_INVALID_ATTRIBUTE_LENGTH')),
        'The speaker rejected that value');
    expect(describeBleError(Exception('android-code: 133 GATT_ERROR')), 'Bluetooth error');
  });

  test('party codec uses Control opcode 0x06 and reads back aac_allowed', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.broadcast!.supportsCodecChoice, isTrue);
      expect(c.broadcast!.codec, PartyCodec.sbc);
      c.setPartyCodec(PartyCodec.aacLc);
      expect(c.codecBusy, isTrue);
      async.elapse(const Duration(seconds: 4));
      expect(backend.log, contains('control:6,1'));
      expect(backend.speaker(_id).aacAllowed, isTrue);
      expect(c.broadcast!.codec, PartyCodec.aacLc);
      expect(c.codecBusy, isFalse);
      c.dispose();
      async.flushTimers();
    });
  });

  test('older firmware (3-byte broadcast) hides the codec option', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).legacyBroadcast = true;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      expect(c.broadcast!.supportsCodecChoice, isFalse);
      final writes = backend.writeCount;
      c.setPartyCodec(PartyCodec.aacLc);
      async.elapse(const Duration(seconds: 4));
      expect(backend.writeCount, writes);
      c.dispose();
      async.flushTimers();
    });
  });

  test('aptX handover shows switching, then asks the user to press Play', () {
    fakeAsync((async) {
      backend = DemoBackend(
        opDelay: const Duration(milliseconds: 300),
        connectDelay: const Duration(seconds: 1),
        handoverDelay: const Duration(seconds: 3),
      );
      backend.speaker(_id).phoneCodec = 'aptX HD';
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 10));
      final seenHandover = <bool>[];
      c.addListener(() => seenHandover.add(c.inHandover));
      c.setBroadcast(true);
      async.elapse(const Duration(seconds: 15));
      expect(seenHandover, contains(true));
      expect(c.inHandover, isFalse);
      expect(c.playPrompt, isTrue);
      expect(backend.speaker(_id).phoneCodec, 'SBC');
      expect(c.broadcast!.on, isTrue);

      backend.playOnPhone(_id);
      async.elapse(const Duration(seconds: 2));
      expect(c.status!.streaming, isTrue);
      expect(c.playPrompt, isFalse);
      c.dispose();
      async.flushTimers();
    });
  });

  test('a pause too short to see as handover still prompts Play', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).phoneCodec = 'AAC'; // AAC not allowed by default
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setBroadcast(true);
      async.elapse(const Duration(seconds: 20));
      expect(c.playPrompt, isTrue);
      c.dismissPlayPrompt();
      expect(c.playPrompt, isFalse);
      c.dispose();
      async.flushTimers();
    });
  });

  test('no handover and no prompt when the phone already streams SBC', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setBroadcast(true);
      async.elapse(const Duration(seconds: 20));
      expect(c.inHandover, isFalse);
      expect(c.playPrompt, isFalse);
      expect(c.status!.streaming, isTrue);
      c.dispose();
      async.flushTimers();
    });
  });

  test('channel write applies and reads back output and party size', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).partyCode = 2;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setChannel(SpeakerChannel.right);
      async.elapse(const Duration(seconds: 4));
      expect(backend.speaker(_id).channelSetting, SpeakerChannel.right);
      expect(c.channel!.output, SpeakerChannel.right);
      expect(c.channel!.pairActive, isTrue);
      c.dispose();
      async.flushTimers();
    });
  });

  test('link drop triggers automatic reconnect', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      final notices = <String>[];
      c.notices.listen(notices.add);
      c.connect();
      async.elapse(const Duration(seconds: 15));
      backend.dropLink(_id);
      async.elapse(Duration.zero);
      expect(c.state, LinkState.reconnecting);
      expect(notices.single, contains('reconnecting'));
      async.elapse(const Duration(seconds: 20));
      expect(c.state, LinkState.ready);
      c.dispose();
      async.flushTimers();
    });
  });

  test('gives up after retries when the speaker is gone', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      backend.speaker(_id).advertising = false;
      backend.dropLink(_id);
      async.elapse(const Duration(seconds: 60));
      expect(c.state, LinkState.failed);
      expect(c.error, 'Connection lost');
      c.dispose();
      async.flushTimers();
    });
  });

  test('a device without the control service fails with a clear message', () {
    fakeAsync((async) {
      backend = slowBackend();
      backend.speaker(_id).hasService = false;
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 10));
      expect(c.state, LinkState.failed);
      expect(c.error, contains('no Music Party control service'));
      c.dispose();
      async.flushTimers();
    });
  });

  test('an open speaker page does no periodic GATT reads', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.watchDetails();
      async.elapse(const Duration(seconds: 5));
      final reads = backend.readCount;
      async.elapse(const Duration(seconds: 60));
      expect(backend.readCount, reads, reason: 'status notifications only');
      expect(c.status, isNotNull);
      c.unwatchDetails();
      c.dispose();
      async.flushTimers();
    });
  });

  test('broadcast follow-up reads stop once the party has settled', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      c.setBroadcast(true);
      async.elapse(const Duration(seconds: 25));
      expect(c.broadcastBusy, isFalse);
      expect(c.broadcast!.wifiReady, isTrue);
      final reads = backend.readCount;
      async.elapse(const Duration(seconds: 60));
      expect(backend.readCount, reads);
      c.dispose();
      async.flushTimers();
    });
  });

  test('a party role change in Status triggers one details read', () {
    fakeAsync((async) {
      backend = slowBackend();
      final c = controller();
      c.connect();
      async.elapse(const Duration(seconds: 15));
      final reads = backend.readCount;
      backend.speaker(_id)
        ..following = true
        ..partyCode = 2;
      async.elapse(const Duration(seconds: 10));
      expect(backend.readCount - reads, 2, reason: 'channel and broadcast once');
      expect(c.partySize.code, 2);
      expect(c.status!.partyRole, PartyRole.following);
      c.dispose();
      async.flushTimers();
    });
  });

  group('AppState', () {
    test('Bluetooth off then on resumes connected speakers', () {
      fakeAsync((async) {
        backend = slowBackend();
        final store = MemoryAppStore();
        final app = AppState(backend: backend, permissions: _Granted(), store: store);
        app.init();
        async.elapse(const Duration(seconds: 2));
        expect(app.entries.map((e) => e.id), contains(_id));
        app.connect(_id);
        async.elapse(const Duration(seconds: 15));
        expect(app.connected.single.id, _id);
        expect(store.speakers.single.id, _id);

        backend.setAdapter(AdapterStatus.off);
        async.elapse(const Duration(seconds: 1));
        final c = app.controller(_id)!;
        expect(c.state, LinkState.failed);
        expect(c.error, 'Bluetooth is off');
        expect(app.ready, isFalse);

        backend.setAdapter(AdapterStatus.on);
        async.elapse(const Duration(seconds: 15));
        expect(c.state, LinkState.ready);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('every scan is one burst capped at 6 s', () {
      fakeAsync((async) {
        backend = slowBackend();
        final app = AppState(backend: backend, permissions: _Granted(), store: MemoryAppStore());
        app.init();
        async.elapse(const Duration(seconds: 1));
        expect(backend.scans.single, kScanBurst);
        expect(kScanBurst, const Duration(seconds: 6));
        expect(app.scanning, isTrue);
        // A second request during a burst does not start another one.
        app.scan();
        async.elapse(const Duration(seconds: 1));
        expect(backend.scans.length, 1);
        async.elapse(const Duration(seconds: 5));
        expect(backend.scanningNow, isFalse);
        expect(app.scanning, isFalse);
        app.scan();
        async.elapse(const Duration(seconds: 1));
        expect(backend.scans, [kScanBurst, kScanBurst]);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('scan stops as soon as every saved speaker is heard', () {
      fakeAsync((async) {
        backend = slowBackend();
        final store = MemoryAppStore(speakers: [
          for (final s in backend.speakers)
            KnownSpeaker(id: s.id, advName: s.advName, lastConnected: DateTime(2026)),
        ]);
        final app = AppState(backend: backend, permissions: _Granted(), store: store);
        app.init();
        async.elapse(const Duration(seconds: 2));
        expect(app.entries.where((e) => e.isNearby).length, 2);
        expect(backend.log, contains('scan-stop'));
        expect(backend.scanningNow, isFalse, reason: 'stopped long before the 6 s cap');
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('Connect stops the scan before connecting', () {
      fakeAsync((async) {
        backend = slowBackend();
        final app = AppState(backend: backend, permissions: _Granted(), store: MemoryAppStore());
        app.init();
        async.flushMicrotasks(); // start-up burst begins
        expect(backend.scanningNow, isTrue);
        // Connect right away, before the backend has even reported scanning.
        app.connect(_id);
        async.elapse(const Duration(seconds: 15));
        final stop = backend.log.indexOf('scan-stop');
        final connect = backend.log.indexWhere((l) => l.startsWith('connect:'));
        expect(stop, greaterThanOrEqualTo(0));
        expect(stop, lessThan(connect));
        expect(backend.log[connect], 'connect:$_id');
        expect(app.connected.single.id, _id);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('no scan burst starts while a speaker is connecting', () {
      fakeAsync((async) {
        backend = slowBackend();
        final app = AppState(backend: backend, permissions: _Granted(), store: MemoryAppStore());
        app.connect(_id); // before init's start-up scan
        app.init();
        async.elapse(const Duration(seconds: 15));
        expect(backend.scans, isEmpty);
        expect(backend.log.where((l) => l.contains('while scanning')), isEmpty);
        expect(app.connected.single.id, _id);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('cards keep first-seen order (no reshuffle under the finger)', () {
      fakeAsync((async) {
        backend = slowBackend();
        // Seen in this order; by name 4A2F would sort before 91C3.
        backend.speakers.sort((a, b) => b.id.compareTo(a.id));
        final late = backend.speaker(_id)..advertising = false;
        final app = AppState(backend: backend, permissions: _Granted(), store: MemoryAppStore());
        app.init();
        async.elapse(const Duration(seconds: 7));
        expect(app.entries.map((e) => e.name), ['Speaker 91C3']);
        // A speaker found later (name sorts first) is appended, not inserted.
        late.advertising = true;
        app.scan();
        async.elapse(const Duration(seconds: 1));
        expect(app.entries.map((e) => e.name), ['Speaker 91C3', 'Speaker 4A2F']);
        // Connecting the second one moves it to "My speakers"; nothing else moves.
        app.connect(_id);
        async.elapse(const Duration(seconds: 15));
        expect(app.entries.map((e) => e.name), ['Speaker 4A2F', 'Speaker 91C3']);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('nothing connects by itself at start-up', () {
      fakeAsync((async) {
        backend = slowBackend();
        final store = MemoryAppStore(speakers: [
          for (final s in backend.speakers)
            KnownSpeaker(id: s.id, advName: s.advName, lastConnected: DateTime(2026)),
        ]);
        final app = AppState(backend: backend, permissions: _Granted(), store: store);
        app.init();
        async.elapse(const Duration(seconds: 30));
        expect(backend.log.where((l) => l.startsWith('connect:')), isEmpty);
        expect(app.connected, isEmpty);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });

    test('presets and nicknames persist', () {
      fakeAsync((async) {
        backend = slowBackend();
        final store = MemoryAppStore(speakers: [
          KnownSpeaker(id: _id, advName: 'WMP-Control-4A2F', lastConnected: DateTime(2026)),
        ]);
        final app = AppState(backend: backend, permissions: _Granted(), store: store);
        app.init();
        async.elapse(const Duration(seconds: 1));
        app.savePreset('Late night', [2, 1, 0, 0, -1, -2, -3]);
        app.rename(_id, 'Kitchen');
        async.elapse(const Duration(seconds: 1));
        expect(store.presets.single.name, 'Late night');
        expect(store.speakers.single.nickname, 'Kitchen');
        expect(app.entries.firstWhere((e) => e.id == _id).name, 'Kitchen');
        app.deletePreset(store.presets.single.id);
        app.forget(_id);
        async.elapse(const Duration(seconds: 1));
        expect(store.presets, isEmpty);
        expect(store.speakers, isEmpty);
        app.dispose();
        backend.dispose();
        async.flushTimers();
      });
    });
  });
}
