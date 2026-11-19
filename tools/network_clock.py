"""Validate a real fixed clock network without treating an AP as a station."""
def failure(s):
    clock=s['WMP_SOURCE_CLOCK'];anchor=s.get('WMP_ANCHOR_STAT',{}).get('requested',0)
    pure=s.get('WMP_PURE_BROADCAST',{}).get('own_clock_local')
    if not s['WMP_NETWORK']['infrastructure'] and pure and s['WMP_RADIO']['mode']==2:
        # Pure broadcast source: its timeline is its own local clock.
        if s['WMP_ASSOC'].get('requested') or s['WMP_ASSOC']['connected'] or s['WMP_WIFI_MODE']['ap']:return 'pure broadcast source must not be associated or an AP'
        if not clock['valid'] or clock['age_us']>1000000:return 'pure broadcast source clock unavailable'
        return None
    if not s['WMP_NETWORK']['infrastructure']:
        # A passive listener uses hardware RX beacon timestamps mapped onto
        # its free-running MAC counter; it does not have an associated TSF.
        if s['WMP_ASSOC'].get('requested') or s['WMP_ASSOC']['connected'] or s['WMP_WIFI_MODE']['ap']:return 'passive listener must not be associated or an AP'
        if not s.get('WMP_WIFI_STATE',{}).get('sniffer') or not s.get('WMP_RECEIVE_WINDOW',{}).get('enabled'):return 'passive MAC capture/receive window unavailable'
        audio=s['WMP_AUDIO'];quality=s['WMP_CLOCK_QUALITY'];beacons=s['WMP_BEACONS']
        if not audio['clock_valid'] or audio['clock_pairs']<8 or beacons['match_age_us']>1500000:return 'hardware-beacon clock unavailable or stale'
        if quality['fit_us']>1000 or abs(quality['ppm'])>500:return 'hardware-beacon clock mapping outside bounds'
        return None
    if not s['WMP_NETWORK']['infrastructure'] or s['WMP_NETWORK'].get('status',0):return 'fixed clock network unavailable'
    if not clock['valid'] or clock['age_us']>1000000 or clock['span_us']>1000 or not clock.get('native_calls',0):return 'native hardware clock unavailable or stale'
    domain=s.get('WMP_CLOCK_DOMAIN')
    if domain is not None and not domain.get('shared'):return 'clock is not a shared network domain'
    if anchor:
        if not s['WMP_WIFI_MODE']['ap'] or not s['WMP_ANCHOR_STAT']['active']:return 'isolated AP anchor inactive'
        if s['WMP_ASSOC']['connected'] or clock['interface_sta']:return 'AP anchor incorrectly reported as an associated station'
        if domain is None or not domain.get('anchor_ap'):return 'AP hardware clock domain missing'
    elif s['WMP_WIFI_MODE']['ap'] or not s['WMP_ASSOC']['connected'] or not clock['interface_sta']:
        return 'station association and native STA clock required'
    return None
