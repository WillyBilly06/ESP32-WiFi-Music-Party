import 'package:flutter/material.dart';

/// Original "night tide" look: deep blue-black surfaces with a teal-to-violet
/// accent gradient.
class AppColors {
  AppColors._();
  static const background = Color(0xFF0C0F14);
  static const surface = Color(0xFF151A22);
  static const surfaceHigh = Color(0xFF1D2430);
  static const outline = Color(0xFF2A3342);
  static const teal = Color(0xFF2FE3C3);
  static const violet = Color(0xFF8B6CFF);
  static const amber = Color(0xFFFFC15E);
  static const danger = Color(0xFFFF6B6B);
  static const textDim = Color(0xFF9AA6B8);

  static const accentGradient = LinearGradient(
    colors: [teal, violet],
    begin: Alignment.centerLeft,
    end: Alignment.centerRight,
  );
}

ThemeData buildTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: AppColors.teal,
    brightness: Brightness.dark,
  ).copyWith(
    primary: AppColors.teal,
    onPrimary: const Color(0xFF00261F),
    secondary: AppColors.violet,
    surface: AppColors.surface,
    surfaceContainerHighest: AppColors.surfaceHigh,
    outline: AppColors.outline,
    error: AppColors.danger,
  );
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: AppColors.background,
    brightness: Brightness.dark,
  );
  return base.copyWith(
    appBarTheme: const AppBarTheme(
      backgroundColor: AppColors.background,
      surfaceTintColor: Colors.transparent,
      elevation: 0,
      centerTitle: false,
    ),
    cardTheme: CardThemeData(
      color: AppColors.surface,
      elevation: 0,
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: const BorderSide(color: AppColors.outline, width: 0.6),
      ),
    ),
    sliderTheme: base.sliderTheme.copyWith(
      trackHeight: 6,
      activeTrackColor: AppColors.teal,
      inactiveTrackColor: AppColors.outline,
      thumbColor: Colors.white,
      overlayColor: AppColors.teal.withValues(alpha: 0.15),
      showValueIndicator: ShowValueIndicator.onDrag,
    ),
    snackBarTheme: const SnackBarThemeData(
      behavior: SnackBarBehavior.floating,
      backgroundColor: AppColors.surfaceHigh,
      contentTextStyle: TextStyle(color: Colors.white),
    ),
    chipTheme: base.chipTheme.copyWith(
      backgroundColor: AppColors.surfaceHigh,
      side: const BorderSide(color: AppColors.outline),
      shape: const StadiumBorder(),
    ),
    textTheme: base.textTheme.apply(
      bodyColor: Colors.white,
      displayColor: Colors.white,
    ),
  );
}
