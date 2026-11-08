// The sync log's per-pass timing line: what a slow "Crunching last night's
// numbers" is diagnosed from.

import 'package:flutter_test/flutter_test.dart';
import 'package:openstrap_edge/compute/derivation_engine.dart';
import 'package:openstrap_edge/sync/file_log.dart';

void main() {
  test(
    'stage clock adds up a stage entered twice and resets on finish',
    () async {
      final clock = PassStageClock()..start('scope');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      clock.start('per_day');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      clock.start('scope');
      await Future<void>.delayed(const Duration(milliseconds: 20));
      final ms = clock.finish();
      expect(ms.keys, unorderedEquals(['scope', 'per_day']));
      expect(ms['scope']!, greaterThanOrEqualTo(40));
      expect(ms['per_day']!, greaterThanOrEqualTo(20));
      expect(clock.finish(), isEmpty);
    },
  );

  test('pass line names mode, totals and the stages that took time', () {
    final line = formatPassTiming(
      mode: 'heavy',
      totalMs: 412300,
      stagesMs: {'scope': 12, 'per_day': 380000, 'cross_day': 4200},
      days: 3,
      done: 3,
      skipped: 0,
      rawRows: 750000,
      concurrency: 3,
    );
    expect(
      line,
      'pass timing: heavy 412.3s total; days=3 done=3 skipped=0 '
      'rows=750000 lanes=3; per_day=380.0s cross_day=4.2s',
    );
  });

  test('log stamp is local month/day and zero-padded time', () {
    expect(FileLog.stamp(DateTime(2026, 11, 8, 7, 5, 9)), '11/8 07:05:09');
  });
}
