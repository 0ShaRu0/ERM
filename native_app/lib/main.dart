import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:qr_flutter/qr_flutter.dart';

import 'exports.dart';
import 'repository.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ErmApp());
}

class ErmApp extends StatelessWidget {
  const ErmApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'ERM 장비대여관리', debugShowCheckedModeBanner: false,
    locale: const Locale('ko'), supportedLocales: const [Locale('ko')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: ThemeData(colorSchemeSeed: const Color(0xff386556), useMaterial3: true,
      inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder())),
    home: const Home(),
  );
}

class Home extends StatefulWidget {
  const Home({super.key, this.repository});
  final Repository? repository;
  @override
  State<Home> createState() => _HomeState();
}

class _HomeState extends State<Home> with WidgetsBindingObserver {
  late final repo = widget.repository ?? Repository();
  bool ready = false;
  String? fatal;
  int tab = 0;
  int year = DateTime.now().year;
  String filter = '';
  final labels = ['장비', '대여자', '대여·반납', '통계', '연결'];
  final icons = [Icons.inventory_2_outlined, Icons.people_outline, Icons.swap_horiz, Icons.bar_chart, Icons.sync];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    repo.addListener(refresh);
    unawaited(initialize());
  }

  Future<void> initialize() async {
    try {
      await repo.initialize();
      if (mounted) setState(() => ready = true);
    } catch (e) {
      if (mounted) setState(() => fatal = '$e');
    }
  }

  void refresh() { if (mounted) setState(() {}); }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (ready && state == AppLifecycleState.resumed) unawaited(repo.sync());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    repo.removeListener(refresh);
    if (ready) unawaited(repo.shutdown());
    super.dispose();
  }

  Future<void> run(Future<void> Function() action) async {
    try {
      await action();
    } catch (e) {
      if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e'), duration: const Duration(seconds: 6)));
    }
  }

  Future<bool> confirm(String text) async => await showDialog<bool>(context: context, builder: (c) => AlertDialog(
    title: const Text('확인'), content: Text(text), actions: [
      TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('취소')),
      FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('확인')),
    ])) ?? false;

  @override
  Widget build(BuildContext context) {
    if (fatal != null) return Scaffold(body: Center(child: SelectableText('앱 초기화 실패\n$fatal')));
    if (!ready) return const Scaffold(body: Center(child: CircularProgressIndicator()));
    final wide = MediaQuery.sizeOf(context).width >= 850;
    final pages = [equipmentPage, renterPage, rentalPage, statsPage, connectionPage];
    return Scaffold(
      appBar: AppBar(title: Text('ERM · ${labels[tab]}'), actions: [
        if (repo.syncing) const Padding(padding: EdgeInsets.all(16), child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)))
        else IconButton(onPressed: () => run(repo.sync), tooltip: '지금 동기화', icon: const Icon(Icons.sync)),
      ]),
      body: Row(children: [
        if (wide) NavigationRail(extended: true, selectedIndex: tab, onDestinationSelected: (v) => setState(() { tab = v; filter = ''; }),
          destinations: List.generate(labels.length, (i) => NavigationRailDestination(icon: Icon(icons[i]), label: Text(labels[i])))),
        Expanded(child: Column(children: [
          Container(width: double.infinity, padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            color: Theme.of(context).colorScheme.surfaceContainerHighest,
            child: Text('${repo.syncing ? '동기화 중' : repo.status}\n전송 대기 ${repo.pending.length} · 충돌 ${repo.conflicts.length} · 마지막 동기화 ${repo.lastSync}',
              maxLines: 3, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 12))),
          Expanded(child: pages[tab]()),
        ])),
      ]),
      bottomNavigationBar: wide ? null : NavigationBar(selectedIndex: tab,
        onDestinationSelected: (v) => setState(() { tab = v; filter = ''; }),
        destinations: List.generate(labels.length, (i) => NavigationDestination(icon: Icon(icons[i]), label: labels[i]))),
    );
  }

  Widget listPage(String addLabel, VoidCallback onAdd, List<Widget> cards) => Column(children: [
    Padding(padding: const EdgeInsets.all(12), child: Row(children: [
      Expanded(child: TextField(key: ValueKey(tab), decoration: const InputDecoration(labelText: '검색', prefixIcon: Icon(Icons.search)),
        onChanged: (v) => setState(() => filter = v))),
      const SizedBox(width: 8), FilledButton(onPressed: onAdd, child: Text(addLabel)),
    ])),
    Expanded(child: cards.isEmpty ? const Center(child: Text('표시할 기록이 없습니다.')) : ListView(padding: const EdgeInsets.all(12), children: cards)),
  ]);

  bool matches(Json r) => jsonEncode(r['data']).toLowerCase().contains(filter.toLowerCase());
  String pendingLabel(Json r) => r['pending'] == true ? ' · 동기화 대기' : '';

  Widget equipmentPage() => listPage('장비 등록', () => run(() async { await editEntity('equipment'); }),
    repo.records('equipment').where(matches).map((r) {
      final d = r['data'] as Json;
      Widget icon = const Icon(Icons.inventory_2);
      if ((d['photo'] ?? '') != '') {
        try {
          icon = Image.memory(base64Decode(d['photo'] as String), width: 48, height: 48, fit: BoxFit.cover,
            errorBuilder: (_, __, ___) => const Icon(Icons.broken_image));
        } catch (_) { /* Older invalid photos can still be edited. */ }
      }
      return Card(child: ListTile(leading: icon, title: Text('${d['name']}${pendingLabel(r)}'),
        subtitle: Text('${d['category']} · 전체 ${d['quantity']} / 가능 ${repo.available(r)}'),
        onTap: () => run(() async { await editEntity('equipment', record: r); }),
        trailing: IconButton(icon: const Icon(Icons.delete_outline), onPressed: () => run(() => remove(r)))));
    }).toList());

  Widget renterPage() => listPage('대여자 등록', () => run(() async { await editEntity('renter'); }),
    repo.records('renter').where(matches).map((r) {
      final d = r['data'] as Json;
      return Card(child: ListTile(title: Text('${d['name']}${pendingLabel(r)}'), subtitle: Text('${d['user_id']} · ${d['phone'] ?? ''}'),
        onTap: () => run(() async { await editEntity('renter', record: r); }),
        trailing: IconButton(icon: const Icon(Icons.delete_outline), onPressed: () => run(() => remove(r)))));
    }).toList());

  Future<void> remove(Json record) async {
    if (await confirm('이 항목을 삭제할까요? 대여 중인 항목은 동기화 시 삭제가 거부될 수 있습니다.')) {
      await repo.queue(record['kind'] as String, record['id'] as String, record['data'] as Json,
        action: 'delete', baseRevision: record['revision'] as int);
    }
  }

  Future<bool> editEntity(String kind, {Json? record}) async {
    final equipment = kind == 'equipment';
    final data = record?['data'] as Json? ?? {};
    final name = TextEditingController(text: data['name'] as String? ?? '');
    final secondary = TextEditingController(text: data[equipment ? 'category' : 'user_id'] as String? ?? '');
    final extra = TextEditingController(text: equipment ? '${data['quantity'] ?? 1}' : data['phone'] as String? ?? '');
    String photo = data['photo'] as String? ?? '';
    final form = GlobalKey<FormState>();
    final value = await showDialog<Json>(context: context, builder: (c) => StatefulBuilder(builder: (c, update) => AlertDialog(
      title: Text('${equipment ? '장비' : '대여자'} ${record == null ? '등록' : '수정'}'),
      content: SizedBox(width: 440, child: Form(key: form, child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        field(name, '이름', required: true),
        field(secondary, equipment ? '분류' : '대여자 아이디', required: true),
        Padding(padding: const EdgeInsets.only(bottom: 12), child: TextFormField(controller: extra,
          keyboardType: equipment ? TextInputType.number : TextInputType.phone,
          decoration: InputDecoration(labelText: equipment ? '전체 수량' : '전화번호'),
          validator: (v) => equipment && ((int.tryParse(v ?? '') ?? 0) < 1 || (int.tryParse(v ?? '') ?? 0) > 9999)
              ? '1~9999 사이 정수로 입력하세요.' : null)),
        if (equipment) Wrap(spacing: 8, children: [
          OutlinedButton.icon(icon: const Icon(Icons.photo), label: Text(photo.isEmpty ? '사진 선택 (3MB 이하)' : '사진 변경'), onPressed: () => run(() async {
            final files = await FilePicker.platform.pickFiles(type: FileType.image, withData: true);
            if (files == null) return;
            final bytes = files.files.single.bytes;
            if (bytes == null || bytes.length > 3 * 1024 * 1024) throw Exception('3MB 이하 사진을 선택하세요.');
            if (c.mounted) update(() => photo = base64Encode(bytes));
          })),
          if (photo.isNotEmpty) TextButton(onPressed: () => update(() => photo = ''), child: const Text('사진 제거')),
        ]),
      ])))),
      actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('취소')),
        FilledButton(onPressed: () {
          if (!form.currentState!.validate()) return;
          Navigator.pop(c, <String, dynamic>{...data, 'name': name.text.trim(),
            if (equipment) ...{'category': secondary.text.trim(), 'quantity': int.parse(extra.text), 'photo': photo}
            else ...{'user_id': secondary.text.trim(), 'phone': extra.text.trim()}});
        }, child: const Text('기기에 저장'))],
    )));
    if (value == null) return false;
    if (equipment && record != null) {
      final active = (data['quantity'] as int) - repo.available(record);
      if ((value['quantity'] as int) < active) throw Exception('대여 중인 수량보다 적게 변경할 수 없습니다.');
    }
    await repo.queue(kind, record?['id'] as String? ?? Repository.uuid.v4(), value,
      baseRevision: record?['revision'] as int? ?? 0);
    return true;
  }

  Widget field(TextEditingController controller, String label, {bool required = false}) => Padding(
    padding: const EdgeInsets.only(bottom: 12), child: TextFormField(controller: controller,
      decoration: InputDecoration(labelText: label),
      validator: (v) => required && (v == null || v.trim().isEmpty) ? '입력해 주세요.' : null));

  Widget rentalPage() {
    final rentals = repo.records('rental').where(matches).toList()
      ..sort((a, b) => '${b['data']['rent_date']}'.compareTo('${a['data']['rent_date']}'));
    return listPage('대여 등록', () => run(() async { await rent(); }), rentals.map((r) {
      final d = r['data'] as Json;
      final active = (d['return_date'] ?? '') == '';
      return Card(child: ListTile(isThreeLine: true,
        leading: Icon(active ? Icons.outbox : Icons.assignment_turned_in_outlined),
        title: Text('${d['equipment_name']}${pendingLabel(r)}'),
        subtitle: Text('${d['renter_name']} (${d['user_id']})\n대여 ${dateLabel(d['rent_date'])}'
          '${active ? ' · 예정 ${d['due_date'] ?? ''}' : '\n반납 ${dateLabel(d['return_date'])}'}'),
        trailing: active ? TextButton(onPressed: () => run(() async {
          if (await confirm('${d['equipment_name']}을 반납 처리할까요?')) {
            await repo.queue('rental', r['id'] as String, {'return_date': DateTime.now().toUtc().toIso8601String()}, action: 'return');
          }
        }), child: const Text('반납')) : const Text('완료')));
    }).toList());
  }

  String dateLabel(dynamic value) => DateTime.tryParse('$value')?.toLocal().toString().split('.').first ?? '$value';

  Future<bool> rent({Json? initial}) async {
    final equipment = repo.records('equipment').where((r) => repo.available(r) > 0).toList();
    final renters = repo.records('renter');
    if (equipment.isEmpty || renters.isEmpty) throw Exception('대여 가능한 장비와 대여자를 먼저 등록하세요.');
    String selectedEquipment = equipment.any((r) => r['id'] == initial?['equipment_id'])
      ? initial!['equipment_id'] as String : equipment.first['id'] as String;
    String selectedRenter = renters.any((r) => r['id'] == initial?['renter_id'])
      ? initial!['renter_id'] as String : renters.first['id'] as String;
    final due = TextEditingController(text: initial?['due_date'] as String? ?? '');
    final form = GlobalKey<FormState>();
    final accepted = await showDialog<bool>(context: context, builder: (c) => AlertDialog(
      title: const Text('대여 등록'), content: SizedBox(width: 440, child: Form(key: form, child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        DropdownButtonFormField<String>(isExpanded: true, value: selectedEquipment,
          decoration: const InputDecoration(labelText: '장비'), items: equipment.map((r) => DropdownMenuItem(
            value: r['id'] as String, child: Text('${r['data']['name']} (가능 ${repo.available(r)})'))).toList(),
          onChanged: (v) => selectedEquipment = v!),
        const SizedBox(height: 12),
        DropdownButtonFormField<String>(isExpanded: true, value: selectedRenter,
          decoration: const InputDecoration(labelText: '대여자'), items: renters.map((r) => DropdownMenuItem(
            value: r['id'] as String, child: Text('${r['data']['name']} (${r['data']['user_id']})'))).toList(),
          onChanged: (v) => selectedRenter = v!),
        const SizedBox(height: 12),
        TextFormField(controller: due, decoration: const InputDecoration(labelText: '반납 예정일 (선택, YYYY-MM-DD)'),
          validator: (v) => v != null && v.isNotEmpty && (DateTime.tryParse(v) == null ||
            DateTime.parse(v).toIso8601String().substring(0, 10) != v) ? 'YYYY-MM-DD 형식으로 입력하세요.' : null),
        const SizedBox(height: 12), const Text('오프라인 대여는 연결 후 수량 검증을 거쳐 확정됩니다.'),
      ])))), actions: [TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('취소')),
        FilledButton(onPressed: () { if (form.currentState!.validate()) Navigator.pop(c, true); }, child: const Text('대여 저장'))],
    )) ?? false;
    if (!accepted) return false;
    final eq = repo.byId(selectedEquipment)!['data'] as Json;
    final renter = repo.byId(selectedRenter)!['data'] as Json;
    await repo.queue('rental', Repository.uuid.v4(), {'equipment_id': selectedEquipment, 'renter_id': selectedRenter,
      'equipment_name': eq['name'], 'renter_name': renter['name'], 'user_id': renter['user_id'], 'renter_phone': renter['phone'] ?? '',
      'rent_date': initial?['rent_date'] ?? DateTime.now().toUtc().toIso8601String(), 'return_date': '', 'due_date': due.text});
    return true;
  }

  Widget statsPage() {
    final rows = repo.monthlyStats(year);
    return ListView(padding: const EdgeInsets.all(16), children: [
      Wrap(spacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
        IconButton(onPressed: () => setState(() => year--), icon: const Icon(Icons.chevron_left)),
        Text('$year 년', style: Theme.of(context).textTheme.titleLarge),
        IconButton(onPressed: () => setState(() => year++), icon: const Icon(Icons.chevron_right)),
        OutlinedButton(onPressed: () => run(() => exportStats(repo, year, pdf: false)), child: const Text('Excel 저장')),
        OutlinedButton(onPressed: () => run(() => exportStats(repo, year, pdf: true)), child: const Text('PDF 저장')),
        FilledButton(onPressed: () => run(() async { await adjustStats(); }), child: const Text('통계 조정')),
      ]),
      Padding(padding: const EdgeInsets.symmetric(vertical: 16), child: Text(
        '대여 ${rows.fold<int>(0, (v, r) => v + (r['rent_count'] as int))}건 · 반납 ${rows.fold<int>(0, (v, r) => v + (r['return_count'] as int))}건\n현재 기기에 저장된 기록 기준입니다.')),
      ...rows.map((r) => Card(child: ListTile(title: Text('${r['stat_month']} · ${r['equipment_name']}'),
        subtitle: Text('대여 ${r['rent_count']} · 반납 ${r['return_count']}${r['adjusted'] == true ? ' · 조정됨' : ''}'),
        onTap: () => run(() async { await adjustStats(initial: r); })))),
    ]);
  }

  Future<bool> adjustStats({Json? initial}) async {
    final revisions = {for (final r in repo.projected) r['id'] as String: r['revision'] as int};
    final options = <String, String>{for (final r in repo.records('equipment')) r['id'] as String: r['data']['name'] as String};
    for (final r in repo.records('rental')) {
      options.putIfAbsent(r['data']['equipment_id'] as String, () => r['data']['equipment_name'] as String);
    }
    for (final r in repo.records('override')) {
      options.putIfAbsent(r['data']['equipment_id'] as String, () => r['data']['equipment_name'] as String);
    }
    if (options.isEmpty) throw Exception('장비를 먼저 등록하세요.');
    String id = initial?['equipment_id'] as String? ?? options.keys.first;
    final month = TextEditingController(text: initial?['stat_month'] as String? ?? '$year-01');
    final rents = TextEditingController(text: '${initial?['rent_count'] ?? 0}');
    final returns = TextEditingController(text: '${initial?['return_count'] ?? 0}');
    final form = GlobalKey<FormState>();
    final result = await showDialog<String>(context: context, builder: (c) => AlertDialog(
      title: const Text('월별 통계 조정'), content: SizedBox(width: 440, child: Form(key: form,
        child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
          DropdownButtonFormField<String>(isExpanded: true, value: id, items: options.entries.map((e) => DropdownMenuItem(value: e.key, child: Text(e.value))).toList(),
            onChanged: (v) => id = v!, decoration: const InputDecoration(labelText: '장비')),
          const SizedBox(height: 12),
          TextFormField(controller: month, decoration: const InputDecoration(labelText: '월 (YYYY-MM)'),
            validator: (v) => !RegExp(r'^\d{4}-(0[1-9]|1[0-2])$').hasMatch(v ?? '') ? 'YYYY-MM 형식으로 입력하세요.' : null),
          const SizedBox(height: 12),
          for (final entry in {'대여 건수': rents, '반납 건수': returns}.entries)
            Padding(padding: const EdgeInsets.only(bottom: 12), child: TextFormField(controller: entry.value, keyboardType: TextInputType.number,
              decoration: InputDecoration(labelText: entry.key), validator: (v) => (int.tryParse(v ?? '') ?? -1) < 0 ? '0 이상의 정수로 입력하세요.' : null)),
        ])))), actions: [
        TextButton(onPressed: () => Navigator.pop(c), child: const Text('취소')),
        TextButton(onPressed: () { if (form.currentState!.validate()) Navigator.pop(c, 'delete'); }, child: const Text('자동 통계 복원')),
        FilledButton(onPressed: () { if (form.currentState!.validate()) Navigator.pop(c, 'put'); }, child: const Text('저장')),
      ]));
    if (result == null) return false;
    if (repo.workspace == null) throw Exception('PC와 최초 연결을 완료하세요.');
    final recordId = Repository.uuid.v5(repo.workspace!, 'override:$id:${month.text}');
    if (result == 'delete' && repo.byId(recordId) == null) return false;
    await repo.queue('override', recordId, {'equipment_id': id, 'equipment_name': options[id], 'stat_month': month.text,
      'rent_count': int.parse(rents.text), 'return_count': int.parse(returns.text)},
      action: result, baseRevision: revisions[recordId] ?? 0);
    return true;
  }

  Widget connectionPage() => ListView(padding: const EdgeInsets.all(16), children: [
    Text(repo.isHub ? '기준 PC · 기기 연결' : '휴대폰 · PC 연결', style: Theme.of(context).textTheme.headlineSmall),
    const SizedBox(height: 12),
    SelectableText('작업 공간: ${repo.workspace ?? '연결 전'}\n저장 위치: ${repo.directory}'),
    if (repo.serviceError.isNotEmpty) SelectableText('서비스 오류: ${repo.serviceError}'),
    const SizedBox(height: 12),
    Wrap(spacing: 8, runSpacing: 8, children: [
      if (repo.isHub) ...[
        FilledButton(onPressed: () => run(showPairing), child: const Text('휴대폰 연결 QR')),
        OutlinedButton(onPressed: () => run(showDevices), child: const Text('등록 기기 관리')),
        OutlinedButton(onPressed: () => run(() async {
          final selection = await FilePicker.platform.pickFiles(dialogTitle: '기존 rental.db 선택');
          if (selection == null || selection.files.single.path == null) return;
          if (!await confirm('빈 작업 공간에 기존 DB의 전체 데이터를 가져옵니다. 원본 DB는 변경하지 않습니다. 계속할까요?')) return;
          await repo.importLegacy(selection.files.single.path!);
          await repo.sync();
        }), child: const Text('기존 DB 가져오기')),
      ] else ...[
        FilledButton.icon(onPressed: () => run(scanPairing), icon: const Icon(Icons.qr_code_scanner), label: const Text('QR로 연결')),
        OutlinedButton(onPressed: () => run(() => manualPair()), child: const Text('직접 연결 정보 입력')),
      ],
      OutlinedButton(onPressed: () => run(repo.sync), child: const Text('지금 동기화')),
      OutlinedButton(onPressed: () => run(exportBackup), child: const Text('로컬 작업 백업')),
    ]),
    const SizedBox(height: 20),
    const Text('같은 Wi-Fi에서 기준 PC 앱이 실행 중이면 자동으로 동기화합니다. PC 주소가 바뀌면 QR로 다시 연결하세요. 연결 코드는 3분 동안 한 번만 사용할 수 있습니다.'),
    const Divider(height: 32),
    Text('충돌 ${repo.conflicts.length}건', style: Theme.of(context).textTheme.titleLarge),
    ...repo.conflicts.map((op) => Card(child: Padding(padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text('${op['kind']} · ${op['message']}'),
      Text(const JsonEncoder.withIndent('  ').convert({...op['data'] as Json}..remove('photo')), maxLines: 12, overflow: TextOverflow.ellipsis),
      Wrap(children: [
        TextButton(onPressed: () => run(() => resolveConflict(op)), child: const Text('확인 후 다시 처리')),
        TextButton(onPressed: () => run(() async {
          if (await confirm('이 충돌 작업을 취소할까요? 중앙 기록은 유지됩니다.')) await repo.dismissConflict(op['id'] as String);
        }), child: const Text('작업 취소')),
      ]),
    ])))),
    const Divider(height: 32),
    Text('전송 대기 ${repo.pending.length}건', style: Theme.of(context).textTheme.titleLarge),
    ...repo.pending.map((op) => ListTile(title: Text('${op['kind']} · ${op['action']}'),
      subtitle: Text('${op['data']['name'] ?? op['data']['equipment_name'] ?? op['entity_id']}'))),
  ]);

  Future<void> exportBackup() async {
    final bytes = utf8.encode(jsonEncode({'format': 'erm-client-backup-v1', 'state': repo.state}));
    final path = await FilePicker.platform.saveFile(fileName: 'erm-local-backup.json', bytes: Uint8List.fromList(bytes));
    if (path != null && Platform.isWindows) await File(path).writeAsBytes(bytes);
  }

  Future<void> resolveConflict(Json op) async {
    bool saved = false;
    if (op['action'] == 'delete') {
      if (await confirm('현재 기록을 다시 확인한 뒤 삭제를 재요청할까요?')) {
        await repo.queue(op['kind'] as String, op['entity_id'] as String, op['data'] as Json, action: 'delete');
        saved = true;
      }
    } else if (op['action'] == 'return') {
      if (await confirm('이 대여 기록에 반납을 재요청할까요?')) {
        await repo.queue('rental', op['entity_id'] as String, op['data'] as Json, action: 'return');
        saved = true;
      }
    } else if (op['kind'] == 'rental') {
      saved = await rent(initial: op['data'] as Json);
    } else if (op['kind'] == 'override') {
      saved = await adjustStats(initial: op['data'] as Json);
    } else {
      final latest = repo.byId(op['entity_id'] as String);
      if (latest != null && !await confirm('최신 내용: ${jsonEncode({...latest['data'] as Json}..remove('photo'))}\n\n원래 입력 내용을 편집하여 다시 저장할까요?')) return;
      var entityId = op['entity_id'] as String;
      if (latest == null && repo.revisionOf(entityId) > 0) {
        if (!await confirm('이 기록은 삭제되었습니다. 원래 입력 내용으로 새 항목을 만들까요?')) return;
        entityId = Repository.uuid.v4();
      }
      saved = await editEntity(op['kind'] as String, record: {
        'id': entityId, 'data': op['data'], 'revision': latest?['revision'] ?? 0,
      });
    }
    if (saved) await repo.dismissConflict(op['id'] as String);
  }

  Future<void> showPairing() async {
    final issued = await repo.request('/v2/pairing');
    final urls = repo.addresses.where((a) => !a.contains('127.0.0.1')).toList();
    if (urls.isEmpty) throw Exception('네트워크 주소를 찾을 수 없습니다. PC의 Wi-Fi 연결을 확인하세요.');
    String address = urls.first;
    if (!mounted) return;
    await showDialog<void>(context: context, builder: (c) => StatefulBuilder(builder: (c, update) {
      final payload = jsonEncode({'url': address, 'fingerprint': repo.connection['fingerprint'], 'code': issued['code'], 'workspace': repo.workspace});
      return AlertDialog(title: const Text('휴대폰에서 QR 스캔'), content: SizedBox(width: 400, child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        DropdownButton<String>(isExpanded: true, value: address, items: urls.map((url) => DropdownMenuItem(value: url, child: Text(url))).toList(),
          onChanged: (v) => update(() => address = v!)),
        QrImageView(data: payload, size: 260, backgroundColor: Colors.white),
        SelectableText('연결 코드: ${issued['code']}\n3분 동안 1회 사용 가능\n인증서 지문:\n${repo.connection['fingerprint']}'),
        TextButton(onPressed: () => Clipboard.setData(ClipboardData(text: payload)), child: const Text('연결 정보 복사')),
      ]))), actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('닫기'))]);
    }));
  }

  Future<void> scanPairing() async {
    final value = await Navigator.push<String>(context, MaterialPageRoute(builder: (_) => const ScanPage()));
    if (value == null) return;
    final data = jsonDecode(value) as Json;
    if (repo.workspace != null && data['workspace'] != repo.workspace) throw Exception('다른 작업 공간의 QR코드입니다.');
    if (!await confirm('${data['url']}의 PC와 연결할까요? PC 화면에서 발급한 QR인지 확인하세요.')) return;
    await repo.pair(data['url'] as String, data['fingerprint'] as String, data['code'] as String, 'Android 휴대폰');
    await repo.sync();
  }

  Future<void> manualPair() async {
    final url = TextEditingController(text: repo.connection['url'] as String? ?? 'https://');
    final fingerprint = TextEditingController(text: repo.connection['fingerprint'] as String? ?? '');
    final code = TextEditingController();
    final name = TextEditingController(text: 'Android 휴대폰');
    final accepted = await showDialog<bool>(context: context, builder: (c) => AlertDialog(title: const Text('PC 연결 정보'),
      content: SizedBox(width: 450, child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        field(url, 'PC 주소 (https://주소:8766)'), field(fingerprint, 'PC 화면의 인증서 지문'), field(code, '연결 코드'), field(name, '기기 이름'),
      ]))), actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('취소')),
        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('연결'))])) ?? false;
    if (!accepted) return;
    await repo.pair(url.text, fingerprint.text, code.text, name.text);
    await repo.sync();
  }

  Future<void> showDevices() async {
    final result = await repo.request('/v2/devices', method: 'GET');
    if (!mounted) return;
    await showDialog<void>(context: context, builder: (c) => AlertDialog(title: const Text('등록 기기'),
      content: SizedBox(width: 450, child: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min,
        children: (result['devices'] as List).map<Widget>((raw) {
          final d = raw as Json;
          return ListTile(title: Text(d['name'] as String), subtitle: Text(d['revoked'] == 1 ? '연결 해제됨' : '연결 허용'),
            trailing: d['revoked'] == 1 ? null : TextButton(onPressed: () => run(() async {
              await repo.request('/v2/devices/${d['id']}', method: 'DELETE');
              if (c.mounted) Navigator.pop(c);
            }), child: const Text('해제')));
        }).toList()))), actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('닫기'))]));
  }
}

class ScanPage extends StatefulWidget {
  const ScanPage({super.key});
  @override
  State<ScanPage> createState() => _ScanPageState();
}

class _ScanPageState extends State<ScanPage> {
  bool captured = false;
  @override
  Widget build(BuildContext context) => Scaffold(appBar: AppBar(title: const Text('PC의 연결 QR코드 스캔')),
    body: MobileScanner(onDetect: (capture) {
      if (captured) return;
      for (final barcode in capture.barcodes) {
        if (barcode.rawValue != null) {
          captured = true;
          Navigator.pop(context, barcode.rawValue);
          break;
        }
      }
    }));
}
