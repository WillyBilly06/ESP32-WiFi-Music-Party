/// Identity handshake on characteristic aefb0009 (pure Dart).
///
/// 1. app writes  [0x01, app_nonce(8)]
/// 2. app reads   [state=1, key_id, speaker_nonce(8), speaker_tag(10)]
///    speaker_tag == HMAC-SHA256(key, "WMP-S" || app_nonce || speaker_nonce)[0..9]
/// 3. app writes  [0x02, app_tag(10)]
///    app_tag     =  HMAC-SHA256(key, "WMP-A" || speaker_nonce || app_nonce)[0..9]
///
/// Before step 3 the speaker rejects every write (CCCDs included) with ATT
/// 0x08 and drops the link after 10 s. Reads stay open.
library;

import 'dart:math';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

const int kNonceLength = 8;
const int kTagLength = 10;
const int kChallengeLength = 2 + kNonceLength + kTagLength; // 20

const int kIdentityOpChallenge = 0x01;
const int kIdentityOpProof = 0x02;
const int kIdentityStateChallenged = 1;
const int kIdentityStateProven = 2;

/// "WMP-S" and "WMP-A" (5 ASCII bytes each).
const List<int> _speakerLabel = [0x57, 0x4D, 0x50, 0x2D, 0x53];
const List<int> _appLabel = [0x57, 0x4D, 0x50, 0x2D, 0x41];

/// The shared secret and its id. Never log [key].
class IdentityKey {
  const IdentityKey(this.id, this.key);
  final int id;
  final List<int> key;

  @override
  String toString() => 'IdentityKey(id: $id)';
}

/// Why a speaker failed the handshake.
enum IdentityFailure {
  /// Wrong tag or unexpected state: not one of our speakers.
  notOurSpeaker,

  /// The speaker uses a different key id than this app.
  keyMismatch,

  /// The speaker has no identity characteristic (old firmware).
  unsupported,

  /// The speaker refused our proof (ATT 0x08 after the handshake).
  rejected,
}

class IdentityException implements Exception {
  IdentityException(this.failure);
  final IdentityFailure failure;

  String get message => switch (failure) {
        IdentityFailure.notOurSpeaker => 'Not a Music Party speaker',
        IdentityFailure.keyMismatch => 'Speaker needs a firmware update',
        IdentityFailure.unsupported => 'Speaker needs a firmware update',
        IdentityFailure.rejected => 'The speaker did not accept this app',
      };

  @override
  String toString() => message;
}

class IdentityChallenge {
  const IdentityChallenge({
    required this.state,
    required this.keyId,
    required this.speakerNonce,
    required this.speakerTag,
  });

  final int state;
  final int keyId;
  final List<int> speakerNonce;
  final List<int> speakerTag;

  /// Decodes the 20-byte read. Short reads are not a valid challenge.
  static IdentityChallenge? decode(List<int> b) {
    if (b.length < kChallengeLength) return null;
    return IdentityChallenge(
      state: b[0],
      keyId: b[1],
      speakerNonce: List.unmodifiable(b.sublist(2, 2 + kNonceLength)),
      speakerTag: List.unmodifiable(b.sublist(2 + kNonceLength, kChallengeLength)),
    );
  }
}

List<int> _hmac10(List<int> key, List<int> msg) =>
    Hmac(sha256, key).convert(msg).bytes.sublist(0, kTagLength);

List<int> speakerTag(List<int> key, List<int> appNonce, List<int> speakerNonce) =>
    _hmac10(key, [..._speakerLabel, ...appNonce, ...speakerNonce]);

List<int> appTag(List<int> key, List<int> speakerNonce, List<int> appNonce) =>
    _hmac10(key, [..._appLabel, ...speakerNonce, ...appNonce]);

/// Constant-time comparison (no early exit on the first difference).
bool constantTimeEquals(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) {
    diff |= (a[i] ^ b[i]) & 0xff;
  }
  return diff == 0;
}

List<int> randomNonce([Random? random]) {
  final r = random ?? Random.secure();
  return [for (var i = 0; i < kNonceLength; i++) r.nextInt(256)];
}

Uint8List challengeWrite(List<int> appNonce) {
  if (appNonce.length != kNonceLength) throw ArgumentError('nonce must be $kNonceLength bytes');
  return Uint8List.fromList([kIdentityOpChallenge, ...appNonce]);
}

Uint8List proofWrite(List<int> tag) {
  if (tag.length != kTagLength) throw ArgumentError('tag must be $kTagLength bytes');
  return Uint8List.fromList([kIdentityOpProof, ...tag]);
}

/// Checks the speaker's challenge and returns the proof to write, or throws
/// [IdentityException]. Key id is checked first so a key mismatch is reported
/// as a firmware update rather than as an impostor.
Uint8List verifySpeakerAndProve(IdentityKey key, List<int> appNonce, List<int> challengeBytes) {
  final ch = IdentityChallenge.decode(challengeBytes);
  if (ch == null || ch.state != kIdentityStateChallenged) {
    throw IdentityException(IdentityFailure.notOurSpeaker);
  }
  if (ch.keyId != key.id) throw IdentityException(IdentityFailure.keyMismatch);
  final expected = speakerTag(key.key, appNonce, ch.speakerNonce);
  if (!constantTimeEquals(expected, ch.speakerTag)) {
    throw IdentityException(IdentityFailure.notOurSpeaker);
  }
  return proofWrite(appTag(key.key, ch.speakerNonce, appNonce));
}
