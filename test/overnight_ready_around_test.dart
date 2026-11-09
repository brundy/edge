// A held night says WHEN it settles, so the post-wake hour does not read as a
// stuck sync. Past that time, or with no wake to anchor on, the plain
// sentence stays.

import 'package:flutter_test/flutter_test.dart';
import 'package:openstrap_edge/ui2/screens/home_screen.dart';

Map<String, dynamic> _today({required String state, int? readyAt}) => {
      'status': {
        'overnight_day': '2026-11-08',
        'overnight_state': state,
        'overnight_ready_at': readyAt,
        'showing_prior_overnight': true,
      },
    };

void main() {
  final nowSec = DateTime.now().millisecondsSinceEpoch ~/ 1000;

  String? note(Map<String, dynamic> t) => staleOvernightNote(t);

  test('a held night names its ready time', () {
    final readyAt = nowSec + 20 * 60;
    final n = note(_today(state: 'building', readyAt: readyAt));
    expect(n, contains('Recovery should be ready around'));
    expect(n, contains(clockOfTs(readyAt)));
  });

  test('past the ready time it falls back to the plain sentence', () {
    final n = note(_today(state: 'building', readyAt: nowSec - 60));
    expect(n, 'Last night is still being worked out.');
  });

  test('no ready time keeps the plain sentence', () {
    final n = note(_today(state: 'building'));
    expect(n, 'Last night is still being worked out.');
  });
}
