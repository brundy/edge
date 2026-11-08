// An engine built while the app was headless must still take the foreground
// lane count once the app is on screen. AppState builds its engine once per
// process, and on Android the foreground service keeps that process alive, so
// a process that started headless used to derive on ONE lane forever.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:openstrap_edge/compute/derivation_engine.dart';
import 'package:openstrap_edge/compute/derive_pacing.dart';
import 'package:openstrap_edge/compute/profile.dart';
import 'package:openstrap_edge/data/db.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    LocalDb.dbName = 'openstrap_live_lanes_test.db';
    final dir = await databaseFactory.getDatabasesPath();
    await databaseFactory.deleteDatabase(p.join(dir, LocalDb.dbName));
  });

  tearDownAll(() async {
    await LocalDb.close();
    final dir = await databaseFactory.getDatabasesPath();
    await databaseFactory.deleteDatabase(p.join(dir, LocalDb.dbName));
  });

  final foregroundLanes = const DerivePacing(
    background: false,
  ).concurrency(Platform.numberOfProcessors);

  Future<int> lanesFor(DerivationEngine engine) async {
    await engine.run(const Profile());
    return engine.snapshot()['concurrency'] as int;
  }

  test(
    'headless-built engine takes foreground lanes while on screen',
    () async {
      final lanes = await lanesFor(
        DerivationEngine(background: true, foregroundNow: () => true),
      );
      expect(lanes, foregroundLanes);
    },
  );

  test('headless-built engine stays on one lane while off screen', () async {
    final lanes = await lanesFor(
      DerivationEngine(background: true, foregroundNow: () => false),
    );
    expect(lanes, 1);
  });

  test('a headless entry with no live signal keeps one lane', () async {
    expect(await lanesFor(DerivationEngine(background: true)), 1);
  });
}
