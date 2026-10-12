"""Temporary PWA delivery or explicitly enabled USB data exchange on localhost."""
import argparse
import json
import secrets
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from urllib.request import urlopen
from urllib.parse import urlsplit, parse_qs
import webbrowser
import zipfile

try:
    from tools.usb_broker import UsbBroker
except ModuleNotFoundError:
    from usb_broker import UsbBroker

ROOT = Path(__file__).resolve().parents[1]
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"


def find_adb():
    candidates = [ROOT / "tools/platform-tools/adb.exe"]
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append(Path(os.environ[variable]) / "platform-tools/adb.exe")
    for path in candidates:
        if path.is_file():
            return str(path)
    return shutil.which("adb")


def download_adb():
    print("Google 공식 Android Platform Tools를 내려받습니다...")
    target = ROOT / "tools/platform-tools"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as downloaded:
        with urlopen(PLATFORM_TOOLS_URL, timeout=60) as response:
            shutil.copyfileobj(response, downloaded)
        downloaded.seek(0)
        with zipfile.ZipFile(downloaded) as archive:
            # Fixed filenames only; never extract arbitrary archive paths.
            for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt", "source.properties"):
                with archive.open(f"platform-tools/{name}") as source:
                    with (target / name).open("wb") as destination:
                        shutil.copyfileobj(source, destination)
    return str(target / "adb.exe")


def adb_run(adb, *arguments):
    result = subprocess.run([adb, *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "ADB 연결 실패")
    return result.stdout.strip()


class AppFiles(SimpleHTTPRequestHandler):
    def usb_request(self):
        target = urlsplit(self.path)
        if not target.path.startswith("/usb-sync/"):
            return False
        broker = getattr(self.server, "usb_broker", None)
        code, result = 200, None
        try:
            if broker is None:
                raise ValueError("USB 데이터 동기화 도구를 실행하세요.")
            host = self.headers.get("Host", "")
            allowed = {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}
            if host not in allowed or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
                raise PermissionError("허용되지 않은 접속 주소입니다.")
            action = target.path.rsplit("/", 1)[-1]
            if action == "session" and self.command == "GET":
                result = {"token": broker.token}
            else:
                if not secrets.compare_digest(self.headers.get("X-ERM-USB", "").encode(), broker.token.encode()):
                    raise PermissionError("USB 연결 키가 올바르지 않습니다.")
                if action == "status" and self.command == "GET":
                    args = parse_qs(target.query)
                    result = broker.status(args.get("round", [""])[0], args.get("client_id", [""])[0])
                elif self.command == "POST" and action in ("submit", "ack"):
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 100 * 1024 * 1024:
                        raise ValueError("동기화 데이터는 100MB 이하이어야 합니다.")
                    self.connection.settimeout(30)
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("동기화 요청 형식이 올바르지 않습니다.")
                    result = broker.submit(body) if action == "submit" else broker.acknowledge(body)
                else:
                    raise ValueError("지원하지 않는 USB 요청입니다.")
        except PermissionError as error:
            code, result = 403, {"error": str(error)}
        except (ValueError, OSError) as error:
            code, result = 400, {"error": str(error)}
        payload = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        return True

    def do_GET(self):
        if not self.usb_request():
            super().do_GET()

    def do_POST(self):
        if not self.usb_request():
            self.send_error(404)

    def log_message(self, *_args):
        pass

    def list_directory(self, _path):
        self.send_error(404)
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", action="store_true", help="PC에 최초 설치")
    parser.add_argument("--sync", action="store_true", help="USB 연결 중 양쪽 앱의 데이터 동기화")
    parser.add_argument("--download-tools", action="store_true", help="Android 도구만 준비하고 종료")
    parser.add_argument("--serial", help="여러 Android USB 기기 연결 시 설치 대상 일련번호")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.download_tools:
        print(find_adb() or download_adb())
        return
    app_dir = ROOT / "standalone"
    if not (app_dir / "index.html").is_file():
        raise RuntimeError("standalone 앱 파일이 없습니다. python tools/build_standalone.py를 실행하세요.")
    if not 1024 <= args.port <= 65535:
        raise ValueError("포트는 1024~65535 사이로 지정하세요.")
    adb, device = None, None
    if not args.desktop:
        adb = find_adb() or download_adb()
        try:
            device = args.serial or adb_run(adb, "-d", "get-serialno")
            if not device or adb_run(adb, "-s", device, "get-state") != "device":
                raise RuntimeError("USB 기기가 준비되지 않았습니다.")
        except RuntimeError as error:
            raise RuntimeError(
                "휴대폰의 개발자 옵션 → USB 디버깅을 켜고, 데이터 전송 USB 케이블로 연결하세요. "
                "휴대폰 잠금을 풀고 이 PC의 USB 디버깅을 허용한 후 다시 실행하세요. "
                "여러 기기가 있으면 하나만 연결하거나 --serial을 지정하세요.\n" + str(error)
            ) from error
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(AppFiles, directory=str(app_dir)))
    if args.sync:
        server.usb_broker = UsbBroker()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    reverse_created = False
    url = f"http://localhost:{args.port}/"
    try:
        if adb:
            # Do not replace any reverse mapping owned by another program.
            adb_run(adb, "-s", device, "reverse", "--no-rebind", f"tcp:{args.port}", f"tcp:{args.port}")
            reverse_created = True
            phone_url = url + "?usb_role=phone" if args.sync else url
            adb_run(adb, "-s", device, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", phone_url, "-p", "com.android.chrome")
        else:
            webbrowser.open(url)
        if args.sync:
            webbrowser.open(url + "?usb_role=pc")
            print(f"\nUSB 동기화 주소: {url}")
            print("PC와 휴대폰 모두 '기기 · 백업'에서 'USB로 양쪽 데이터 동기화'를 누르세요.")
            print("기존에 설치한 것과 같은 브라우저를 사용하고, 양쪽 기록이 맞는지 먼저 확인하세요.")
            print("양쪽에 '동기화를 완료했습니다'가 표시될 때까지 이 창과 USB를 유지하세요.")
            input("\n양쪽 동기화 완료 후 Enter: ")
            return
        print(f"\n앱 설치 주소: {url}")
        print("1. 열린 브라우저의 '기기 · 백업'에서 '오프라인 실행 준비 완료'를 확인하세요.")
        print("2. 앱 설치 버튼 또는 브라우저 메뉴 → 앱 설치/홈 화면에 추가를 누르세요.")
        print("3. 설치한 앱을 한 번 열어 준비 완료를 확인하세요.")
        print("4. 여기서 Enter를 누르면 설치용 연결이 종료됩니다. USB를 빼고 앱을 다시 여세요.")
        print("\n데이터는 각 기기에 따로 저장됩니다. 이후 실행에는 PC나 USB가 필요 없습니다.")
        print("앱 업데이트나 재설치는 같은 포트와 주소로 이 도구를 다시 실행하세요.")
        input("\n설치 완료 후 Enter: ")
    finally:
        if reverse_created:
            try:
                adb_run(adb, "-s", device, "reverse", "--remove", f"tcp:{args.port}")
            except (RuntimeError, subprocess.TimeoutExpired):
                print("USB가 이미 분리되었습니다. 설치용 파일 서버를 종료합니다.")
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(f"\n설치를 완료하지 못했습니다: {error}")
        raise SystemExit(1)
