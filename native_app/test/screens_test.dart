import 'package:erm_native/main.dart';
import 'package:erm_native/repository.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

class ScreenRepository extends Repository {
  ScreenRepository() {
    directory = 'test-local-storage';
    state['workspace'] = Repository.uuid.v4();
    state['records'] = [
      {'id': 'equipment', 'kind': 'equipment', 'revision': 1, 'deleted': 0,
        'data': {'name': '카메라', 'category': '영상', 'quantity': 2, 'photo': ''}},
      {'id': 'renter', 'kind': 'renter', 'revision': 1, 'deleted': 0,
        'data': {'name': '홍길동', 'user_id': 'u1', 'phone': '010'}},
    ];
  }

  @override
  Future<void> initialize() async {}
  @override
  Future<void> shutdown() async {}
  @override
  bool get isHub => false;
}

void main() {
  for (final size in [const Size(390, 844), const Size(1280, 800)]) {
    testWidgets('offline screens fit ${size.width.toInt()}px and registration opens', (tester) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(MaterialApp(locale: const Locale('ko'), supportedLocales: const [Locale('ko')],
        localizationsDelegates: GlobalMaterialLocalizations.delegates, home: Home(repository: ScreenRepository())));
      await tester.pumpAndSettle();
      expect(find.text('카메라'), findsOneWidget);
      await tester.tap(find.text('장비 등록'));
      await tester.pumpAndSettle();
      expect(find.text('기기에 저장'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('취소'));
      await tester.pumpAndSettle();
      for (final title in ['대여자', '대여·반납', '통계', '연결']) {
        await tester.tap(find.text(title).last);
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull, reason: '$title at $size');
      }
      expect(find.text('QR로 연결'), findsOneWidget);
    });
  }
}
