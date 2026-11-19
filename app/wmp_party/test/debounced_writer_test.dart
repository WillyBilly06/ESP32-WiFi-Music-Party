import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:wmp_party/protocol/debounced_writer.dart';

/// A fake slow characteristic: each GATT op takes [opTime].
class _SlowChar {
  _SlowChar(this.opTime);
  final Duration opTime;
  int value = 0;
  final writes = <int>[];
  int reads = 0;
  bool failNextWrite = false;
  int? clampTo;

  Future<void> write(int v) async {
    await Future<void>.delayed(opTime);
    if (failNextWrite) {
      failNextWrite = false;
      throw Exception('ATT 0x13');
    }
    writes.add(v);
    value = clampTo != null && v > clampTo! ? clampTo! : v;
  }

  Future<int> read() async {
    await Future<void>.delayed(opTime);
    reads++;
    return value;
  }
}

void main() {
  test('slider drag collapses to one write plus one read back', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 800));
      final results = <WriteResult<int>>[];
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: results.add,
      );
      for (var v = 0; v <= 50; v += 5) {
        w.set(v);
        async.elapse(const Duration(milliseconds: 50));
      }
      expect(w.busy, isTrue);
      async.elapse(const Duration(seconds: 3));
      expect(c.writes, [50]);
      expect(c.reads, 1);
      expect(results.single.confirmed, isTrue);
      expect(results.single.actual, 50);
      expect(w.busy, isFalse);
    });
  });

  test('value changed during an in-flight write is written next', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 800));
      final results = <WriteResult<int>>[];
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: results.add,
      );
      w.set(10);
      async.elapse(const Duration(milliseconds: 500)); // write of 10 in flight
      w.set(20);
      w.set(30);
      async.elapse(const Duration(seconds: 5));
      expect(c.writes, [10, 30]);
      expect(results.length, 1);
      expect(results.single.requested, 30);
      expect(results.single.actual, 30);
    });
  });

  test('setNow skips the debounce', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 100));
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: (_) {},
      );
      w.setNow(1);
      async.elapse(const Duration(milliseconds: 150));
      expect(c.writes, [1]);
      async.elapse(const Duration(seconds: 1));
    });
  });

  test('rejected write reports the error and the read-back value', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 100))..value = 7;
      final results = <WriteResult<int>>[];
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: results.add,
      );
      c.failNextWrite = true;
      w.set(99);
      async.elapse(const Duration(seconds: 2));
      expect(results.single.confirmed, isFalse);
      expect(results.single.error, isNotNull);
      expect(results.single.actual, 7);
    });
  });

  test('speaker applying a different value is visible in the result', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 100))..clampTo = 40;
      final results = <WriteResult<int>>[];
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: results.add,
      );
      w.set(80);
      async.elapse(const Duration(seconds: 2));
      expect(results.single.requested, 80);
      expect(results.single.actual, 40);
    });
  });

  test('dispose drops pending values', () {
    fakeAsync((async) {
      final c = _SlowChar(const Duration(milliseconds: 100));
      final w = DebouncedWriter<int>(
        write: c.write,
        readBack: c.read,
        onSettled: (_) => fail('should not settle'),
      );
      w.set(5);
      w.dispose();
      async.elapse(const Duration(seconds: 2));
      expect(c.writes, isEmpty);
    });
  });
}
