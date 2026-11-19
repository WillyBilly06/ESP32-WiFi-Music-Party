import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../ble/permissions.dart';
import '../state/app_state.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/speaker_art.dart';

class _GateLayout extends StatelessWidget {
  const _GateLayout({
    required this.title,
    required this.body,
    required this.action,
    this.secondary,
    this.icon,
  });

  final String title;
  final String body;
  final Widget action;
  final Widget? secondary;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(32),
            child: Column(
              children: [
                if (icon == null)
                  const SpeakerArt(size: 150, broadcasting: true)
                else
                  Container(
                    width: 110,
                    height: 110,
                    decoration: const BoxDecoration(
                      shape: BoxShape.circle,
                      color: AppColors.surfaceHigh,
                    ),
                    child: Icon(icon, size: 52, color: AppColors.teal),
                  ),
                const SizedBox(height: 28),
                Text(title,
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800)),
                const SizedBox(height: 12),
                Text(body,
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 15, height: 1.45, color: AppColors.textDim)),
                const SizedBox(height: 30),
                action,
                if (secondary != null) ...[const SizedBox(height: 12), secondary!],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class PermissionScreen extends StatelessWidget {
  const PermissionScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final permanent = app.permission == PermissionState.permanentlyDenied;
    final what = app.needsLocation
        ? 'Location access (Android uses it for Bluetooth scanning on this version)'
        : 'Nearby devices access';
    return _GateLayout(
      title: 'Find your speakers',
      body: 'Music Party talks to your speakers over Bluetooth Low Energy to change '
          'volume, EQ, stereo pairing and party mode.\n\n'
          '$what is needed to find and connect to them. Nothing else is collected.',
      action: permanent
          ? GradientButton(
              label: 'Open app settings',
              icon: Icons.settings_rounded,
              onPressed: app.openSettings,
            )
          : GradientButton(
              label: 'Allow Bluetooth access',
              icon: Icons.bluetooth_searching_rounded,
              onPressed: app.requestPermission,
            ),
      secondary: permanent
          ? const Text(
              'Access was denied. Enable "Nearby devices" for Music Party in Settings, then come back.',
              textAlign: TextAlign.center,
              style: TextStyle(color: AppColors.amber, fontSize: 13),
            )
          : null,
    );
  }
}

class BluetoothOffScreen extends StatelessWidget {
  const BluetoothOffScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final unavailable = app.adapter.name == 'unavailable';
    return _GateLayout(
      icon: Icons.bluetooth_disabled_rounded,
      title: unavailable ? 'Bluetooth not available' : 'Bluetooth is off',
      body: unavailable
          ? 'This phone does not support Bluetooth Low Energy.'
          : 'Turn on Bluetooth to see and control your speakers. '
              'Speakers you were connected to reconnect automatically.',
      action: unavailable
          ? const SizedBox.shrink()
          : GradientButton(
              label: 'Turn on Bluetooth',
              icon: Icons.bluetooth_rounded,
              onPressed: app.turnOnBluetooth,
            ),
    );
  }
}
