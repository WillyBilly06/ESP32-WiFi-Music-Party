/// Runtime Bluetooth permission flow.
///
/// Android 12+ (API 31): BLUETOOTH_SCAN (neverForLocation) + BLUETOOTH_CONNECT.
/// Android 11 and older: scanning needs location permission (and location
/// services switched on).
library;

import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

enum PermissionState { unknown, granted, denied, permanentlyDenied }

abstract class PermissionGate {
  Future<PermissionState> check();
  Future<PermissionState> request();
  Future<void> openSettings();

  /// Whether this Android version needs location for BLE scanning.
  Future<bool> needsLocation();
}

class AndroidPermissionGate implements PermissionGate {
  static const _channel = MethodChannel('wmp_party/platform');
  int? _sdk;

  Future<int> _sdkInt() async {
    if (_sdk != null) return _sdk!;
    try {
      _sdk = await _channel.invokeMethod<int>('sdkInt') ?? 31;
    } on PlatformException {
      _sdk = 31;
    } on MissingPluginException {
      _sdk = 31;
    }
    return _sdk!;
  }

  Future<List<Permission>> _needed() async {
    if (await _sdkInt() >= 31) {
      return [Permission.bluetoothScan, Permission.bluetoothConnect];
    }
    return [Permission.locationWhenInUse];
  }

  @override
  Future<bool> needsLocation() async => await _sdkInt() < 31;

  static PermissionState _combine(Iterable<PermissionStatus> all) {
    if (all.every((s) => s.isGranted || s.isLimited)) {
      return PermissionState.granted;
    }
    if (all.any((s) => s.isPermanentlyDenied || s.isRestricted)) {
      return PermissionState.permanentlyDenied;
    }
    return PermissionState.denied;
  }

  @override
  Future<PermissionState> check() async {
    final perms = await _needed();
    final statuses = [for (final p in perms) await p.status];
    return _combine(statuses);
  }

  @override
  Future<PermissionState> request() async {
    final perms = await _needed();
    final result = await perms.request();
    return _combine(result.values);
  }

  @override
  Future<void> openSettings() async {
    await openAppSettings();
  }
}
