import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../protocol/eq.dart';
import '../protocol/wmp_protocol.dart';
import '../state/app_state.dart';
import '../state/speaker_controller.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/eq_curve.dart';

class EqScreen extends StatefulWidget {
  const EqScreen({super.key, required this.controller});
  final SpeakerController controller;

  @override
  State<EqScreen> createState() => _EqScreenState();
}

class _EqScreenState extends State<EqScreen> {
  StreamSubscription<String>? _notices;

  SpeakerController get c => widget.controller;

  @override
  void initState() {
    super.initState();
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
    super.dispose();
  }

  Future<void> _savePreset(List<int> gains) async {
    final app = context.read<AppState>();
    final messenger = ScaffoldMessenger.of(context);
    final ctl = TextEditingController();
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Save preset'),
        content: TextField(
          key: const Key('preset-name'),
          controller: ctl,
          autofocus: true,
          maxLength: 20,
          decoration: const InputDecoration(hintText: 'e.g. Late night'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, ctl.text), child: const Text('Save')),
        ],
      ),
    );
    if (name == null) return;
    final p = await app.savePreset(name, gains);
    messenger.showSnackBar(SnackBar(content: Text('Saved "${p.name}"')));
  }

  Future<void> _deletePreset(EqPreset p) async {
    final app = context.read<AppState>();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Delete "${p.name}"?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
    );
    if (ok == true) await app.deletePreset(p.id);
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final gains = c.eq ?? flatGains();
        final preset = matchPreset(gains, app.customPresets);
        final enabled = c.isReady && c.eq != null;
        return Scaffold(
          appBar: AppBar(
            title: const Text('Equalizer'),
            actions: [
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Center(child: SyncDot(busy: c.eqBusy)),
              ),
            ],
          ),
          body: AbsorbPointer(
            absorbing: !enabled,
            child: Opacity(
              opacity: enabled ? 1 : 0.45,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
                children: [
                  Text(c.displayName, style: const TextStyle(color: AppColors.textDim)),
                  const SizedBox(height: 4),
                  Text(preset?.name ?? 'Custom',
                      key: const Key('eq-preset-name'),
                      style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
                  const SizedBox(height: 14),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 16, 12, 12),
                      child: EqCurve(gains: gains, height: 140),
                    ),
                  ),
                  const SizedBox(height: 16),
                  const Text('PRESETS',
                      style: TextStyle(
                          fontSize: 12.5, letterSpacing: 1.3, color: AppColors.textDim, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final p in app.allPresets)
                        GestureDetector(
                          onLongPress: p.builtIn ? null : () => _deletePreset(p),
                          child: ChoiceChip(
                            label: Text(p.name),
                            avatar: p.builtIn ? null : const Icon(Icons.person_rounded, size: 16),
                            selected: preset?.id == p.id,
                            onSelected: (_) => c.setEq(p.gains),
                          ),
                        ),
                    ],
                  ),
                  if (app.customPresets.isNotEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 6),
                      child: Text('Long-press one of your presets to delete it.',
                          style: TextStyle(fontSize: 12, color: AppColors.textDim)),
                    ),
                  const SizedBox(height: 20),
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(2, 16, 2, 12),
                      child: Row(
                        children: [
                          for (var i = 0; i < kEqBands; i++)
                            Expanded(
                              child: _BandSlider(
                                key: Key('eq-band-$i'),
                                label: kEqBandLabels[i],
                                value: gains[i],
                                onChanged: (db) => c.setEqBand(i, db),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          key: const Key('eq-reset'),
                          onPressed: () => c.setEq(kBuiltInPresets.first.gains),
                          icon: const Icon(Icons.restart_alt_rounded),
                          label: const Text('Flat'),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: FilledButton.icon(
                          key: const Key('eq-save'),
                          onPressed: preset == null ? () => _savePreset(gains) : null,
                          icon: const Icon(Icons.bookmark_add_rounded),
                          label: const Text('Save preset'),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  const Text(
                    'Low shelf 63 Hz · peaks at 160 Hz, 400 Hz, 1 kHz, 2.5 kHz, 6.3 kHz · '
                    'high shelf 16 kHz. '
                    'Saved on the speaker and applied to its own output only.',
                    style: TextStyle(fontSize: 12, color: AppColors.textDim, height: 1.4),
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}

class _BandSlider extends StatelessWidget {
  const _BandSlider({super.key, required this.label, required this.value, required this.onChanged});

  final String label;
  final int value;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        FittedBox(
          fit: BoxFit.scaleDown,
          child: Text('${value > 0 ? '+' : ''}$value',
              style: const TextStyle(fontWeight: FontWeight.w800, fontFeatures: [FontFeature.tabularFigures()])),
        ),
        SizedBox(
          height: 220,
          child: RotatedBox(
            quarterTurns: 3,
            child: Slider(
              value: value.toDouble(),
              min: kEqMinDb.toDouble(),
              max: kEqMaxDb.toDouble(),
              divisions: kEqMaxDb - kEqMinDb,
              label: '$value dB',
              onChanged: (x) => onChanged(x.round()),
            ),
          ),
        ),
        FittedBox(
          fit: BoxFit.scaleDown,
          child: Text(label, style: const TextStyle(color: AppColors.textDim, fontSize: 12.5)),
        ),
        const Text('Hz', style: TextStyle(color: AppColors.textDim, fontSize: 10)),
      ],
    );
  }
}
