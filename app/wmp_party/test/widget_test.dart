import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wmp_party/ble/ble_backend.dart';
import 'package:wmp_party/ble/demo_backend.dart';
import 'package:wmp_party/ble/permissions.dart';
import 'package:wmp_party/main.dart';
import 'package:wmp_party/protocol/wmp_protocol.dart';
import 'package:wmp_party/state/app_state.dart';
import 'package:wmp_party/storage/app_store.dart';
import 'package:wmp_party/ui/widgets/speaker_art.dart';

class FakePermissions implements PermissionGate {
  FakePermissions(this.state, {this.grantOnRequest = true});
  PermissionState state;
  bool grantOnRequest;
  int requests = 0;
  int settingsOpened = 0;

  @override
  Future<PermissionState> check() async => state;
  @override
  Future<PermissionState> request() async {
    requests++;
    state = grantOnRequest ? PermissionState.granted : PermissionState.permanentlyDenied;
    return state;
  }

  @override
  Future<void> openSettings() async => settingsOpened++;
  @override
  Future<bool> needsLocation() async => false;
}

const _a = 'AA:BB:CC:00:4A:2F';
const _b = 'AA:BB:CC:00:91:C3';

class Harness {
  Harness({
    PermissionState permission = PermissionState.granted,
    AdapterStatus adapter = AdapterStatus.on,
    MemoryAppStore? store,
  })  : backend = DemoBackend(initialAdapter: adapter),
        permissions = FakePermissions(permission),
        store = store ?? MemoryAppStore() {
    app = AppState(
      backend: backend,
      permissions: permissions,
      store: this.store,
      controllerDebounce: const Duration(milliseconds: 300),
    );
  }

  final DemoBackend backend;
  final FakePermissions permissions;
  final MemoryAppStore store;
  late final AppState app;

  Future<void> pump(WidgetTester tester) async {
    tester.view.physicalSize = const Size(1170, 2532);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(PartyApp(state: app));
    await settle(tester);
  }

  /// Advances fake time in small steps (pumpAndSettle would spin on the
  /// progress indicators shown while scanning or sending).
  static Future<void> settle(WidgetTester tester, [int ms = 1500]) async {
    for (var t = 0; t < ms; t += 100) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  Future<void> dispose(WidgetTester tester) async {
    await tester.pumpWidget(const SizedBox());
    app.dispose();
    backend.dispose();
    await settle(tester, 15000);
  }
}

Future<void> _openSpeaker(WidgetTester tester, String name) async {
  await tester.tap(find.text(name));
  await Harness.settle(tester, 4000);
}

Future<void> _scrollTo(WidgetTester tester, Finder f) async {
  await tester.scrollUntilVisible(f, 200, scrollable: find.byType(Scrollable).last);
  await tester.pump();
}

void main() {
  setUpAll(() => SpeakerArt.animationsEnabled = false);

  testWidgets('permission screen asks, then shows nearby speakers', (tester) async {
    final h = Harness(permission: PermissionState.denied);
    await h.pump(tester);
    expect(find.text('Find your speakers'), findsOneWidget);
    await tester.tap(find.text('Allow Bluetooth access'));
    await Harness.settle(tester);
    expect(h.permissions.requests, 1);
    expect(find.text('Music Party'), findsWidgets);
    expect(find.text('Speaker 4A2F'), findsOneWidget);
    expect(find.text('Speaker 91C3'), findsOneWidget);
    expect(find.text('NEARBY'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('permanently denied permission offers app settings', (tester) async {
    final h = Harness(permission: PermissionState.permanentlyDenied);
    await h.pump(tester);
    await tester.tap(find.text('Open app settings'));
    await tester.pump();
    expect(h.permissions.settingsOpened, 1);
    await h.dispose(tester);
  });

  testWidgets('Bluetooth off screen turns Bluetooth on', (tester) async {
    final h = Harness(adapter: AdapterStatus.off);
    await h.pump(tester);
    expect(find.text('Bluetooth is off'), findsOneWidget);
    await tester.tap(find.text('Turn on Bluetooth'));
    await Harness.settle(tester);
    expect(find.text('Speaker 4A2F'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('connecting shows the speaker page with live status', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    expect(find.text('Volume'), findsOneWidget);
    expect(find.text('35%'), findsOneWidget);
    expect(find.text('Broadcast'), findsOneWidget);
    expect(find.textContaining('Playing 44.1 kHz'), findsWidgets);
    expect(find.text('Phone'), findsWidgets);
    expect(find.text('Solo'), findsWidgets);

    // The speaker is remembered after a successful connection.
    expect(h.store.speakers.single.id, _a);

    // Back home: card is under "My speakers" with the volume; party banner.
    await tester.pageBack();
    await Harness.settle(tester);
    expect(find.text('MY SPEAKERS'), findsOneWidget);
    expect(find.text('Start a party'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('volume slider is debounced into a single write', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    final before = h.backend.writeCount;
    final slider = find.byKey(const Key('volume-slider'));
    final gesture = await tester.startGesture(tester.getCenter(slider));
    for (var i = 0; i < 10; i++) {
      await gesture.moveBy(const Offset(12, 0));
      await tester.pump(const Duration(milliseconds: 40));
    }
    await gesture.up();
    await Harness.settle(tester, 2000);
    expect(h.backend.writeCount - before, 1);
    final applied = h.backend.speaker(_a).volume;
    expect(applied, greaterThan(35));
    expect(find.text('$applied%'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('broadcast switch and stereo pair picker write to the speaker', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).partyCode = 2;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');

    await tester.tap(find.byKey(const Key('broadcast-switch')));
    await Harness.settle(tester, 500);
    expect(find.text('Starting Wi-Fi party...'), findsOneWidget);
    await Harness.settle(tester, 6000);
    expect(h.backend.speaker(_a).broadcastOn, isTrue);
    expect(find.textContaining('Party on'), findsOneWidget);
    expect(find.text('Party host'), findsWidgets);

    await _scrollTo(tester, find.byKey(const Key('channel-picker')));
    // Party size is 1 after the demo party starts; force 2 to test the note.
    h.backend.speaker(_a).partyCode = 2;
    await tester.tap(find.text('L'));
    await Harness.settle(tester, 1500);
    expect(h.backend.speaker(_a).channelSetting, SpeakerChannel.left);
    expect(find.textContaining('Playing Left'), findsOneWidget);
    expect(find.text('Stereo pair active.'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('party codec picker switches SBC / AAC-LC', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await _scrollTo(tester, find.byKey(const Key('party-codec')));
    expect(find.text('Party codec'), findsOneWidget);
    await tester.tap(find.text('AAC-LC'));
    await Harness.settle(tester, 1500);
    expect(h.backend.speaker(_a).aacAllowed, isTrue);
    expect(find.textContaining('also use AAC-LC'), findsOneWidget);
    await tester.tap(find.text('SBC'));
    await Harness.settle(tester, 1500);
    expect(h.backend.speaker(_a).aacAllowed, isFalse);
    await h.dispose(tester);
  });

  testWidgets('codec handover shows switching state then a Play prompt', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).phoneCodec = 'aptX';
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await tester.tap(find.byKey(const Key('broadcast-switch')));
    await Harness.settle(tester, 300);
    expect(find.byKey(const Key('handover-banner')), findsOneWidget);
    expect(find.text('Switching to party mode...'), findsWidgets);
    await Harness.settle(tester, 8000);
    expect(find.byKey(const Key('handover-banner')), findsNothing);
    expect(find.byKey(const Key('play-prompt')), findsOneWidget);
    expect(find.text('Press Play on your phone to start the party.'), findsOneWidget);
    h.backend.playOnPhone(_a);
    await Harness.settle(tester, 2000);
    expect(find.byKey(const Key('play-prompt')), findsNothing);
    await h.dispose(tester);
  });

  testWidgets('older firmware hides the party codec option', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).legacyBroadcast = true;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    expect(find.byKey(const Key('party-codec')), findsNothing);
    expect(find.text('Party codec'), findsNothing);
    await h.dispose(tester);
  });

  testWidgets('EQ presets, band sliders and saving a custom preset', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await _scrollTo(tester, find.byKey(const Key('eq-customize')));
    await tester.tap(find.byKey(const Key('eq-customize')));
    await Harness.settle(tester);
    expect(find.text('Equalizer'), findsOneWidget);
    expect(find.byKey(const Key('eq-preset-name')), findsOneWidget);
    expect(tester.widget<Text>(find.byKey(const Key('eq-preset-name'))).data, 'Flat');

    await tester.tap(find.text('Bass Boost'));
    await Harness.settle(tester, 1500);
    expect(h.backend.speaker(_a).eq, [7, 5, 2, 0, 0, 0, 1]);
    for (var i = 0; i < 7; i++) {
      expect(find.byKey(Key('eq-band-$i')), findsOneWidget);
    }
    for (final l in ['63', '160', '400', '1k', '2.5k', '6.3k', '16k']) {
      expect(find.text(l), findsOneWidget);
    }
    expect(tester.widget<Text>(find.byKey(const Key('eq-preset-name'))).data, 'Bass Boost');

    // Drag the 16 kHz band (rotated slider: drag up = more treble).
    await tester.drag(find.byKey(const Key('eq-band-6')), const Offset(0, -60));
    await Harness.settle(tester, 1500);
    expect(h.backend.speaker(_a).eq[6], greaterThan(1));
    expect(tester.widget<Text>(find.byKey(const Key('eq-preset-name'))).data, 'Custom');

    await _scrollTo(tester, find.byKey(const Key('eq-save')));
    await tester.tap(find.byKey(const Key('eq-save')));
    await tester.pump();
    await tester.enterText(find.byKey(const Key('preset-name')), 'Rooftop');
    await tester.tap(find.text('Save'));
    await Harness.settle(tester);
    expect(h.store.presets.single.name, 'Rooftop');
    expect(h.store.presets.single.gains, h.backend.speaker(_a).eq);
    await h.dispose(tester);
  });

  testWidgets('multiple speakers connect and party mode switches all', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await tester.pageBack();
    await Harness.settle(tester);
    await _openSpeaker(tester, 'Speaker 91C3');
    await tester.pageBack();
    await Harness.settle(tester);
    expect(h.app.connected.length, 2);
    await tester.tap(find.byKey(const Key('party-all')));
    await Harness.settle(tester, 8000);
    expect(h.backend.speaker(_a).broadcastOn, isTrue);
    expect(h.backend.speaker(_b).broadcastOn, isTrue);
    expect(find.text('Party is on'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('a dropped link shows reconnect progress and recovers', (tester) async {
    final h = Harness();
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    h.backend.dropLink(_a);
    await tester.pump();
    await tester.pump();
    expect(find.textContaining('reconnecting'), findsOneWidget); // snackbar
    expect(find.text('Reconnecting...'), findsOneWidget);
    await Harness.settle(tester, 6000);
    expect(find.text('Reconnecting...'), findsNothing);
    expect(find.text('Volume'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('scan button, pull to refresh and coming back home rescan', (tester) async {
    final h = Harness();
    await h.pump(tester);
    expect(h.backend.scans.length, 1); // start-up burst
    await Harness.settle(tester, 7000); // burst ends by itself (6 s cap)
    expect(h.backend.scanningNow, isFalse);

    await tester.tap(find.byTooltip('Scan for speakers'));
    await Harness.settle(tester, 500);
    expect(h.backend.scans.length, 2);
    await Harness.settle(tester, 7000);

    await tester.fling(find.byType(CustomScrollView), const Offset(0, 400), 1000);
    await Harness.settle(tester, 1500);
    expect(h.backend.scans.length, 3);

    // Opening a speaker ends the burst; coming back starts a new one.
    await _openSpeaker(tester, 'Speaker 4A2F');
    expect(h.backend.scanningNow, isFalse);
    final before = h.backend.scans.length;
    await tester.pageBack();
    await Harness.settle(tester, 500);
    expect(h.backend.scans.length, before + 1);
    // The connected speaker is saved and already heard: the burst ends early.
    expect(h.backend.scanningNow, isFalse);

    // App back in the foreground with home on top.
    await Harness.settle(tester, 7000);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await Harness.settle(tester, 500);
    expect(h.backend.scans.length, before + 2);
    await h.dispose(tester);
  });

  testWidgets('only real Music Party adverts are listed', (tester) async {
    final h = Harness();
    h.backend.speaker(_b).companyId = 0x0059; // right UUID, wrong manufacturer data
    await h.pump(tester);
    expect(find.text('Speaker 4A2F'), findsOneWidget);
    expect(find.text('Speaker 91C3'), findsNothing);
    // advert status bits are shown on the nearby card
    expect(find.textContaining('Playing'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('an impostor shows "Not a Music Party speaker"', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).impostor = true;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    expect(find.text('Not a Music Party speaker'), findsWidgets);
    expect(find.text('Reconnect'), findsOneWidget);
    await h.dispose(tester);
  });

  testWidgets('a failed connect shows why and offers the connect log', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).busyDiscoveries = 10;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await Harness.settle(tester, 3000);
    expect(find.textContaining('Service discovery failed'), findsWidgets);
    await tester.tap(find.byKey(const Key('connect-log')));
    await Harness.settle(tester, 500);
    expect(find.text('Connect log'), findsOneWidget);
    expect(find.textContaining('busy, retry'), findsOneWidget);
    await tester.tap(find.text('Close'));
    await Harness.settle(tester, 500);
    await h.dispose(tester);
  });

  testWidgets('Karaoke & effects: preset, switch and status banners', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).effectsMemoryShort = true;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await _scrollTo(tester, find.byKey(const Key('effects-entry')));
    await tester.tap(find.byKey(const Key('effects-entry')));
    await Harness.settle(tester, 1000);
    expect(find.text('Karaoke & effects'), findsOneWidget);
    for (final n in ['vocalRemover', 'echo', 'reverb', 'chorus']) {
      expect(find.byKey(Key('fx-switch-$n')), findsOneWidget);
    }
    await tester.tap(find.byKey(const Key('fx-preset-karaoke')));
    await Harness.settle(tester, 1000);
    expect(h.backend.speaker(_a).effects[0], 0x05);
    expect(find.byKey(const Key('fx-memory')), findsOneWidget);
    expect(find.text('Not enough memory for this effect right now'), findsOneWidget);
    expect(find.text('Room and hall ambience · not running'), findsOneWidget);
    await _scrollTo(tester, find.byKey(const Key('fx-switch-chorus')));
    await tester.tap(find.byKey(const Key('fx-switch-chorus')));
    await Harness.settle(tester, 1000);
    expect(h.backend.speaker(_a).effects[0], 0x0d);
    await h.dispose(tester);
  });

  testWidgets('effects on a party follower are read-only', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).following = true;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    await _scrollTo(tester, find.byKey(const Key('effects-entry')));
    await tester.tap(find.byKey(const Key('effects-entry')));
    await Harness.settle(tester, 1000);
    expect(find.text("Following the party host's effects"), findsOneWidget);
    await tester.tap(find.byKey(const Key('fx-preset-karaoke')));
    await Harness.settle(tester, 1000);
    expect(h.backend.speaker(_a).effects[0], 0);
    await h.dispose(tester);
  });

  testWidgets('older firmware hides the effects entry', (tester) async {
    final h = Harness();
    h.backend.speaker(_a).hasEffects = false;
    await h.pump(tester);
    await _openSpeaker(tester, 'Speaker 4A2F');
    expect(find.byKey(const Key('effects-entry'), skipOffstage: false), findsNothing);
    await h.dispose(tester);
  });

  testWidgets('saved speakers that are out of range are listed', (tester) async {
    final h = Harness(
      store: MemoryAppStore(speakers: [
        KnownSpeaker(
          id: '11:22:33:44:55:66',
          advName: 'WMP-Control-5566',
          nickname: 'Garden',
          lastConnected: DateTime(2026, 11, 1),
        ),
      ]),
    );
    await h.pump(tester);
    expect(find.text('SAVED'), findsOneWidget);
    expect(find.text('Garden'), findsOneWidget);
    expect(find.text('Not in range'), findsOneWidget);
    await h.dispose(tester);
  });
}
