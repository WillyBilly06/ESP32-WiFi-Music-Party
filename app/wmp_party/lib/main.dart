import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'ble/ble_backend.dart';
import 'ble/demo_backend.dart';
import 'ble/fbp_backend.dart';
import 'ble/permissions.dart';
import 'state/app_state.dart';
import 'storage/app_store.dart';
import 'ui/gate_screens.dart';
import 'ui/home_screen.dart';
import 'ui/theme.dart';

/// `flutter run --dart-define=WMP_DEMO=true` shows simulated speakers so the UI
/// can be reviewed without hardware. Normal builds always use real BLE.
const bool kDemoMode = bool.fromEnvironment('WMP_DEMO');

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    systemNavigationBarColor: AppColors.background,
  ));
  final BleBackend backend = kDemoMode ? DemoBackend() : FbpBackend();
  final PermissionGate permissions = kDemoMode ? _DemoPermissions() : AndroidPermissionGate();
  final app = AppState(backend: backend, permissions: permissions, store: PrefsAppStore());
  runApp(PartyApp(state: app));
}

class _DemoPermissions implements PermissionGate {
  @override
  Future<PermissionState> check() async => PermissionState.granted;
  @override
  Future<PermissionState> request() async => PermissionState.granted;
  @override
  Future<void> openSettings() async {}
  @override
  Future<bool> needsLocation() async => false;
}

class PartyApp extends StatefulWidget {
  const PartyApp({super.key, required this.state});
  final AppState state;

  @override
  State<PartyApp> createState() => _PartyAppState();
}

class _PartyAppState extends State<PartyApp> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.state.init();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState s) {
    // Coming back from the system settings page after granting access.
    if (s == AppLifecycleState.resumed && widget.state.initialized) {
      widget.state.recheckPermission();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<AppState>.value(
      value: widget.state,
      child: MaterialApp(
        title: 'Music Party',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(),
        navigatorObservers: [homeRouteObserver],
        home: const RootGate(),
      ),
    );
  }
}

/// Permission -> Bluetooth on -> home.
class RootGate extends StatelessWidget {
  const RootGate({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    Widget child;
    if (!app.initialized || app.permission == PermissionState.unknown) {
      child = const Scaffold(body: Center(child: CircularProgressIndicator()));
    } else if (app.permission != PermissionState.granted) {
      child = const PermissionScreen();
    } else if (app.adapter != AdapterStatus.on) {
      child = app.adapter == AdapterStatus.unknown || app.adapter == AdapterStatus.turningOn
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : const BluetoothOffScreen();
    } else {
      child = const HomeScreen();
    }
    return AnimatedSwitcher(duration: const Duration(milliseconds: 250), child: child);
  }
}
