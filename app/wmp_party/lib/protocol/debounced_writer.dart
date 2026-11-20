/// Debounced, coalescing write-then-read-back helper.
///
/// Every GATT write takes radio time the phone also needs for its A2DP
/// stream (about 100-150 ms per round trip at the speaker's 60-75 ms
/// connection interval), so slider drags must not queue dozens of writes. [DebouncedWriter] keeps
/// only the latest requested value, writes it after [debounce] of quiet, never
/// has more than one write in flight, and reads the characteristic back after
/// the last write to confirm what the speaker actually applied.
///
/// Pure Dart (dart:async only).
library;

import 'dart:async';

typedef WriteFn<T> = Future<void> Function(T value);
typedef ReadFn<T> = Future<T> Function();
typedef EqualsFn<T> = bool Function(T a, T b);

/// Outcome of one settled write cycle.
class WriteResult<T> {
  const WriteResult({
    required this.requested,
    required this.actual,
    this.error,
  });

  final T requested;

  /// Read-back value, or null if the read back also failed.
  final T? actual;
  final Object? error;

  bool get confirmed => error == null && actual != null;
}

class DebouncedWriter<T> {
  DebouncedWriter({
    required this.write,
    required this.readBack,
    required this.onSettled,
    this.debounce = const Duration(milliseconds: 250),
    EqualsFn<T>? equals,
  }) : equals = equals ?? ((a, b) => a == b);

  final WriteFn<T> write;
  final ReadFn<T> readBack;
  final void Function(WriteResult<T> result) onSettled;
  final Duration debounce;
  final EqualsFn<T> equals;

  Timer? _timer;
  T? _pending;
  bool _hasPending = false;
  bool _inFlight = false;
  bool _disposed = false;
  int _writes = 0;

  /// True while a value is waiting for its debounce or being written/confirmed.
  /// The UI uses this to ignore stale values from status notifications.
  bool get busy => _hasPending || _inFlight || (_timer?.isActive ?? false);

  /// A value is waiting for its debounce (not yet written).
  bool get waiting => _timer?.isActive ?? false;

  /// Number of writes actually sent (for tests/diagnostics).
  int get writeCount => _writes;

  /// Requests [value]; it is written once the user stops changing it.
  void set(T value) {
    if (_disposed) return;
    _pending = value;
    _hasPending = true;
    _timer?.cancel();
    _timer = Timer(debounce, _kick);
  }

  /// Requests [value] and writes it as soon as the link is free.
  void setNow(T value) {
    if (_disposed) return;
    _pending = value;
    _hasPending = true;
    _timer?.cancel();
    _kick();
  }

  void _kick() {
    if (_inFlight || _disposed || !_hasPending) return;
    unawaited(_run());
  }

  Future<void> _run() async {
    _inFlight = true;
    T requested = _pending as T;
    Object? error;
    try {
      // Write the latest value; if the user moved again while we were
      // writing, write the newer value before reading back.
      while (_hasPending && !_disposed) {
        if (_timer?.isActive ?? false) {
          // a newer value is still debouncing: let its timer restart us.
          _inFlight = false;
          return;
        }
        requested = _pending as T;
        _hasPending = false;
        _writes++;
        await write(requested);
      }
    } catch (e) {
      error = e;
      _hasPending = false;
    }
    if (_disposed) {
      _inFlight = false;
      return;
    }
    T? actual;
    try {
      actual = await readBack();
    } catch (e) {
      error ??= e;
    }
    _inFlight = false;
    if (_disposed) return;
    if (_hasPending) {
      // A newer value arrived during the read back; it wins.
      if (!(_timer?.isActive ?? false)) _kick();
      return;
    }
    onSettled(WriteResult<T>(requested: requested, actual: actual, error: error));
  }

  /// Drops any pending value (e.g. on disconnect).
  void cancel() {
    _timer?.cancel();
    _hasPending = false;
  }

  void dispose() {
    _disposed = true;
    cancel();
  }
}
