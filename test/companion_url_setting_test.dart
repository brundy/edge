// The upload server row: where the daily database upload goes.
//
// The address carries the server's secret path, so the row shows the host
// and nothing after it. And it only exists beside the upload toggle it serves.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:openstrap_edge/ui2/profile/settings.dart';
import 'package:openstrap_edge/ui2/ui2.dart';

Widget _wrap(Widget child) =>
    MaterialApp(theme: buildTheme(Brightness.light), home: child);

void _tallPhone(WidgetTester tester) {
  tester.view.physicalSize = const Size(390 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
}

void main() {
  testWidgets('shows the host, never the secret path', (tester) async {
    _tallPhone(tester);
    var tapped = false;
    await tester.pumpWidget(
      _wrap(
        MoreSettingsView(
          showHealthShare: true,
          companionUrl: 'https://pi.tail0000.ts.net/s3cret-token-value',
          onEditCompanionUrl: () => tapped = true,
        ),
      ),
    );
    expect(find.text('Upload server'), findsOneWidget);
    expect(find.text('pi.tail0000.ts.net'), findsOneWidget);
    expect(find.textContaining('s3cret'), findsNothing);
    await tester.tap(find.text('Upload server'));
    expect(tapped, isTrue);
  });

  testWidgets('says so when unset', (tester) async {
    _tallPhone(tester);
    await tester.pumpWidget(
      _wrap(const MoreSettingsView(showHealthShare: true)),
    );
    expect(find.text('Not set, so nothing is uploaded'), findsOneWidget);
  });

  testWidgets('absent without the upload feature', (tester) async {
    _tallPhone(tester);
    await tester.pumpWidget(_wrap(const MoreSettingsView()));
    expect(find.text('Upload server'), findsNothing);
  });
}
