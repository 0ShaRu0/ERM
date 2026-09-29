import 'dart:io';

import 'package:excel/excel.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/services.dart';
import 'package:pdf/pdf.dart';
import 'package:pdf/widgets.dart' as pw;

import 'repository.dart';

Future<void> exportStats(Repository repo, int year, {required bool pdf}) async {
  final rows = repo.monthlyStats(year);
  final headers = ['월', '장비', '대여', '반납'];
  final values = rows.map((r) => [r['stat_month'], r['equipment_name'], r['rent_count'], r['return_count']]).toList();
  final note = '마지막 동기화: ${repo.lastSync} / 대기 ${repo.pending.length} / 충돌 ${repo.conflicts.length}';
  late Uint8List bytes;
  if (pdf) {
    final font = pw.Font.ttf(await rootBundle.load('assets/NanumGothic-Regular.ttf'));
    final doc = pw.Document(theme: pw.ThemeData.withFont(base: font, bold: font));
    doc.addPage(pw.MultiPage(pageFormat: PdfPageFormat.a4, build: (_) => [
      pw.Text('$year 장비 대여 통계', style: const pw.TextStyle(fontSize: 20)),
      pw.SizedBox(height: 12), pw.Text(note, style: const pw.TextStyle(fontSize: 9)),
      pw.SizedBox(height: 12),
      pw.TableHelper.fromTextArray(headers: headers, data: values),
    ]));
    bytes = await doc.save();
  } else {
    final book = Excel.createExcel();
    final sheet = book['통계'];
    book.delete('Sheet1');
    sheet.appendRow([TextCellValue(note)]);
    sheet.appendRow(headers.map(TextCellValue.new).toList());
    for (final row in values) {
      sheet.appendRow(row.map<CellValue>((v) => v is int ? IntCellValue(v) : TextCellValue('$v')).toList());
    }
    bytes = Uint8List.fromList(book.encode()!);
  }
  final extension = pdf ? 'pdf' : 'xlsx';
  final path = await FilePicker.platform.saveFile(dialogTitle: '통계 저장',
      fileName: 'ERM-$year.$extension', type: FileType.custom, allowedExtensions: [extension], bytes: bytes);
  if (path != null && !Platform.isAndroid && !Platform.isIOS) await File(path).writeAsBytes(bytes);
}
