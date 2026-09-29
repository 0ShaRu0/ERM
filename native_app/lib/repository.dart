import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart' as ffi;
import 'package:uuid/uuid.dart';

typedef Json = Map<String, dynamic>;

class Repository extends ChangeNotifier {
  static const uuid = Uuid();
  static const vault = FlutterSecureStorage();
  late Database database;
  late String directory;
  Json connection = {};
  Json state = {'records': <dynamic>[], 'pending': <dynamic>[], 'conflicts': <dynamic>[]};
  List<String> addresses = [];
  Process? service;
  Timer? timer;
  Future<void> _tail = Future.value();
  bool syncing = false;
  String status = '연결 안 됨 · 로컬 사용';
  String serviceError = '';
  String? get workspace => state['workspace'] as String?;
  bool get isHub => Platform.isWindows;
  List<Json> get pending => (state['pending'] as List).cast<Json>();
  List<Json> get conflicts => (state['conflicts'] as List).cast<Json>();
  String get lastSync => state['last_sync'] as String? ?? '아직 없음';

  Future<T> locked<T>(Future<T> Function() action) {
    final result = _tail.then((_) => action());
    _tail = result.then<void>((_) {}, onError: (Object _, StackTrace __) {});
    return result;
  }

  Future<void> initialize() async {
    directory = p.join((await getApplicationSupportDirectory()).path, 'ERM');
    await Directory(directory).create(recursive: true);
    if (Platform.isWindows || Platform.isLinux) {
      ffi.sqfliteFfiInit();
      databaseFactory = ffi.databaseFactoryFfi;
    }
    database = await openDatabase(p.join(directory, 'client.db'), version: 1,
        onCreate: (db, _) => db.execute('CREATE TABLE app_state(id INTEGER PRIMARY KEY, value TEXT NOT NULL)'));
    final rows = await database.query('app_state');
    if (rows.isNotEmpty) state = jsonDecode(rows.first['value'] as String) as Json;
    final saved = await vault.read(key: 'erm-connection');
    if (saved != null) connection = jsonDecode(saved) as Json;
    if (isHub) {
      try {
        await startHub();
      } catch (e) {
        serviceError = '$e';
        status = '동기화 서비스 시작 실패 · 로컬 사용';
      }
    }
    await sync();
    timer = Timer.periodic(const Duration(seconds: 20), (_) => unawaited(sync()));
  }

  Future<void> startHub() async {
    final executable = p.join(p.dirname(Platform.resolvedExecutable), 'erm_hub.exe');
    const python = String.fromEnvironment('ERM_PYTHON', defaultValue: 'python');
    const script = String.fromEnvironment('ERM_SERVICE_SCRIPT');
    service = await Process.start(script.isEmpty ? executable : python,
        [if (script.isNotEmpty) script, '--data-dir', p.join(directory, 'hub')]);
    final ready = Completer<Json>();
    service!.stdout.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
      if (!ready.isCompleted) {
        try {
          ready.complete(jsonDecode(line) as Json);
        } catch (e) {
          ready.completeError(Exception('서비스 응답 오류: $e'));
        }
      }
    });
    final errors = StringBuffer();
    service!.stderr.transform(utf8.decoder).listen(errors.write);
    unawaited(service!.exitCode.then((code) {
      if (!ready.isCompleted) ready.completeError(Exception('PC 서비스 종료 ($code): $errors'));
    }));
    final info = await ready.future.timeout(const Duration(seconds: 30));
    if (workspace != null && workspace != info['workspace']) {
      throw Exception('기준 DB가 변경되었습니다. 기존 로컬 기록을 보존하고 서비스를 확인하세요.');
    }
    addresses = (info['addresses'] as List).cast<String>();
    connection = info;
    await vault.write(key: 'erm-connection', value: jsonEncode(connection));
    state['workspace'] = info['workspace'];
    await persist();
  }

  Future<Json> request(String route, {Json? body, String method = 'POST', Json? credentials}) async {
    final config = credentials ?? connection;
    final uri = Uri.parse('${config['url']}$route');
    if (uri.scheme != 'https' || uri.host.isEmpty) throw Exception('https://PC주소:8766 형식으로 입력하세요.');
    final fingerprint = (config['fingerprint'] as String? ?? '').replaceAll(':', '').toLowerCase();
    if (!RegExp(r'^[a-f0-9]{64}$').hasMatch(fingerprint)) throw Exception('인증서 지문이 올바르지 않습니다.');
    // Empty trust roots ensure even CA-signed certificates must match the paired fingerprint.
    final client = HttpClient(context: SecurityContext(withTrustedRoots: false))
      ..connectionTimeout = const Duration(seconds: 8)
      ..badCertificateCallback = (cert, host, port) =>
          host == uri.host && port == uri.port && sha256.convert(cert.der).toString() == fingerprint;
    try {
      final req = await client.openUrl(method, uri).timeout(const Duration(seconds: 10));
      req.followRedirects = false;
      req.headers.contentType = ContentType.json;
      if (config['token'] != null) req.headers.set('Authorization', 'Bearer ${config['token']}');
      if (body != null) req.write(jsonEncode(body));
      final response = await req.close().timeout(const Duration(seconds: 30));
      final text = await response.transform(utf8.decoder).join().timeout(const Duration(seconds: 60));
      final decoded = jsonDecode(text) as Json;
      if (response.statusCode >= 300) {
        throw Exception(decoded['message'] ?? '서버 오류 ${response.statusCode}');
      }
      return decoded;
    } finally {
      client.close(force: true);
    }
  }

  Future<void> pair(String url, String fingerprint, String code, String name) => locked(() async {
    final config = <String, dynamic>{'url': url.trim().replaceFirst(RegExp(r'/+$'), ''),
      'fingerprint': fingerprint.trim()};
    final result = await request('/v2/pair', credentials: config, body: {
      'code': code.trim().toUpperCase(), 'name': name,
      if (state['device_id'] != null) 'device_id': state['device_id'],
    });
    if (workspace != null && workspace != result['workspace']) {
      throw Exception('다른 작업 공간입니다. 현재 데이터는 유지됩니다. 같은 PC의 연결 코드를 사용하세요.');
    }
    connection = {...config, ...result};
    await vault.write(key: 'erm-connection', value: jsonEncode(connection));
    state['workspace'] = result['workspace'];
    state['device_id'] = result['device_id'];
    await persist();
    notifyListeners();
  });

  Future<void> persist() => database.transaction((txn) async {
    await txn.insert('app_state', {'id': 1, 'value': jsonEncode(state)}, conflictAlgorithm: ConflictAlgorithm.replace);
  });

  List<Json> get projected {
    final records = <String, Json>{};
    for (final raw in state['records'] as List) {
      final record = Map<String, dynamic>.from(raw as Map);
      records[record['id'] as String] = record;
    }
    for (final op in pending) {
      final id = op['entity_id'] as String;
      final old = records[id];
      records[id] = {'id': id, 'kind': op['kind'], 'revision': (old?['revision'] as int? ?? 0) + 1,
        'deleted': op['action'] == 'delete' ? 1 : 0, 'pending': true,
        'data': op['action'] == 'return' ? {...?(old?['data'] as Json?), ...(op['data'] as Json)}
          : op['data']};
    }
    return records.values.toList();
  }

  List<Json> records(String kind) => projected.where((r) => r['kind'] == kind && r['deleted'] == 0).toList();
  Json? byId(String id) {
    for (final r in projected) {
      if (r['id'] == id && r['deleted'] == 0) return r;
    }
    return null;
  }

  int revisionOf(String id) {
    for (final r in projected) {
      if (r['id'] == id) return r['revision'] as int;
    }
    return 0;
  }

  int available(Json equipment) => (equipment['data']['quantity'] as int) - records('rental').where(
      (r) => r['data']['equipment_id'] == equipment['id'] && (r['data']['return_date'] ?? '') == '').length;

  Future<void> queue(String kind, String id, Json data, {String action = 'put', int? baseRevision}) => locked(() async {
    if (workspace == null) throw Exception('처음 한 번 PC와 연결해 작업 공간을 등록하세요.');
    final previous = jsonEncode(state);
    try {
      pending.add({'id': uuid.v4(), 'entity_id': id, 'kind': kind, 'action': action,
        'base_revision': baseRevision ?? revisionOf(id), 'data': data});
      await persist();
    } catch (_) {
      state = jsonDecode(previous) as Json;
      rethrow;
    }
    notifyListeners();
    unawaited(sync());
  });

  Future<void> sync() async {
    if (syncing || connection['token'] == null) return;
    syncing = true;
    notifyListeners();
    try {
      await locked(() async {
        do {
          final batch = pending.take(10).toList();
          final result = await request('/v2/sync', body: {'workspace': workspace, 'operations': batch});
          if (result['workspace'] != workspace) throw Exception('작업 공간이 변경되어 동기화를 중지했습니다.');
          final previous = jsonEncode(state);
          try {
            for (final raw in result['results'] as List) {
              final outcome = raw as Json;
              final op = batch.firstWhere((op) => op['id'] == outcome['id']);
              if (outcome['status'] != 'applied') conflicts.add({...op, 'message': outcome['message']});
              pending.removeWhere((item) => item['id'] == op['id']);
            }
            state['records'] = result['records'];
            state['last_sync'] = DateTime.now().toLocal().toString().split('.').first;
            await persist();
          } catch (_) {
            state = jsonDecode(previous) as Json;
            rethrow;
          }
        } while (pending.isNotEmpty);
        status = conflicts.isEmpty ? '동기화 완료' : '충돌 확인 필요';
      });
    } catch (e) {
      status = '동기화 대기: $e';
    } finally {
      syncing = false;
      notifyListeners();
    }
  }

  Future<void> dismissConflict(String id) => locked(() async {
    final previous = jsonEncode(state);
    try {
      conflicts.removeWhere((c) => c['id'] == id);
      await persist();
    } catch (_) {
      state = jsonDecode(previous) as Json;
      rethrow;
    }
    notifyListeners();
  });

  Future<void> importLegacy(String path) => locked(() async {
    if (pending.isNotEmpty || conflicts.isNotEmpty) throw Exception('대기 작업과 충돌을 먼저 처리하세요.');
    await request('/v2/import', body: {'path': path});
  });

  List<Json> monthlyStats(int year) {
    final grouped = <String, Json>{};
    void increment(Json data, String field, String count) {
      if ((data[field] ?? '') == '') return;
      final date = DateTime.parse(data[field] as String).toLocal();
      if (date.year != year) return;
      final month = '$year-${date.month.toString().padLeft(2, '0')}';
      final key = '${data['equipment_id']}:$month';
      final row = grouped.putIfAbsent(key, () => {'equipment_id': data['equipment_id'],
        'equipment_name': data['equipment_name'], 'stat_month': month, 'rent_count': 0, 'return_count': 0});
      row[count] = (row[count] as int) + 1;
    }
    for (final record in records('rental')) {
      final data = record['data'] as Json;
      increment(data, 'rent_date', 'rent_count');
      increment(data, 'return_date', 'return_count');
    }
    for (final record in records('override')) {
      final data = record['data'] as Json;
      if ((data['stat_month'] as String).startsWith('$year-')) {
        grouped['${data['equipment_id']}:${data['stat_month']}'] = {...data, 'adjusted': true};
      }
    }
    return grouped.values.toList()..sort((a, b) => '${a['stat_month']}${a['equipment_name']}'.compareTo('${b['stat_month']}${b['equipment_name']}'));
  }

  Future<void> shutdown() async {
    timer?.cancel();
    await service?.stdin.close();
    await database.close();
  }
}
