# ERM 설치형 앱 (0.1.0)

Windows 기준 PC와 Android 휴대폰에서 동작하는 Flutter 앱입니다.
장비·대여자·대여·반납·사진·월별 통계 조정을 기기별 SQLite에 저장하고,
같은 네트워크의 PC 앱과 인증된 HTTPS 연결로 동기화합니다.

## 구현된 기능

- Windows / Android 네이티브 실행 프로젝트와 반응형 화면
- 오프라인 CRUD, 대여·반납, 전체 이력 검색, 장비 사진(신규 선택 3MB 이하)
- 기기 데이터 기준 월별 통계, 통계 조정/자동 통계 복원, Excel·한글 PDF 저장
- 로컬 변경과 전송 대기열의 원자적 저장, 재실행 후 대기 작업 유지
- Windows 앱이 시작하는 내장 Python 동기화 서비스
- QR/직접 입력 연결, 일회용 코드(3분·1회), 기기별 토큰과 연결 해제
- 인증서 SHA-256 지문 고정, HTTPS 통신, 운영체제 보안 저장소에 토큰 보관
- 20초 주기·앱 복귀·작업 저장 시 동기화, 수동 동기화
- UUID, 작업 중복 반영 방지, 버전 충돌, 삭제 기록, 충돌 원문 보존·재처리
- 기존 `rental.db` 전체 이력·통계 조정·사진의 일회성 읽기 전용 가져오기
- 로컬 작업 JSON 내보내기

**0.1 구현 범위:** 서버 응답은 변경분 대신 전체 스냅샷이며 사진도 JSON에 포함됩니다.
대기 작업은 10개씩 전송합니다. mDNS 자동 발견, 앱 종료 후 Android 백그라운드 동기화,
별도 사진 전송·증분 동기화, JSON 백업 자동 복원은 후속 작업입니다.

## 빌드 환경

코드 분석과 테스트에 사용한 버전: Flutter **3.29.3**, Dart **3.7.2**.
`pubspec.lock`을 포함하여 의존성 버전을 고정합니다.

Windows에서 다음 도구를 설치합니다.

1. Flutter SDK 및 PATH 설정
2. Windows 앱: Visual Studio 2022의 **Desktop development with C++** 워크로드
3. 내장 서비스 빌드: Python 3.11 이상
4. Android APK: Android Studio, Android SDK, JDK 17 이상, Android SDK 라이선스 동의
5. Setup.exe 생성: 선택적으로 Inno Setup 6 (`ISCC.exe`를 PATH에 추가)

먼저 `flutter doctor -v`로 필요한 플랫폼 도구를 확인합니다.
일반 사용자의 PC에는 Flutter/Python 설치가 필요 없습니다. 빌드 결과 전체를 배포합니다.

프로젝트 루트에서 PowerShell로 실행:

```powershell
.\build_native.ps1 -Target windows
.\build_native.ps1 -Target android
# 둘 다 빌드하려면:
.\build_native.ps1 -Target all
```

출력:

| 대상 | 결과 |
|---|---|
| Windows 압축 배포 | `native_app/dist/ERM-Windows.zip` |
| Windows 설치 프로그램 (Inno Setup 사용 시) | `native_app/dist/ERM-Setup.exe` |
| Android 직접 설치 | `native_app/build/app/outputs/flutter-apk/app-release.apk` |

Windows 압축 파일은 **전체를 풀고** `erm_native.exe`를 실행합니다.
`erm_hub.exe`, DLL, `data/`가 같은 배포 폴더에 있어야 합니다.
휴대폰에서는 APK를 설치합니다(Android 6.0/API 23 이상).

`android/key.properties`가 없으면 APK는 내부 시험용 디버그 키로 서명됩니다.
장기 사용/배포 전에는 전용 키를 만들고 아래 파일을 설정하세요. 업데이트에는 같은 키가 필요합니다.

```properties
# native_app/android/key.properties (git에서 제외됨)
storeFile=C:/keys/erm-release.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=erm
keyPassword=YOUR_KEY_PASSWORD
```

## 최초 사용

1. 기준 PC에서 앱을 실행합니다.
2. 기존 데이터를 쓸 경우 **새 기록을 만들기 전에** `연결 → 기존 DB 가져오기`에서
   기존 프로젝트의 `rental.db`를 선택합니다. 사진은 DB와 같은 폴더의 `images/`에서 읽습니다.
   기존 웹 앱의 전송 대기 작업은 먼저 기존 웹 앱에서 동기화해야 합니다.
3. 기존 DB 가져오기는 빈 작업 공간에서 한 번만 가능합니다. 완료 후 데이터는 새 앱 DB에서 관리됩니다.
   기존 웹/Tkinter 프로그램에 나중에 입력한 변경은 새 앱으로 자동 반영되지 않습니다.
4. PC와 휴대폰을 같은 Wi-Fi에 연결합니다.
5. PC의 `연결 → 휴대폰 연결 QR`을 열고, 휴대폰에서 접근할 PC의 네트워크 주소를 선택합니다.
6. Android의 `연결 → QR로 연결`로 스캔하고 PC 주소를 확인합니다.
7. QR 이용이 어려우면 주소·인증서 지문·연결 코드를 직접 입력합니다.
8. 최초 동기화가 완료되면 인터넷/PC 연결 없이도 앱을 재실행하고 작업할 수 있습니다.

Windows 방화벽이 물으면 `erm_hub.exe`의 개인 네트워크 접근을 허용합니다(기본 TCP 8766).
공유기의 게스트 Wi-Fi/기기 간 통신 차단 기능이 켜져 있으면 연결할 수 없습니다.
PC 주소가 바뀌면 같은 PC에서 새 QR을 발급받아 다시 연결합니다. 작업 공간이 같으면 대기 기록은 유지됩니다.

PC 앱을 닫거나 PC가 절전 상태가 되면 동기화가 멈춥니다. 휴대폰 작업은 계속 로컬에 저장됩니다.
Android가 앱을 중지시킨 동안의 실시간 동기화는 보장하지 않습니다. 앱을 다시 열면 재시도합니다.

## 데이터와 충돌

- UI는 확정된 기록 위에 아직 전송하지 않은 변경을 반영해 보여 줍니다.
- 서버 응답을 받지 못한 작업은 같은 작업 ID로 재전송하며 한 번만 적용됩니다.
- 마지막 장비를 여러 기기가 대여하면 기준 PC에서 먼저 유효하게 반영된 작업만 확정됩니다.
- 같은 레코드의 동시 수정은 자동 덮어쓰기 대신 충돌로 보관합니다.
- 이미 반납된 대여를 다시 반납해도 수량이 중복 증가하지 않습니다.
- 대여 중 장비/대여자 삭제, 중복 대여자 아이디, 잘못된 참조는 충돌입니다.
- 대여 이력에는 당시 장비명·대여자 정보가 남습니다. 삭제 후에도 통계 이력은 유지됩니다.
- `연결 → 충돌 → 확인 후 다시 처리`로 원래 입력 내용을 편집합니다.
  참조 기록이 먼저 충돌한 경우 장비·대여자를 먼저 해결한 뒤 대여 작업을 처리하세요.
- 로컬 통계와 출력물은 마지막 동기화 시각, 대기/충돌 수를 함께 표시합니다.
- 다른 작업 공간으로 연결을 변경하여 대기 기록을 섞는 동작은 차단합니다.

## 저장 위치와 백업

`연결` 화면에 현재 앱 데이터 디렉터리가 표시됩니다.

```text
ERM/
  client.db          # 기기별 확정 스냅샷, 전송 대기 작업, 충돌 기록
  hub/              # Windows 기준 PC만
    hub.db          # 동기화 기준 DB, 등록 기기, 처리한 작업 ID
    hub.crt         # 기준 PC 인증서
    hub.key         # 기준 PC 인증서 개인 키
```

PC 앱을 닫고 해당 디렉터리 전체를 복사하면 기준 DB와 연결 신원을 함께 백업할 수 있습니다.
실행 중에는 DB의 WAL 파일이 존재할 수 있으므로 `hub.db` 파일만 복사하지 마세요.
복원은 앱이 종료된 상태에서 동일 PC의 해당 디렉터리를 백업본으로 되돌리는 방식입니다.
구버전 백업으로 되돌리기 전에는 다른 기기의 미동기화 작업을 내보내고 복원 시점을 확인해야 합니다.

`로컬 작업 백업`은 사진을 포함한 스냅샷·대기·충돌 JSON을 저장합니다.
인증 토큰은 포함하지 않습니다. 이 JSON을 앱으로 자동 복원하는 화면은 아직 제공하지 않습니다.

## 개발 실행 및 테스트

Windows 앱 개발 시 Python 서비스 경로를 지정할 수 있습니다.

```powershell
python -m pip install -r native_service/requirements.txt
Set-Location native_app
flutter run -d windows --dart-define=ERM_PYTHON=python --dart-define=ERM_SERVICE_SCRIPT=C:/path/to/ERM/native_service/server.py
```

백엔드 테스트는 프로젝트 루트에서:

```powershell
python -m pip install -r requirements.txt -r native_service/requirements.txt
python -m unittest discover -s tests -p "test_*.py" -v
```

앱 테스트는 `native_app`에서:

```powershell
flutter analyze
flutter test
# 실제 Python HTTPS 서비스와 연동하는 테스트도 실행:
$env:ERM_TEST_PYTHON = (Get-Command python).Source
flutter test --reporter expanded
```

Linux 검증 환경에는 SQLite 공유 라이브러리(`libsqlite3.so`)가 필요합니다.
Windows·Android 빌드/설치 및 실제 Wi-Fi 기기 테스트는 해당 도구·기기 환경에서 별도로 확인해야 합니다.

## 후속 개발 계획

1. Windows/Android 실제 설치, 방화벽·절전·카메라·파일 저장 UX 확인
2. 정식 서명 APK/설치 프로그램과 업데이트 방식 확정
3. 변경 커서 기반 증분 수신과 사진 파일 분리·재개 전송
4. mDNS 기기 발견 및 PC 주소 변경 자동 추적
5. 충돌 비교 화면 고도화, 자동 백업·복원 마법사
6. Android 백그라운드 작업 예약(운영체제 제약에 맞춘 best-effort 동기화)

구조 및 전환 방향은 루트의 `앱_전환계획.md`를 참고하세요.
