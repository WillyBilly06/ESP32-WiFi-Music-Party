import 'package:flutter/material.dart';

import '../../ble/ble_backend.dart';
import '../../protocol/wmp_protocol.dart';
import '../../state/speaker_controller.dart';
import '../theme.dart';

String partyRoleLabel(PartyRole r) => switch (r) {
      PartyRole.master => 'Party host',
      PartyRole.following => 'Following',
      PartyRole.solo => 'Solo',
    };

IconData partyRoleIcon(PartyRole r) => switch (r) {
      PartyRole.master => Icons.cell_tower_rounded,
      PartyRole.following => Icons.link_rounded,
      PartyRole.solo => Icons.speaker_rounded,
    };

String channelLabel(SpeakerChannel c) => switch (c) {
      SpeakerChannel.stereo => 'Stereo',
      SpeakerChannel.left => 'Left',
      SpeakerChannel.right => 'Right',
    };

String connectStageLabel(ConnectStage? s, {bool reconnecting = false}) {
  if (reconnecting && s == null) return 'Reconnecting...';
  return switch (s) {
    ConnectStage.connecting || null => reconnecting ? 'Reconnecting...' : 'Connecting...',
    ConnectStage.discovering => 'Finding controls...',
    ConnectStage.subscribing => 'Subscribing to status...',
    ConnectStage.reading => 'Reading settings...',
  };
}

/// Small rounded tag.
class Pill extends StatelessWidget {
  const Pill({super.key, required this.label, this.icon, this.color, this.filled = false});

  final String label;
  final IconData? icon;
  final Color? color;
  final bool filled;

  @override
  Widget build(BuildContext context) {
    final c = color ?? AppColors.textDim;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: filled ? c.withValues(alpha: 0.16) : Colors.transparent,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: c.withValues(alpha: filled ? 0.0 : 0.5)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 13, color: c),
            const SizedBox(width: 4),
          ],
          Flexible(
            child: Text(label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, color: c, fontWeight: FontWeight.w600)),
          ),
        ],
      ),
    );
  }
}

/// Role + phone + stream tags for a connected speaker.
class StatusPills extends StatelessWidget {
  const StatusPills({super.key, required this.controller});

  final SpeakerController controller;

  @override
  Widget build(BuildContext context) {
    final s = controller.status;
    if (s == null) return const SizedBox.shrink();
    final rate = formatSampleRate(s.sampleRateHz);
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        Pill(
          label: partyRoleLabel(s.partyRole),
          icon: partyRoleIcon(s.partyRole),
          color: s.partyRole == PartyRole.solo ? AppColors.textDim : AppColors.violet,
          filled: s.partyRole != PartyRole.solo,
        ),
        if (s.phoneConnected)
          const Pill(label: 'Phone', icon: Icons.smartphone_rounded, color: AppColors.teal),
        if (s.streaming)
          Pill(
            label: rate == null ? 'Playing' : 'Playing $rate',
            icon: Icons.graphic_eq_rounded,
            color: AppColors.teal,
            filled: true,
          ),
        if (s.preparing) const Pill(label: 'Preparing', icon: Icons.hourglass_top_rounded, color: AppColors.amber),
      ],
    );
  }
}

/// A titled card section used on the speaker page.
class SectionCard extends StatelessWidget {
  const SectionCard({
    super.key,
    required this.title,
    required this.icon,
    required this.child,
    this.trailing,
    this.subtitle,
  });

  final String title;
  final IconData icon;
  final Widget child;
  final Widget? trailing;
  final String? subtitle;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 34,
                  height: 34,
                  decoration: BoxDecoration(
                    color: AppColors.surfaceHigh,
                    borderRadius: BorderRadius.circular(11),
                  ),
                  child: Icon(icon, size: 19, color: AppColors.teal),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title,
                          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700)),
                      if (subtitle != null)
                        Text(subtitle!,
                            style: const TextStyle(fontSize: 12.5, color: AppColors.textDim)),
                    ],
                  ),
                ),
                ?trailing,
              ],
            ),
            const SizedBox(height: 14),
            child,
          ],
        ),
      ),
    );
  }
}

/// Tiny "sending" indicator shown while a debounced write is in flight.
class SyncDot extends StatelessWidget {
  const SyncDot({super.key, required this.busy});
  final bool busy;

  @override
  Widget build(BuildContext context) {
    return AnimatedOpacity(
      opacity: busy ? 1 : 0,
      duration: const Duration(milliseconds: 200),
      child: const Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 12,
            height: 12,
            child: CircularProgressIndicator(strokeWidth: 1.6),
          ),
          SizedBox(width: 6),
          Text('Sending', style: TextStyle(fontSize: 12, color: AppColors.textDim)),
        ],
      ),
    );
  }
}

/// Gradient primary button.
class GradientButton extends StatelessWidget {
  const GradientButton({super.key, required this.label, required this.onPressed, this.icon});

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final enabled = onPressed != null;
    return Opacity(
      opacity: enabled ? 1 : 0.45,
      child: DecoratedBox(
        decoration: BoxDecoration(
          gradient: AppColors.accentGradient,
          borderRadius: BorderRadius.circular(30),
        ),
        child: Material(
          type: MaterialType.transparency,
          child: InkWell(
            borderRadius: BorderRadius.circular(30),
            onTap: onPressed,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 13),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  if (icon != null) ...[
                    Icon(icon, color: const Color(0xFF07131A), size: 20),
                    const SizedBox(width: 8),
                  ],
                  Flexible(
                    child: Text(label,
                        textAlign: TextAlign.center,
                        style: const TextStyle(
                            color: Color(0xFF07131A), fontWeight: FontWeight.w800, fontSize: 15)),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
