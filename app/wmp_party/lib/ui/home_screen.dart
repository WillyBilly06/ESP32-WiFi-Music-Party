import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../protocol/wmp_protocol.dart';
import '../state/app_state.dart';
import '../state/speaker_controller.dart';
import 'speaker_screen.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/speaker_art.dart';

/// Lets the home screen notice when a pushed page is popped (rescan).
final RouteObserver<ModalRoute<void>> homeRouteObserver = RouteObserver<ModalRoute<void>>();

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  static Future<void> openSpeaker(BuildContext context, String id) async {
    final app = context.read<AppState>();
    final c = app.controllerFor(id);
    // Leaving the list: end the scan burst before anything else.
    app.stopScan();
    if (c.state == LinkState.idle || c.state == LinkState.failed) {
      // Start connecting; the speaker page shows progress.
      app.connect(id);
    }
    await Navigator.of(context).push(
      MaterialPageRoute<void>(builder: (_) => SpeakerScreen(controller: c)),
    );
  }

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with RouteAware, WidgetsBindingObserver {
  ModalRoute<void>? _route;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final route = ModalRoute.of(context);
    if (route != _route && route != null) {
      if (_route != null) homeRouteObserver.unsubscribe(this);
      _route = route;
      homeRouteObserver.subscribe(this, route);
    }
  }

  @override
  void dispose() {
    homeRouteObserver.unsubscribe(this);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// Back from a speaker page.
  @override
  void didPopNext() => context.read<AppState>().homeResumed();

  /// App back in the foreground with the home screen on top.
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && (_route?.isCurrent ?? false)) {
      context.read<AppState>().homeResumed();
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final entries = app.entries;
    final active = entries.where((e) => e.isActive).toList();
    final nearby = entries.where((e) => !e.isActive && e.isNearby).toList();
    final saved = entries.where((e) => !e.isActive && !e.isNearby).toList();

    return Scaffold(
      body: RefreshIndicator(
        onRefresh: app.scan,
        child: CustomScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          slivers: [
            SliverAppBar.large(
              title: const Text('Music Party', style: TextStyle(fontWeight: FontWeight.w800)),
              actions: [
                IconButton(
                  tooltip: app.scanning ? 'Stop scanning' : 'Scan for speakers',
                  onPressed: app.scanning ? app.stopScan : app.scan,
                  icon: app.scanning
                      ? const SizedBox(
                          width: 20,
                          height: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.radar_rounded),
                ),
                const SizedBox(width: 6),
              ],
            ),
            if (app.connected.isNotEmpty)
              const SliverToBoxAdapter(child: _PartyBanner()),
            if (app.scanError != null)
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(20, 8, 20, 0),
                  child: Text(app.scanError!, style: const TextStyle(color: AppColors.amber)),
                ),
              ),
            if (entries.isEmpty)
              SliverFillRemaining(hasScrollBody: false, child: _EmptyState(scanning: app.scanning)),
            ..._section('My speakers', active),
            ..._section('Nearby', nearby),
            ..._section('Saved', saved),
            const SliverToBoxAdapter(child: SizedBox(height: 40)),
          ],
        ),
      ),
    );
  }

  List<Widget> _section(String title, List<SpeakerEntry> list) {
    if (list.isEmpty) return const [];
    return [
      SliverToBoxAdapter(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(22, 22, 22, 10),
          child: Text(title.toUpperCase(),
              style: const TextStyle(
                  fontSize: 12.5, letterSpacing: 1.3, color: AppColors.textDim, fontWeight: FontWeight.w700)),
        ),
      ),
      SliverList.separated(
        itemCount: list.length,
        separatorBuilder: (_, _) => const SizedBox(height: 12),
        itemBuilder: (context, i) => Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: SpeakerCard(entry: list[i]),
        ),
      ),
    ];
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.scanning});
  final bool scanning;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          SpeakerArt(size: 130, broadcasting: scanning, dimmed: !scanning),
          const SizedBox(height: 22),
          Text(scanning ? 'Looking for speakers...' : 'No speakers found',
              style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w700)),
          const SizedBox(height: 8),
          const Text(
            'Switch your speaker on and keep it close to the phone. '
            'Speakers advertise as WMP-Control.',
            textAlign: TextAlign.center,
            style: TextStyle(color: AppColors.textDim, height: 1.4),
          ),
          const SizedBox(height: 20),
          if (!scanning)
            GradientButton(
              label: 'Scan again',
              icon: Icons.radar_rounded,
              onPressed: context.read<AppState>().scan,
            ),
        ],
      ),
    );
  }
}

/// Party overview + one-tap party switch for all connected speakers.
class _PartyBanner extends StatelessWidget {
  const _PartyBanner();

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final connected = app.connected;
    final on = app.anyBroadcasting;
    final busy = connected.any((c) => c.broadcastBusy);
    SpeakerController? host;
    for (final c in connected) {
      if (c.status?.partyRole == PartyRole.master) host = c;
    }
    final size = connected
        .map((c) => c.partySize.code)
        .fold<int>(0, (a, b) => b > a ? b : a);
    final subtitle = on
        ? [
            if (host != null) 'Hosted by ${host.displayName}',
            if (size > 0) PartySize(size).label,
          ].join(' · ')
        : '${connected.length} connected · Party is off';
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
      child: Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(24),
          gradient: LinearGradient(
            colors: on
                ? [AppColors.teal.withValues(alpha: 0.28), AppColors.violet.withValues(alpha: 0.35)]
                : [AppColors.surfaceHigh, AppColors.surface],
          ),
          border: Border.all(color: AppColors.outline, width: 0.6),
        ),
        child: Row(
          children: [
            SpeakerArt(size: 56, broadcasting: on, active: on),
            const SizedBox(width: 14),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(on ? 'Party is on' : 'Start a party',
                      style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 3),
                  Text(
                    subtitle.isEmpty ? 'Waiting for speakers to join' : subtitle,
                    style: const TextStyle(fontSize: 13, color: AppColors.textDim),
                  ),
                ],
              ),
            ),
            if (busy)
              const Padding(
                padding: EdgeInsets.all(8),
                child: SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2)),
              )
            else
              FilledButton.tonal(
                key: const Key('party-all'),
                onPressed: () => app.setPartyAll(!on),
                child: Text(on ? 'End' : 'Party'),
              ),
          ],
        ),
      ),
    );
  }
}

class SpeakerCard extends StatelessWidget {
  const SpeakerCard({super.key, required this.entry});

  final SpeakerEntry entry;

  @override
  Widget build(BuildContext context) {
    final c = entry.controller;
    final ready = c?.isReady ?? false;
    final s = ready ? c!.status : null;
    final busy = c?.isBusy ?? false;
    final failed = c?.state == LinkState.failed;

    Widget trailing;
    if (busy) {
      trailing = const SizedBox(width: 24, height: 24, child: CircularProgressIndicator(strokeWidth: 2));
    } else if (ready) {
      trailing = Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Icon(Icons.volume_up_rounded, size: 18, color: AppColors.textDim),
          Text('${c!.volume ?? s?.volume ?? 0}%',
              style: const TextStyle(fontWeight: FontWeight.w700)),
        ],
      );
    } else {
      trailing = OutlinedButton(
        onPressed: entry.isNearby || failed
            ? () => HomeScreen.openSpeaker(context, entry.id)
            : null,
        child: Text(failed ? 'Retry' : 'Connect'),
      );
    }

    String subtitle;
    if (busy) {
      subtitle = connectStageLabel(c!.stage, reconnecting: c.state == LinkState.reconnecting);
    } else if (failed) {
      subtitle = c!.error ?? 'Not connected';
    } else if (ready && c!.inHandover) {
      subtitle = 'Switching to party mode...';
    } else if (ready && c!.playPrompt) {
      subtitle = 'Press Play on your phone to start the party';
    } else if (ready) {
      subtitle = s == null ? 'Connected' : (s.phoneConnected ? 'Phone connected' : 'No phone audio');
    } else if (entry.isNearby) {
      final rssi = entry.seen!.rssi;
      subtitle = 'Nearby · ${rssi > -65 ? 'strong' : rssi > -80 ? 'good' : 'weak'} signal';
    } else {
      subtitle = 'Not in range';
    }

    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: (ready || busy || entry.isNearby || failed)
            ? () => HomeScreen.openSpeaker(context, entry.id)
            : null,
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            children: [
              SpeakerArt(
                size: 64,
                active: s?.streaming ?? false,
                broadcasting: s?.partyRole == PartyRole.master,
                dimmed: !ready,
              ),
              const SizedBox(width: 14),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(entry.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 3),
                    Text(subtitle,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: TextStyle(
                            fontSize: 13, color: failed ? AppColors.amber : AppColors.textDim)),
                    if (ready && c!.status != null) ...[
                      const SizedBox(height: 8),
                      StatusPills(controller: c),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 10),
              trailing,
            ],
          ),
        ),
      ),
    );
  }
}
