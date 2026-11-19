/* Identity key shared by the speaker firmware and the companion app.
 *
 * The speaker and the app prove to each other that they know this key (HMAC-SHA256
 * challenge-response, docs/ble-control-protocol.md "Identity"), so the app lists only these
 * speakers and the speakers accept control only from this app.
 *
 * This default key is public. To make your own speakers and app recognise only each other,
 * put your own 32 bytes here AND in app/wmp_party/lib/ble/identity_key.dart, give it a new
 * key id, then rebuild both. */
#pragma once
#include <stdint.h>
#define WMP_IDENTITY_KEY_ID 0u
static const uint8_t wmp_identity_key[32]={'W','M','P',' ','d','e','v','e','l','o','p','m','e','n','t',' ','i','d','e','n','t','i','t','y',' ','k','e','y',' ','v','0','1'};
