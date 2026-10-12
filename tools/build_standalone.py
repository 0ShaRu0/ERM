"""Render a self-contained static PWA, without rental.db or personal data."""
from pathlib import Path
import shutil
import zipfile

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "standalone"


def main():
    OUTPUT.mkdir(exist_ok=True)
    environment = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
    html = environment.get_template("index.html").render(bootstrap_key="", independent=True)
    html = html.replace('"/static/', '"./static/')
    html = html.replace("<span>동기화</span>", "<span>기기 · 백업</span>")
    html = html.replace("지금 동기화", "새로고침").replace("동기화 상태 열기", "기기 저장 및 백업 열기")
    html = html.replace("서버 연결을 확인하고 있습니다.", "기기 저장소를 확인하고 있습니다.")
    html = html.replace("오프라인에서도 처리할 수 있으며, 연결되면 입력 순서대로 서버에 전송됩니다.", "대여·반납 기록은 이 기기에 즉시 저장됩니다.")
    html = html.replace(">Excel</button>", ">Excel용 CSV</button>")
    html = html.replace(">PDF</button>", ">인쇄 · PDF</button>")
    (OUTPUT / "index.html").write_text(html, encoding="utf-8")
    assets = ["app.js", "local-store.js", "usb-merge.js", "usb-client.js", "app.css", "icon.svg", "icon-192.png", "icon-512.png"]
    (OUTPUT / "static").mkdir(exist_ok=True)
    for name in assets:
        shutil.copy2(ROOT / "static" / name, OUTPUT / "static" / name)
    shutil.copy2(ROOT / "static" / "standalone.webmanifest", OUTPUT / "static" / "manifest.webmanifest")
    shutil.copy2(ROOT / "static" / "standalone-sw.js", OUTPUT / "sw.js")
    files = [OUTPUT / "index.html", OUTPUT / "sw.js", OUTPUT / "static" / "manifest.webmanifest"]
    files.extend(OUTPUT / "static" / name for name in assets)
    with zipfile.ZipFile(ROOT / "이음-독립앱.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, file.relative_to(OUTPUT))
    with zipfile.ZipFile(ROOT / "이음-USB설치.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(file, file.relative_to(ROOT))
        for name in ("Android_USB_최초설치.bat", "PC_앱_최초설치.bat", "USB_데이터_동기화.bat", "tools/install_independent.py", "tools/usb_broker.py", "독립앱_사용안내.md"):
            archive.write(ROOT / name, name)
        for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt", "source.properties"):
            path = ROOT / "tools/platform-tools" / name
            if path.is_file():
                archive.write(path, path.relative_to(ROOT))
    print(f"Built {OUTPUT} and 이음-독립앱.zip (no user data included)")


if __name__ == "__main__":
    main()
