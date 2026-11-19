import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../protocol/eq.dart';
import '../../protocol/wmp_protocol.dart';
import '../theme.dart';

/// Smooth response curve of the 5-band EQ on a log frequency axis.
class EqCurve extends StatelessWidget {
  const EqCurve({super.key, required this.gains, this.height = 90, this.showGrid = true});

  final List<int> gains;
  final double height;
  final bool showGrid;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(painter: _EqCurvePainter(gains, showGrid)),
    );
  }
}

class _EqCurvePainter extends CustomPainter {
  _EqCurvePainter(this.gains, this.showGrid);
  final List<int> gains;
  final bool showGrid;

  static const _fMin = 20.0;
  static const _fMax = 20000.0;
  static const _range = 15.0; // dB shown above/below 0

  double _x(double f, double w) =>
      w * (math.log(f / _fMin) / math.log(_fMax / _fMin));

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width;
    final h = size.height;
    double y(double db) => h / 2 - (db.clamp(-_range, _range) / _range) * (h / 2 - 4);

    if (showGrid) {
      final grid = Paint()
        ..color = AppColors.outline
        ..strokeWidth = 0.6;
      canvas.drawLine(Offset(0, h / 2), Offset(w, h / 2), grid);
      for (final f in kEqBandHz) {
        final x = _x(f.toDouble(), w);
        canvas.drawLine(Offset(x, 0), Offset(x, h), grid..color = AppColors.outline.withValues(alpha: 0.5));
      }
    }

    final path = Path();
    final fill = Path();
    const steps = 120;
    for (var i = 0; i <= steps; i++) {
      final f = _fMin * math.pow(_fMax / _fMin, i / steps);
      final p = Offset(_x(f.toDouble(), w), y(eqResponseDb(gains, f.toDouble())));
      if (i == 0) {
        path.moveTo(p.dx, p.dy);
        fill.moveTo(p.dx, h / 2);
        fill.lineTo(p.dx, p.dy);
      } else {
        path.lineTo(p.dx, p.dy);
        fill.lineTo(p.dx, p.dy);
      }
    }
    fill
      ..lineTo(w, h / 2)
      ..close();

    final rect = Offset.zero & size;
    canvas.drawPath(
      fill,
      Paint()
        ..shader = LinearGradient(
          colors: [AppColors.teal.withValues(alpha: 0.22), AppColors.violet.withValues(alpha: 0.22)],
        ).createShader(rect),
    );
    canvas.drawPath(
      path,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.4
        ..strokeCap = StrokeCap.round
        ..shader = AppColors.accentGradient.createShader(rect),
    );
    for (var i = 0; i < kEqBands; i++) {
      final f = kEqBandHz[i].toDouble();
      canvas.drawCircle(
        Offset(_x(f, w), y(eqResponseDb(gains, f))),
        3.2,
        Paint()..color = Colors.white,
      );
    }
  }

  @override
  bool shouldRepaint(_EqCurvePainter old) =>
      old.showGrid != showGrid || !_same(old.gains, gains);

  static bool _same(List<int> a, List<int> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}
