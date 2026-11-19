import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../protocol/eq.dart';
import '../protocol/wmp_protocol.dart';
import '../state/app_state.dart';
import '../state/speaker_controller.dart';
import 'eq_screen.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/eq_curve.dart';
import 'widgets/speaker_art.dart';

class SpeakerScreen extends StatefulWidget {
  const SpeakerScreen({super.key, required this.controller});

  final SpeakerController controller;

  @override
  State<SpeakerScreen> createState() => _SpeakerScreenState();
}

class _SpeakerScreenState extends State<SpeakerScreen> {
  StreamSubscription<String>? _notices;

  SpeakerController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.watchDetails();
    _notices = c.notices.listen((msg) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(msg)));
    });
  }

  @override
  void dispose() {
    _notices?.cancel();
    c.unwatchDetails();
    super.dispose();
  }

  Future<void> _rename() async {
    final app = context.read<AppState>();
    final ctl = TextEditingController(text: c.displayName);
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Rename speaker'),
        content: TextField(
          controller: ctl,
          autofocus: true,
          maxLength: 24,
          decoration: InputDecoration(helperText: 'Shown only in this app · ${c.advName}'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctl.text), child: const Text('Save')),
        ],
      ),
    );
    if (name != null) await app.rename(c.id, name);
  }

  Future<void> _forget() async {
    final app = context.read<AppState>();
    final nav = Navigator.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Forget ${c.displayName}?'),
        content: const Text(
            'The app disconnects and removes it from your saved speakers. Settings stay on the speaker.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Forget')),
        ],
      ),
    );
    if (ok == true) {
      nav.pop();
      await app.forget(c.id);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final ready = c.isReady;
        return Scaffold(
          appBar: AppBar(
            title: Text(c.displayName),
            actions: [
              PopupMenuButton<String>(
                onSelected: (v) {
                  switch (v) {
                    case 'rename':
                      _rename();
                    case 'disconnect':
                      c.disconnect();
                    case 'connect':
                      context.read<AppState>().connect(c.id);
                    case 'forget':
                      _forget();
                  }
                },
                itemBuilder: (_) => [
                  const PopupMenuItem(value: 'rename', child: Text('Rename')),
                  if (ready)
                    const PopupMenuItem(value: 'disconnect', child: Text('Disconnect'))
                  else if (!c.isBusy)
                    const PopupMenuItem(value: 'connect', child: Text('Connect')),
                  const PopupMenuItem(value: 'forget', child: Text('Forget speaker')),
                ],
              ),
            ],
          ),
          body: RefreshIndicator(
            onRefresh: c.refresh,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 4, 16, 40),
              children: [
                _Hero(controller: c),
                if (!ready) ...[
                  const SizedBox(height: 14),
                  _LinkBanner(controller: c),
                ],
                const SizedBox(height: 16),
                AbsorbPointer(
                  absorbing: !ready,
                  child: AnimatedOpacity(
                    opacity: ready ? 1 : 0.4,
                    duration: const Duration(milliseconds: 250),
                    child: Column(
                      children: [
                        _VolumeCard(controller: c),
                        const SizedBox(height: 12),
                        _BroadcastCard(controller: c),
                        const SizedBox(height: 12),
                        _StereoCard(controller: c),
                        const SizedBox(height: 12),
                        _EqCard(controller: c),
                        if (c.status?.partyRole == PartyRole.master) ...[
                          const SizedBox(height: 12),
                          _LatencyCard(controller: c),
                        ],
                        const SizedBox(height: 12),
                        _DetailsCard(controller: c),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final s = controller.status;
    return Column(
      children: [
        SpeakerArt(
          size: 170,
          active: controller.isReady && (s?.streaming ?? false),
          broadcasting: controller.isReady && s?.partyRole == PartyRole.master,
          dimmed: !controller.isReady,
        ),
        const SizedBox(height: 8),
        if (controller.isReady) StatusPills(controller: controller),
      ],
    );
  }
}

class _LinkBanner extends StatelessWidget {
  const _LinkBanner({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final busy = controller.isBusy;
    final text = busy
        ? connectStageLabel(controller.stage, reconnecting: controller.state == LinkState.reconnecting)
        : (controller.error ?? 'Not connected');
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: busy ? AppColors.surfaceHigh : AppColors.amber.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Row(
        children: [
          if (busy)
            const SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2))
          else
            const Icon(Icons.link_off_rounded, color: AppColors.amber),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(text, style: const TextStyle(fontWeight: FontWeight.w600)),
              ],
            ),
          ),
          if (!busy)
            TextButton(
              onPressed: () => context.read<AppState>().connect(controller.id),
              child: const Text('Reconnect'),
            ),
        ],
      ),
    );
  }
}

class _VolumeCard extends StatelessWidget {
  const _VolumeCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final v = (controller.volume ?? controller.status?.volume ?? 0).toDouble();
    return SectionCard(
      title: 'Volume',
      icon: Icons.volume_up_rounded,
      trailing: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SyncDot(busy: controller.volumeBusy),
          const SizedBox(width: 10),
          Text('${v.round()}%', style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w800)),
        ],
      ),
      child: Row(
        children: [
          IconButton(
            tooltip: 'Quieter',
            onPressed: () => controller.setVolume(v.round() - 5),
            icon: const Icon(Icons.volume_mute_rounded, color: AppColors.textDim),
          ),
          Expanded(
            child: Slider(
              key: const Key('volume-slider'),
              value: v.clamp(0, 100),
              max: 100,
              divisions: 100,
              label: '${v.round()}%',
              onChanged: (x) => controller.setVolume(x.round()),
            ),
          ),
          IconButton(
            tooltip: 'Louder',
            onPressed: () => controller.setVolume(v.round() + 5),
            icon: const Icon(Icons.volume_up_rounded, color: AppColors.textDim),
          ),
        ],
      ),
    );
  }
}

class _BroadcastCard extends StatelessWidget {
  const _BroadcastCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final b = controller.broadcast;
    final on = controller.broadcastRequested ?? b?.on ?? false;
    final transitioning = controller.broadcastBusy || (b?.transitioning ?? false);
    final party = controller.partySize;
    String state;
    if (controller.inHandover) {
      state = 'Switching to party mode...';
    } else if (transitioning) {
      state = on ? 'Starting Wi-Fi party...' : 'Stopping Wi-Fi...';
    } else if (on) {
      state = party.known ? 'Party on · ${party.label}' : 'Party on · looking for speakers';
    } else {
      state = 'Off · playing solo';
    }
    return SectionCard(
      title: 'Broadcast',
      subtitle: state,
      icon: Icons.cell_tower_rounded,
      trailing: Switch(
        key: const Key('broadcast-switch'),
        value: on,
        onChanged: b == null ? null : controller.setBroadcast,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (controller.inHandover) ...[
            const _HandoverBanner(),
            const SizedBox(height: 10),
          ] else if (controller.playPrompt) ...[
            _PlayPrompt(onDismiss: controller.dismissPlayPrompt),
            const SizedBox(height: 10),
          ],
          _ModeRow(
            selected: !on,
            icon: Icons.speaker_rounded,
            title: 'Off: solo',
            text: 'Wi-Fi off. This speaker plays alone and offers every codec '
                '(SBC, AAC, aptX, aptX HD) for the best sound.',
          ),
          const SizedBox(height: 10),
          _ModeRow(
            selected: on,
            icon: Icons.groups_rounded,
            title: 'On: party',
            text: 'The speaker with your phone hosts the party and shares the music over '
                'Wi-Fi; other speakers with Broadcast on join within seconds. '
                'The phone is moved to the party codec, so its audio may briefly '
                'reconnect and pause.',
          ),
          if (b != null && b.supportsCodecChoice) ...[
            const SizedBox(height: 14),
            _PartyCodecPicker(controller: controller),
          ],
          const SizedBox(height: 10),
          const Text('Off again after every power-on.',
              style: TextStyle(fontSize: 12, color: AppColors.textDim)),
        ],
      ),
    );
  }
}

class _HandoverBanner extends StatelessWidget {
  const _HandoverBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const Key('handover-banner'),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: AppColors.violet.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(14),
      ),
      child: const Row(
        children: [
          SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2)),
          SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Switching to party mode...', style: TextStyle(fontWeight: FontWeight.w700)),
                SizedBox(height: 2),
                Text('The speaker is reconnecting your phone with the party codec. '
                    'Music may pause for a moment.',
                    style: TextStyle(fontSize: 12.5, color: AppColors.textDim, height: 1.35)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _PlayPrompt extends StatelessWidget {
  const _PlayPrompt({required this.onDismiss});
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const Key('play-prompt'),
      padding: const EdgeInsets.fromLTRB(12, 12, 4, 12),
      decoration: BoxDecoration(
        gradient: LinearGradient(colors: [
          AppColors.teal.withValues(alpha: 0.22),
          AppColors.violet.withValues(alpha: 0.22),
        ]),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          const CircleAvatar(
            radius: 18,
            backgroundColor: AppColors.teal,
            child: Icon(Icons.play_arrow_rounded, color: Color(0xFF07131A)),
          ),
          const SizedBox(width: 12),
          const Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Party mode is ready', style: TextStyle(fontWeight: FontWeight.w700)),
                SizedBox(height: 2),
                Text('Press Play on your phone to start the party.',
                    style: TextStyle(fontSize: 12.5, color: AppColors.textDim)),
              ],
            ),
          ),
          IconButton(
            tooltip: 'Dismiss',
            onPressed: onDismiss,
            icon: const Icon(Icons.close_rounded, size: 18, color: AppColors.textDim),
          ),
        ],
      ),
    );
  }
}

class _PartyCodecPicker extends StatelessWidget {
  const _PartyCodecPicker({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final codec = controller.broadcast?.codec ?? PartyCodec.sbc;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Expanded(
              child: Text('Party codec', style: TextStyle(fontWeight: FontWeight.w700)),
            ),
            SyncDot(busy: controller.codecBusy),
          ],
        ),
        const SizedBox(height: 8),
        SizedBox(
          width: double.infinity,
          child: SegmentedButton<PartyCodec>(
            key: const Key('party-codec'),
            showSelectedIcon: false,
            segments: [
              for (final c in PartyCodec.values) ButtonSegment(value: c, label: Text(c.label)),
            ],
            selected: {codec},
            onSelectionChanged: (sel) => controller.setPartyCodec(sel.first),
          ),
        ),
        const SizedBox(height: 6),
        Text(
          codec == PartyCodec.sbc
              ? 'SBC only (default): every phone supports it.'
              : 'The phone may also use AAC-LC while the party is on.',
          style: const TextStyle(fontSize: 12.5, color: AppColors.textDim),
        ),
      ],
    );
  }
}

class _ModeRow extends StatelessWidget {
  const _ModeRow({required this.selected, required this.icon, required this.title, required this.text});
  final bool selected;
  final IconData icon;
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 250),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: selected ? AppColors.teal.withValues(alpha: 0.08) : Colors.transparent,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: selected ? AppColors.teal.withValues(alpha: 0.5) : AppColors.outline),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 20, color: selected ? AppColors.teal : AppColors.textDim),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(text, style: const TextStyle(fontSize: 12.5, color: AppColors.textDim, height: 1.35)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _StereoCard extends StatelessWidget {
  const _StereoCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final ch = controller.channel;
    final setting = ch?.setting ?? SpeakerChannel.stereo;
    final party = controller.partySize;
    final note = party.isExactlyTwo
        ? (setting == SpeakerChannel.stereo
            ? 'Two speakers in the party: pick Left or Right to make a stereo pair.'
            : 'Stereo pair active.')
        : 'Left/Right applies only while exactly 2 speakers are in the party. '
            'Otherwise every speaker plays full stereo.';
    return SectionCard(
      title: 'Stereo pair',
      icon: Icons.surround_sound_rounded,
      subtitle: '${party.label} · Playing ${channelLabel(ch?.output ?? SpeakerChannel.stereo)}',
      trailing: SyncDot(busy: controller.channelBusy),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: double.infinity,
            child: SegmentedButton<SpeakerChannel>(
              key: const Key('channel-picker'),
              showSelectedIcon: false,
              segments: const [
                ButtonSegment(value: SpeakerChannel.left, label: Text('L'), icon: Icon(Icons.chevron_left_rounded)),
                ButtonSegment(value: SpeakerChannel.stereo, label: Text('Stereo')),
                ButtonSegment(value: SpeakerChannel.right, label: Text('R'), icon: Icon(Icons.chevron_right_rounded)),
              ],
              selected: {setting},
              onSelectionChanged: ch == null ? null : (s) => controller.setChannel(s.first),
            ),
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                party.isExactlyTwo ? Icons.check_circle_rounded : Icons.info_outline_rounded,
                size: 16,
                color: party.isExactlyTwo ? AppColors.teal : AppColors.textDim,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(note, style: const TextStyle(fontSize: 12.5, color: AppColors.textDim, height: 1.35)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _EqCard extends StatelessWidget {
  const _EqCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final gains = controller.eq ?? flatGains();
    final preset = matchPreset(gains, app.customPresets);
    return SectionCard(
      title: 'Equalizer',
      icon: Icons.equalizer_rounded,
      subtitle: controller.eq == null ? 'Loading...' : (preset?.name ?? 'Custom'),
      trailing: SyncDot(busy: controller.eqBusy),
      child: Column(
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute<void>(builder: (_) => EqScreen(controller: controller)),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: EqCurve(gains: gains, height: 70),
            ),
          ),
          const SizedBox(height: 10),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final p in app.allPresets)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(p.name),
                      selected: preset?.id == p.id,
                      onSelected: (_) => controller.setEq(p.gains),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              key: const Key('eq-customize'),
              onPressed: () => Navigator.of(context).push(
                MaterialPageRoute<void>(builder: (_) => EqScreen(controller: controller)),
              ),
              icon: const Icon(Icons.tune_rounded, size: 18),
              label: const Text('Customize'),
            ),
          ),
        ],
      ),
    );
  }
}

class _LatencyCard extends StatelessWidget {
  const _LatencyCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final ms = (controller.latencyMs ?? controller.status?.latencyMs ?? kLatencyMinMs)
        .clamp(kLatencyMinMs, kLatencyMaxMs);
    return SectionCard(
      title: 'Party delay',
      icon: Icons.timer_outlined,
      subtitle: 'Host only · shared by every speaker in the party',
      trailing: Row(mainAxisSize: MainAxisSize.min, children: [
        SyncDot(busy: controller.latencyBusy),
        const SizedBox(width: 10),
        Text('$ms ms', style: const TextStyle(fontWeight: FontWeight.w800)),
      ]),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Slider(
            value: ms.toDouble(),
            min: kLatencyMinMs.toDouble(),
            max: kLatencyMaxMs.toDouble(),
            divisions: (kLatencyMaxMs - kLatencyMinMs) ~/ 10,
            label: '$ms ms',
            onChanged: (x) => controller.setLatency(x.round()),
          ),
          const Text('More delay gives the Wi-Fi party more headroom against dropouts.',
              style: TextStyle(fontSize: 12.5, color: AppColors.textDim)),
        ],
      ),
    );
  }
}

class _DetailsCard extends StatelessWidget {
  const _DetailsCard({required this.controller});
  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final s = controller.status;
    final rows = <(String, String)>[
      if (s != null) ...[
        ('Role', switch (s.role) {
          SpeakerRole.partyMaster => 'Party host (broadcasting)',
          SpeakerRole.playing => s.followingParty ? 'Playing · following a party' : 'Playing',
          SpeakerRole.idle => 'Idle',
          SpeakerRole.unknown => 'Unknown',
        }),
        ('Phone audio', s.phoneConnected ? (s.streaming ? 'Connected · streaming' : 'Connected') : 'Not connected'),
        ('Sample rate', formatSampleRate(s.sampleRateHz) ?? 'No stream'),
        ('Audio clock', s.audioRunning ? 'Running' : 'Stopped'),
        ('Party delay', '${s.latencyMs} ms'),
        ('Stutters (concealed)', '${s.concealedGroups}'),
        ('Late groups', '${s.lateGroups}'),
        ('Status format', s.isKnownVersion ? 'v${s.version}' : 'v${s.version} (newer than app)'),
      ],
      if (controller.broadcast != null)
        ('Wi-Fi', controller.broadcast!.wifiReady ? 'Ready' : 'Changing...'),
      ('Firmware', controller.firmware ?? '-'),
      ('Address', controller.id),
      if (controller.layout != null)
        ('UUID byte order', controller.layout == UuidLayout.canonical ? 'As documented' : 'Reversed'),
    ];
    return Card(
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          key: const Key('details-tile'),
          leading: const Icon(Icons.insights_rounded, color: AppColors.teal),
          title: const Text('Status details', style: TextStyle(fontWeight: FontWeight.w700)),
          subtitle: Text(
            s == null ? 'No status yet' : 'Stutters ${s.concealedGroups} · late ${s.lateGroups}',
            style: const TextStyle(fontSize: 12.5, color: AppColors.textDim),
          ),
          childrenPadding: const EdgeInsets.fromLTRB(18, 0, 18, 14),
          children: [
            for (final (k, v) in rows)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 5),
                child: Row(
                  children: [
                    Expanded(child: Text(k, style: const TextStyle(color: AppColors.textDim))),
                    Flexible(
                      child: Text(v,
                          textAlign: TextAlign.right,
                          style: const TextStyle(fontWeight: FontWeight.w600, fontFeatures: [FontFeature.tabularFigures()])),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
