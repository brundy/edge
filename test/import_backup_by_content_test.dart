// A backup that went through a cloud drive can come back without its `.db`
// ("OpenStrap database" is what Google Drive saves an export as). The import
// must still recognise it as this app's own backup by what is in the file,
// and must not claim a NOOP database or a CSV as one.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:path/path.dart' as p;
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:openstrap_edge/data/db.dart';

void main() {
  late Directory dir;

  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('import_by_content_');
  });

  tearDown(() async {
    try {
      await dir.delete(recursive: true);
    } catch (_) {}
  });

  Future<String> sqliteWith(String name, String table) async {
    final path = p.join(dir.path, name);
    final db = await databaseFactory.openDatabase(path);
    await db.execute('CREATE TABLE $table (id INTEGER PRIMARY KEY)');
    await db.close();
    return path;
  }

  test('an OpenStrap database with no extension is ours', () async {
    final path = await sqliteWith('OpenStrap database', 'decoded_onehz');
    expect(await LocalDb.isOpenStrapBackup(path), isTrue);
  });

  test('a gzipped backup with no extension is ours', () async {
    final src = await sqliteWith('src.db', 'decoded_onehz');
    final path = p.join(dir.path, 'OpenStrap backup');
    await File(path).writeAsBytes(gzip.encode(await File(src).readAsBytes()));
    expect(await LocalDb.isOpenStrapBackup(path), isTrue);
  });

  test('a SQLite file without our tables (a NOOP database) is not', () async {
    final path = await sqliteWith('noop-backup.sqlite', 'grdb_migrations');
    expect(await LocalDb.isOpenStrapBackup(path), isFalse);
  });

  test('a CSV and a missing file are not', () async {
    final csv = p.join(dir.path, 'export.csv');
    await File(csv).writeAsString('unix_s,hr\n1700000000,60\n');
    expect(await LocalDb.isOpenStrapBackup(csv), isFalse);
    expect(await LocalDb.isOpenStrapBackup(p.join(dir.path, 'nope')), isFalse);
  });
}
