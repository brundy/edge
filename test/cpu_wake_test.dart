// CpuWake mirrors one busy flag to the native wake lock: only transitions
// reach the platform, and a held lock is re-sent so the native timeout never
// lapses mid-work.

import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:openstrap_edge/sync/cpu_wake.dart';

void main() {
  test('only transitions are sent', () {
    final sent = <bool>[];
    final w = CpuWake(send: (on) async => sent.add(on));
    w.set(false);
    w.set(true);
    w.set(true);
    w.set(false);
    w.set(false);
    expect(sent, [true, false]);
    expect(w.held, isFalse);
  });

  test('a held lock is refreshed inside the native timeout', () {
    fakeAsync((clock) {
      final sent = <bool>[];
      final w = CpuWake(
        send: (on) async => sent.add(on),
        refreshEvery: const Duration(minutes: 10),
      );
      w.set(true);
      clock.elapse(const Duration(minutes: 25));
      expect(sent, [true, true, true]);
      w.set(false);
      clock.elapse(const Duration(minutes: 30));
      expect(sent, [true, true, true, false]);
    });
  });

  test('dispose releases', () {
    final sent = <bool>[];
    final w = CpuWake(send: (on) async => sent.add(on));
    w.set(true);
    w.dispose();
    expect(sent, [true, false]);
  });
}
