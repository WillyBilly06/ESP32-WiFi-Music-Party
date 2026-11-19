/// The identity key the app proves itself with (docs/ble-control-protocol.md,
/// "Identity").
///
/// The default key below is PUBLIC on purpose: it stops other apps from
/// driving a speaker and keeps this app from listing other devices, but it is
/// not a secret against anyone who reads the source.
///
/// Building your own speakers? Put your own 32-byte key and a NEW key id here
/// AND in firmware/app/main/identity_key.h, then rebuild both the app and the
/// firmware. A speaker whose key id differs from the app's shows
/// "Speaker needs a firmware update".
library;

import '../protocol/identity.dart';

/// Key id 0 = the public default key.
const int wmpIdentityKeyId = 0;

/// ASCII "WMP development identity key v01" (32 bytes).
const List<int> wmpIdentityKey = [
  0x57, 0x4d, 0x50, 0x20, 0x64, 0x65, 0x76, 0x65, //
  0x6c, 0x6f, 0x70, 0x6d, 0x65, 0x6e, 0x74, 0x20, //
  0x69, 0x64, 0x65, 0x6e, 0x74, 0x69, 0x74, 0x79, //
  0x20, 0x6b, 0x65, 0x79, 0x20, 0x76, 0x30, 0x31, //
];

/// The key compiled into this app.
const IdentityKey appIdentityKey = IdentityKey(wmpIdentityKeyId, wmpIdentityKey);
