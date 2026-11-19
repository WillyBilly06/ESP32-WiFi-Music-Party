import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// Original stylised speaker: a rounded capsule with a ring radiator. Pulses
/// when [active] (streaming) and shows broadcast rings when [broadcasting].
class SpeakerArt extends StatefulWidget {
  const SpeakerArt({
    super.key,
    this.size = 96,
    this.active = false,
    this.broadcasting = false,
    this.dimmed = false,
  });

  final double size;
  final bool active;
  final bool broadcasting;
  final bool dimmed;

  /// Widget tests switch this off so `pumpAndSettle` can settle.
  static bool animationsEnabled = true;

  @override
  State<SpeakerArt> createState() => _SpeakerArtState();
}

class _SpeakerArtState extends State<SpeakerArt> with SingleTickerProviderStateMixin {
  late final AnimationController _ctl =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 2400));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(SpeakerArt oldWidget) {
    super.didUpdateWidget(oldWidget);
    _sync();
  }

  void _sync() {
    final animate = (widget.active || widget.broadcasting) && !widget.dimmed;
    final disabled = !SpeakerArt.animationsEnabled ||
        MediaQuery.maybeDisableAnimationsOf(context) == true;
    if (animate && !disabled) {
      if (!_ctl.isAnimating) _ctl.repeat();
    } else {
      _ctl.stop();
    }
  }

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox.square(
      dimension: widget.size,
      child: AnimatedBuilder(
        animation: _ctl,
        builder: (context, _) => CustomPaint(
          painter: _SpeakerPainter(
            t: _ctl.value,
            active: widget.active,
            broadcasting: widget.broadcasting,
            dimmed: widget.dimmed,
          ),
        ),
      ),
    );
  }
}

class _SpeakerPainter extends CustomPainter {
  _SpeakerPainter({
    required this.t,
    required this.active,
    required this.broadcasting,
    required this.dimmed,
  });

  final double t;
  final bool active;
  final bool broadcasting;
  final bool dimmed;

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    final c = Offset(w / 2, h / 2);

    if (broadcasting && !dimmed) {
      for (var i = 0; i < 3; i++) {
        final p = (t + i / 3) % 1.0;
        final r = w * (0.36 + 0.16 * p);
        canvas.drawCircle(
          c,
          r,
          Paint()
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.6
            ..color = AppColors.violet.withValues(alpha: (1 - p) * 0.55),
        );
      }
    }

    final body = RRect.fromRectAndRadius(
      Rect.fromCenter(center: c, width: w * 0.62, height: h * 0.72),
      Radius.circular(w * 0.2),
    );
    final bodyPaint = Paint()
      ..shader = LinearGradient(
        begin: Alignment.topLeft,
        end: Alignment.bottomRight,
        colors: dimmed
            ? [const Color(0xFF2A303A), const Color(0xFF1A1E25)]
            : [const Color(0xFF2B3547), const Color(0xFF141922)],
      ).createShader(body.outerRect);
    canvas.drawRRect(body, bodyPaint);
    canvas.drawRRect(
      body,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2
        ..color = Colors.white.withValues(alpha: dimmed ? 0.06 : 0.12),
    );

    final pulse = active && !dimmed ? 0.5 + 0.5 * math.sin(t * 2 * math.pi * 2) : 0.0;
    final ringR = w * 0.19;
    final ringCenter = c.translate(0, h * 0.04);
    canvas.drawCircle(
      ringCenter,
      ringR * (1 + 0.04 * pulse),
      Paint()
        ..shader = (dimmed
                ? const LinearGradient(colors: [Color(0xFF3A414D), Color(0xFF2A303A)])
                : AppColors.accentGradient)
            .createShader(Rect.fromCircle(center: ringCenter, radius: ringR))
        ..style = PaintingStyle.stroke
        ..strokeWidth = w * 0.045,
    );
    canvas.drawCircle(
      ringCenter,
      ringR * 0.55,
      Paint()..color = const Color(0xFF0E1218),
    );
    if (active && !dimmed) {
      canvas.drawCircle(
        ringCenter,
        ringR * (0.25 + 0.08 * pulse),
        Paint()..color = AppColors.teal.withValues(alpha: 0.35 + 0.3 * pulse),
      );
    }
    // status light
    canvas.drawCircle(
      c.translate(0, -h * 0.26),
      w * 0.022,
      Paint()
        ..color = dimmed
            ? const Color(0xFF3A414D)
            : (active ? AppColors.teal : Colors.white.withValues(alpha: 0.5)),
    );
  }

  @override
  bool shouldRepaint(_SpeakerPainter old) =>
      old.t != t || old.active != active || old.broadcasting != broadcasting || old.dimmed != dimmed;
}
