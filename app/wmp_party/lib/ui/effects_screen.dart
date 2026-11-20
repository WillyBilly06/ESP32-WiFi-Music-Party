import 'dart:async';

import 'package:flutter/material.dart';

import '../protocol/effects.dart';
import '../state/speaker_controller.dart';
import 'theme.dart';
import 'widgets/common.dart';

/// Karaoke / effects: vocal remover, echo, reverb, chorus.
class EffectsScreen extends StatefulWidget {
  const EffectsScreen({super.key, required this.controller});
  final SpeakerController controller;

  @override
  State<EffectsScreen> createState() => _EffectsScreenState();
}

class _EffectsScreenState extends State<EffectsScreen> {
  StreamSubscription<String>? _notices;

  SpeakerController get c => widget.controller;

  @override
  void initState() {
    super.initState();
    c.loadEffects();
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

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: c,
      builder: (context, _) {
        final st = c.effects;
        final following = st?.status.following ?? false;
        final editable = c.isReady && st != null && !following;
        final s = st?.settings ?? EffectsSettings.defaults;
        EffectsPreset? preset;
        for (final p in kEffectsPresets) {
          if (st != null && p.matches(s)) {
            preset = p;
            break;
          }
        }
        return Scaffold(
          appBar: AppBar(
            title: const Text('Karaoke & effects'),
            actions: [
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: Center(child: SyncDot(busy: c.effectsBusy)),
              ),
            ],
          ),
          body: st == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
                  children: [
                    Text(c.displayName, style: const TextStyle(color: AppColors.textDim)),
                    const SizedBox(height: 10),
                    if (following)
                      const _Banner(
                        key: Key('fx-following'),
                        icon: Icons.link_rounded,
                        color: AppColors.violet,
                        text: "Following the party host's effects",
                        detail: 'In a party the host speaker sets the effects for every speaker. '
                            'Change them on the host.',
                      ),
                    if (st.status.outOfMemory)
                      const _Banner(
                        key: Key('fx-memory'),
                        icon: Icons.memory_rounded,
                        color: AppColors.amber,
                        text: 'Not enough memory for this effect right now',
                        detail: 'A busy party host may not have room for every effect. '
                            'Effects marked "not running" are switched on but refused.',
                      ),
                    const SizedBox(height: 4),
                    const Text('PRESETS',
                        style: TextStyle(
                            fontSize: 12.5, letterSpacing: 1.3, color: AppColors.textDim, fontWeight: FontWeight.w700)),
                    const SizedBox(height: 8),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final p in kEffectsPresets)
                          ChoiceChip(
                            key: Key('fx-preset-${p.id}'),
                            label: Text(p.name),
                            selected: preset?.id == p.id,
                            onSelected: editable ? (_) => c.setEffectsNow(p.apply(s)) : null,
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    AbsorbPointer(
                      absorbing: !editable,
                      child: Opacity(
                        opacity: editable ? 1 : 0.5,
                        child: Column(
                          children: [
                            _EffectCard(
                              effect: Effect.vocalRemover,
                              icon: Icons.mic_off_rounded,
                              subtitle: 'Removes centre-panned vocals for singing along',
                              controller: c,
                              sliders: [
                                _SliderSpec('Amount', s.vocalAmount, 1, '%', (v) => s.copyWith(vocalAmount: v)),
                              ],
                            ),
                            const SizedBox(height: 12),
                            _EffectCard(
                              effect: Effect.echo,
                              icon: Icons.graphic_eq_rounded,
                              subtitle: 'Repeats of the sound',
                              controller: c,
                              sliders: [
                                _SliderSpec('Delay', s.echoDelay, 2, '', (v) => s.copyWith(echoDelay: v),
                                    format: (v) => '${v * 10} ms'),
                                _SliderSpec('Repeats', s.echoFeedback, 3, '%', (v) => s.copyWith(echoFeedback: v)),
                                _SliderSpec('Mix', s.echoMix, 4, '%', (v) => s.copyWith(echoMix: v)),
                              ],
                            ),
                            const SizedBox(height: 12),
                            _EffectCard(
                              effect: Effect.reverb,
                              icon: Icons.blur_on_rounded,
                              subtitle: 'Room and hall ambience',
                              controller: c,
                              sliders: [
                                _SliderSpec('Size', s.reverbSize, 5, '', (v) => s.copyWith(reverbSize: v)),
                                _SliderSpec('Damping', s.reverbDamping, 6, '', (v) => s.copyWith(reverbDamping: v),
                                    hint: 'higher = darker'),
                                _SliderSpec('Mix', s.reverbMix, 7, '%', (v) => s.copyWith(reverbMix: v)),
                              ],
                            ),
                            const SizedBox(height: 12),
                            _EffectCard(
                              effect: Effect.chorus,
                              icon: Icons.waves_rounded,
                              subtitle: 'Shimmering, doubled sound',
                              controller: c,
                              sliders: [
                                _SliderSpec('Rate', s.chorusRate, 8, '', (v) => s.copyWith(chorusRate: v),
                                    format: (v) => '${(v / 10).toStringAsFixed(1)} Hz'),
                                _SliderSpec('Depth', s.chorusDepth, 9, '%', (v) => s.copyWith(chorusDepth: v)),
                                _SliderSpec('Mix', s.chorusMix, 10, '%', (v) => s.copyWith(chorusMix: v)),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 14),
                    const Text(
                      "In a party the host's effects apply to every speaker automatically.",
                      style: TextStyle(fontSize: 12, color: AppColors.textDim),
                    ),
                  ],
                ),
        );
      },
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({super.key, required this.icon, required this.color, required this.text, required this.detail});
  final IconData icon;
  final Color color;
  final String text;
  final String detail;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: color, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(text, style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 2),
                Text(detail, style: const TextStyle(fontSize: 12.5, color: AppColors.textDim, height: 1.35)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _SliderSpec {
  const _SliderSpec(this.label, this.value, this.byteIndex, this.unit, this.apply, {this.format, this.hint});
  final String label;
  final int value;

  /// Setting byte (1..10): picks the range in [kEffectRanges].
  final int byteIndex;
  final String unit;
  final EffectsSettings Function(int v) apply;
  final String Function(int v)? format;
  final String? hint;

  EffectRange get range => kEffectRanges[byteIndex - 1];
  String show(int v) => format?.call(v) ?? '$v$unit';
}

class _EffectCard extends StatelessWidget {
  const _EffectCard({
    required this.effect,
    required this.icon,
    required this.subtitle,
    required this.controller,
    required this.sliders,
  });

  final Effect effect;
  final IconData icon;
  final String subtitle;
  final SpeakerController controller;
  final List<_SliderSpec> sliders;

  @override
  Widget build(BuildContext context) {
    final st = controller.effects!;
    final on = st.settings.isOn(effect);
    final running = st.status.running(effect);
    final refused = on && !running && !controller.effectsBusy;
    return SectionCard(
      title: effect.label,
      icon: icon,
      subtitle: refused ? '$subtitle · not running' : subtitle,
      trailing: Switch(
        key: Key('fx-switch-${effect.name}'),
        value: on,
        onChanged: (v) => controller.setEffectsNow(st.settings.withEffect(effect, v)),
      ),
      child: AnimatedOpacity(
        opacity: on ? 1 : 0.45,
        duration: const Duration(milliseconds: 200),
        child: Column(
          children: [
            for (final sl in sliders)
              Row(
                children: [
                  SizedBox(
                    width: 74,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(sl.label, style: const TextStyle(fontWeight: FontWeight.w600)),
                        if (sl.hint != null)
                          Text(sl.hint!, style: const TextStyle(fontSize: 10.5, color: AppColors.textDim)),
                      ],
                    ),
                  ),
                  Expanded(
                    child: Slider(
                      key: Key('fx-${effect.name}-${sl.label.toLowerCase()}'),
                      value: sl.range.clamp(sl.value).toDouble(),
                      min: sl.range.min.toDouble(),
                      max: sl.range.max.toDouble(),
                      divisions: sl.range.max - sl.range.min,
                      label: sl.show(sl.value),
                      onChanged: (x) => controller.setEffects(sl.apply(x.round())),
                      onChangeEnd: (_) => controller.commitEffects(),
                    ),
                  ),
                  SizedBox(
                    width: 58,
                    child: Text(sl.show(sl.value),
                        textAlign: TextAlign.right,
                        style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 13)),
                  ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
