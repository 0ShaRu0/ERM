import os
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
from web_app import create_app
from werkzeug.serving import make_server


BROWSER_PATHS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


def main():
    browser_path = next(
        (path for path in BROWSER_PATHS if os.path.isfile(path)), None
    )
    if browser_path is None:
        raise RuntimeError("Chrome 또는 Microsoft Edge를 찾을 수 없습니다.")

    with tempfile.TemporaryDirectory() as temp_dir:
        database.DB_PATH = os.path.join(temp_dir, "rental.db")
        database.IMAGE_DIR = os.path.join(temp_dir, "images")
        database.init_db()
        database.add_equipment("Browser Test Camera", "Test", 2)
        database.add_renter("edge01", "Edge User", "010-1000-2000")

        server = make_server("127.0.0.1", 0, create_app())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        profile_path = os.path.join(temp_dir, "browser-profile")
        url = f"http://127.0.0.1:{server.server_port}"

        def run_browser():
            return subprocess.run(
                [
                    browser_path,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--enable-logging=stderr",
                    "--v=0",
                    f"--user-data-dir={profile_path}",
                    "--virtual-time-budget=12000",
                    "--dump-dom",
                    url,
                ],
                capture_output=True,
                timeout=35,
            )

        try:
            result = run_browser()
            dom = result.stdout.decode("utf-8", "replace")
            if result.returncode != 0 or "Browser Test Camera" not in dom:
                log = result.stderr.decode("utf-8", "replace")
                raise AssertionError(
                    "브라우저에서 동적 장비 목록을 불러오지 못했습니다.\n"
                    f"exit={result.returncode}\n"
                    f"connected={'서버 연결됨' in dom}\n"
                    f"toast_error={'toast error' in dom}\n"
                    f"stderr={log[-3000:]}"
                )
            controlled_result = run_browser()
            controlled_dom = controlled_result.stdout.decode("utf-8", "replace")
            if (
                controlled_result.returncode != 0
                or "Browser Test Camera" not in controlled_dom
            ):
                raise AssertionError("서비스 워커 활성화 후 온라인 화면을 열지 못했습니다.")
        finally:
            server.shutdown()
            thread.join(5)


if __name__ == "__main__":
    main()
