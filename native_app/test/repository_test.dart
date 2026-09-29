import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart' as ffi;

import 'package:erm_native/repository.dart';

Future<Repository> localRepo(String workspace) async {
  final repo = Repository();
  repo.database = await databaseFactory.openDatabase(inMemoryDatabasePath,
    options: OpenDatabaseOptions(singleInstance: false, version: 1,
      onCreate: (db, _) => db.execute('CREATE TABLE app_state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)')));
  repo.state['workspace'] = workspace;
  return repo;
}

void main() {
  ffi.sqfliteFfiInit();
  databaseFactory = ffi.databaseFactoryFfi;

  test('offline operations survive reload and preserve dependent revisions', () async {
    final repo = await localRepo(Repository.uuid.v4());
    addTearDown(repo.database.close);
    final equipment = Repository.uuid.v4();
    await repo.queue('equipment', equipment, {'name': '카메라', 'category': '영상', 'quantity': 2});
    await repo.queue('equipment', equipment, {'name': '수정한 카메라', 'category': '영상', 'quantity': 3});
    final persisted = await repo.database.query('app_state');
    final reloaded = Repository()..state = jsonDecode(persisted.single['value'] as String) as Json;
    expect(reloaded.pending.length, 2);
    expect(reloaded.pending.map((o) => o['base_revision']), [0, 1]);
    expect(reloaded.records('equipment').single['data']['quantity'], 3);
    expect(reloaded.byId(equipment)!['pending'], true);
  });

  test('offline rental and return affect availability and month-specific statistics', () async {
    final repo = await localRepo(Repository.uuid.v4());
    addTearDown(repo.database.close);
    final equipment = Repository.uuid.v4();
    final rental = Repository.uuid.v4();
    await repo.queue('equipment', equipment, {'name': '카메라', 'category': '영상', 'quantity': 1});
    await repo.queue('rental', rental, {'equipment_id': equipment, 'equipment_name': '카메라',
      'rent_date': '2026-08-15T12:00:00Z', 'return_date': ''});
    expect(repo.available(repo.byId(equipment)!), 0);
    await repo.queue('rental', rental, {'return_date': '2026-09-15T12:00:00Z'}, action: 'return');
    expect(repo.available(repo.byId(equipment)!), 1);
    final stats = repo.monthlyStats(2026);
    expect(stats.map((r) => r['rent_count']), [1, 0]);
    expect(stats.map((r) => r['return_count']), [0, 1]);
    final key = Repository.uuid.v5(repo.workspace!, 'override:$equipment:2026-08');
    await repo.queue('override', key, {'equipment_id': equipment, 'equipment_name': '카메라',
      'stat_month': '2026-08', 'rent_count': 8, 'return_count': 0});
    expect(repo.monthlyStats(2026).first['rent_count'], 8);
    await repo.queue('override', key, {}, action: 'delete');
    expect(repo.monthlyStats(2026).first['rent_count'], 1);
    expect(repo.revisionOf(key), 2);
  });

  test('failed local persistence does not create a phantom saved operation', () async {
    final repo = await localRepo(Repository.uuid.v4());
    await repo.database.close();
    await expectLater(repo.queue('equipment', Repository.uuid.v4(), {'name': '미저장'}), throwsA(anything));
    expect(repo.pending, isEmpty);
    expect(repo.records('equipment'), isEmpty);
  });

  test('an open edit form retains its observed revision after a background refresh', () async {
    final repo = await localRepo(Repository.uuid.v4());
    addTearDown(repo.database.close);
    final equipment = Repository.uuid.v4();
    repo.state['records'] = [
      {'id': equipment, 'kind': 'equipment', 'revision': 2, 'deleted': 0,
        'data': {'name': '다른 기기의 수정', 'category': '영상', 'quantity': 2}},
    ];
    await repo.queue('equipment', equipment, {'name': '이전 화면에서 편집', 'category': '영상', 'quantity': 1}, baseRevision: 1);
    expect(repo.pending.single['base_revision'], 1);
  });

  test('real TLS hub: pair, offline queue, restart state, sync, conflict, revoke', () async {
    final directory = await Directory.systemTemp.createTemp('erm-hub-test-');
    final portProbe = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final port = portProbe.port;
    await portProbe.close();
    final hub = await Process.start(Platform.environment['ERM_TEST_PYTHON']!,
      ['../native_service/server.py', '--data-dir', directory.path, '--port', '$port']);
    hub.stderr.transform(utf8.decoder).listen((_) {});
    addTearDown(() async {
      await hub.stdin.close();
      await hub.exitCode.timeout(const Duration(seconds: 10));
      await directory.delete(recursive: true);
    });
    final info = jsonDecode(await hub.stdout.transform(utf8.decoder).transform(const LineSplitter()).first) as Json;
    final pc = await localRepo(info['workspace'] as String)..connection = info;
    addTearDown(pc.database.close);
    final pairCode = await pc.request('/v2/pairing');
    final phoneCredentials = await pc.request('/v2/pair',
      body: {'code': pairCode['code'], 'name': '테스트 Android'});
    final phone = await localRepo(info['workspace'] as String);
    addTearDown(phone.database.close);
    final equipment = Repository.uuid.v4();
    final renter = Repository.uuid.v4();
    await phone.queue('equipment', equipment, {'name': '오프라인 장비', 'category': '영상', 'quantity': 1});
    await phone.queue('renter', renter, {'name': '사용자', 'user_id': 'u1'});
    // Discard in-memory state, exactly as after terminating the app offline.
    phone.state = jsonDecode((await phone.database.query('app_state')).single['value'] as String) as Json;
    phone.connection = {...info, ...phoneCredentials};
    await phone.sync();
    expect(phone.pending, isEmpty);
    expect(phone.conflicts, isEmpty);
    await pc.sync();
    expect(pc.records('equipment').single['data']['name'], '오프라인 장비');
    // Work independently using the same base revision.
    phone.connection = {};
    pc.connection = {};
    await phone.queue('equipment', equipment, {'name': '폰 변경', 'category': '영상', 'quantity': 1});
    await pc.queue('equipment', equipment, {'name': 'PC 변경', 'category': '영상', 'quantity': 1});
    pc.connection = info;
    await pc.sync();
    phone.connection = {...info, ...phoneCredentials};
    await phone.sync();
    expect(phone.conflicts.single['data']['name'], '폰 변경');
    expect(phone.records('equipment').single['data']['name'], 'PC 변경');
    // Exact certificate pin is required.
    await expectLater(pc.request('/v2/devices', method: 'GET',
      credentials: {...info, 'fingerprint': List.filled(64, '0').join()}), throwsA(anything));
    await pc.request('/v2/devices/${phoneCredentials['device_id']}', method: 'DELETE');
    await phone.sync();
    expect(phone.status, contains('인증'));
  }, skip: Platform.environment['ERM_TEST_PYTHON'] == null,
     timeout: const Timeout(Duration(seconds: 90)));
}
