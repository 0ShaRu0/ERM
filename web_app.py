import os
import secrets
import socket
import tempfile
import threading
import webbrowser
from datetime import date, datetime
from urllib.error import URLError
from urllib.request import urlopen

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    send_file,
    send_from_directory,
)
from werkzeug.utils import secure_filename

import database
import stats_export


ALLOWED_IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


def _rows(rows):
    return [dict(row) for row in rows]


def _equipment_rows(rows):
    items = []
    for row in rows:
        item = dict(row)
        image_path = item.pop("image_path", "")
        item["image_url"] = (
            f"/images/{os.path.basename(image_path)}" if image_path else ""
        )
        items.append(item)
    return items


def build_snapshot(app):
    port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "8765"
    try:
        connection_urls = local_addresses(int(port), request.scheme)
    except ValueError:
        connection_urls = [request.host_url.rstrip("/")]
    snapshot = database.snapshot_rows()
    return {
        "version": snapshot["version"],
        "database_id": snapshot["database_id"],
        "access_pin": database.get_access_pin(),
        "server_time": datetime.now().replace(microsecond=0).isoformat(),
        "connection_urls": connection_urls,
        "equipment": _equipment_rows(snapshot["equipment"]),
        "renters": _rows(snapshot["renters"]),
        "active_rentals": _rows(snapshot["active_rentals"]),
        "recent_rentals": _rows(snapshot["recent_rentals"]),
    }


def _json_error(message, status=400):
    return jsonify(ok=False, message=message), status


def _parse_date(value, label):
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label}은 YYYY-MM-DD 형식이어야 합니다.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label}은 YYYY-MM-DD 형식이어야 합니다.")
    return value


def _parse_stats_filters():
    year = request.args.get("year", str(date.today().year)).strip()
    if len(year) != 4 or not year.isdigit():
        raise ValueError("기준 연도가 올바르지 않습니다.")
    start_date = _parse_date(request.args.get("start_date", "").strip(), "시작일")
    end_date = _parse_date(request.args.get("end_date", "").strip(), "종료일")
    if start_date and end_date and start_date > end_date:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    equipment_value = request.args.get("equipment_id", "").strip()
    equipment_id = int(equipment_value) if equipment_value else None
    return year, start_date, end_date, equipment_id


def _equipment_name(equipment_id):
    if equipment_id is None:
        return "전체 장비"
    for item in database.rental_equipment_items():
        if item["equipment_id"] == equipment_id:
            return item["equipment_name"]
    return f"장비 #{equipment_id}"


def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
    app.json.ensure_ascii = False

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def protect_mutations():
        if request.path.startswith("/api/") and request.path != "/api/health":
            supplied_pin = request.headers.get("X-ERM-Key", "")
            if not secrets.compare_digest(supplied_pin, database.get_access_pin()):
                return _json_error("연결 PIN을 확인하세요.", 401)
            if (
                request.method not in ("GET", "HEAD", "OPTIONS")
                and request.headers.get("X-ERM-Client") != "web"
            ):
                return _json_error("허용되지 않은 요청입니다.", 403)
        return None

    @app.get("/")
    def index():
        is_local = request.remote_addr in ("127.0.0.1", "::1")
        return render_template(
            "index.html",
            bootstrap_key=database.get_access_pin() if is_local else "",
        )

    @app.get("/sw.js")
    def service_worker():
        response = send_from_directory(app.static_folder, "sw.js")
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/images/<path:filename>")
    def equipment_image(filename):
        return send_from_directory(database.IMAGE_DIR, filename)

    @app.get("/api/health")
    def health():
        port = request.host.rsplit(":", 1)[-1] if ":" in request.host else "8765"
        try:
            connection_urls = local_addresses(int(port), request.scheme)
        except ValueError:
            connection_urls = [request.host_url.rstrip("/")]
        return jsonify(
            ok=True,
            version=database.get_data_version(),
            connection_urls=connection_urls,
        )

    @app.get("/api/snapshot")
    def snapshot():
        return jsonify(build_snapshot(app))

    @app.post("/api/sync")
    def sync():
        body = request.get_json(silent=True) or {}
        client_id = str(body.get("client_id", "")).strip()
        operations = body.get("operations", [])
        if not client_id:
            return _json_error("기기 ID가 없습니다.")
        if not isinstance(operations, list):
            return _json_error("동기화 작업 형식이 올바르지 않습니다.")
        if len(operations) > 500:
            return _json_error("한 번에 최대 500개 작업을 동기화할 수 있습니다.")
        results = database.apply_sync_operations(client_id, operations)
        return jsonify(ok=True, results=results, snapshot=build_snapshot(app))

    @app.post("/api/equipment")
    def add_equipment():
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "").strip()
        try:
            quantity = int(request.form.get("quantity", "1"))
        except ValueError:
            return _json_error("수량은 숫자로 입력하세요.")
        if not name or not category:
            return _json_error("장비명과 분류를 입력하세요.")
        if quantity < 1 or quantity > 9999:
            return _json_error("수량은 1~9999 사이로 입력하세요.")

        upload = request.files.get("image")
        temp_path = None
        try:
            if upload and upload.filename:
                filename = secure_filename(upload.filename)
                extension = os.path.splitext(filename)[1].lower()
                if extension not in ALLOWED_IMAGE_EXTENSIONS:
                    return _json_error("지원하는 이미지 형식이 아닙니다.")
                handle = tempfile.NamedTemporaryFile(suffix=extension, delete=False)
                temp_path = handle.name
                handle.close()
                upload.save(temp_path)
                try:
                    from PIL import Image

                    with Image.open(temp_path) as image:
                        image.verify()
                except (OSError, ValueError):
                    return _json_error("올바른 이미지 파일이 아닙니다.")
            database.add_equipment(name, category, quantity, temp_path)
            database.bump_data_version()
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        return jsonify(ok=True, message=f"[{name}] 장비가 등록되었습니다.")

    @app.delete("/api/equipment/<int:equipment_id>")
    def delete_equipment(equipment_id):
        ok, message = database.delete_equipment(equipment_id)
        if not ok:
            return _json_error(message, 409)
        database.bump_data_version()
        return jsonify(ok=True, message=message)

    @app.post("/api/renters")
    def add_renter():
        body = request.get_json(silent=True) or {}
        user_id = str(body.get("user_id", "")).strip()
        name = str(body.get("name", "")).strip()
        phone = str(body.get("phone", "")).strip()
        if not user_id or not name:
            return _json_error("아이디와 이름을 입력하세요.")
        ok, message = database.add_renter(user_id, name, phone)
        if not ok:
            return _json_error(message, 409)
        database.bump_data_version()
        return jsonify(ok=True, message=message)

    @app.delete("/api/renters/<int:renter_id>")
    def delete_renter(renter_id):
        ok, message = database.delete_renter(renter_id)
        if not ok:
            return _json_error(message, 409)
        database.bump_data_version()
        return jsonify(ok=True, message=message)

    @app.get("/api/stats")
    def stats():
        try:
            year, start_date, end_date, equipment_id = _parse_stats_filters()
        except ValueError as exc:
            return _json_error(str(exc))

        detail_start = start_date or f"{year}-01-01"
        detail_end = end_date or f"{year}-12-31"
        monthly = (
            database.monthly_stats_by_equipment(year, equipment_id)
            if equipment_id is not None
            else database.monthly_stats(year)
        )
        years = sorted(
            set(database.stat_years()) | {str(date.today().year), year}, reverse=True
        )
        return jsonify(
            years=years,
            selected_year=year,
            monthly=monthly,
            yearly=database.yearly_stats(),
            equipment_totals=_rows(database.equipment_totals()),
            equipment_items=_rows(database.equipment_stat_items()),
            details=_rows(
                database.list_rental_details(
                    detail_start, detail_end, equipment_id
                )
            ),
        )

    @app.post("/api/stats/override")
    def stats_override():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode", "save"))
        if mode == "reset":
            count = database.reset_stat_overrides()
            database.bump_data_version()
            return jsonify(ok=True, message=f"통계 조정 {count}건을 초기화했습니다.")

        try:
            equipment_id = int(body.get("equipment_id"))
            rent_count = int(body.get("rent_count", 0))
            return_count = int(body.get("return_count", 0))
        except (TypeError, ValueError):
            return _json_error("장비와 건수 입력을 확인하세요.")
        stat_month = str(body.get("stat_month", "")).strip()
        try:
            parsed_month = date.fromisoformat(f"{stat_month}-01")
            if parsed_month.isoformat()[:7] != stat_month:
                raise ValueError
        except ValueError:
            return _json_error("통계 월은 YYYY-MM 형식으로 입력하세요.")

        if mode == "add":
            ok, message = database.add_stat_override(
                equipment_id, stat_month, rent_count, return_count
            )
        elif mode == "remove":
            ok, message = database.remove_stat_override(equipment_id, stat_month)
        elif mode == "save":
            ok, message = database.save_stat_override(
                equipment_id, stat_month, rent_count, return_count
            )
        else:
            return _json_error("지원하지 않는 통계 조정 방식입니다.")
        if not ok:
            return _json_error(message, 409)
        database.bump_data_version()
        return jsonify(ok=True, message=message)

    @app.get("/api/stats/export")
    def export_stats():
        try:
            year, start_date, end_date, equipment_id = _parse_stats_filters()
        except ValueError as exc:
            return _json_error(str(exc))
        file_format = request.args.get("format", "xlsx").lower()
        if file_format not in ("xlsx", "pdf"):
            return _json_error("출력 형식은 xlsx 또는 pdf여야 합니다.")

        detail_start = start_date or f"{year}-01-01"
        detail_end = end_date or f"{year}-12-31"
        rentals = database.list_rental_details(
            detail_start, detail_end, equipment_id
        )
        rows = [
            (
                row["id"],
                row["rent_date"],
                row["equipment_name"],
                row["user_id"],
                row["renter_name"] or "-",
                row["renter_phone"] or "-",
                row["due_date"] or "-",
                row["return_date"] or "대여 중",
            )
            for row in rentals
        ]
        equipment_name = _equipment_name(equipment_id)
        details = (
            ("조회 기간", f"{detail_start} ~ {detail_end}"),
            ("장비", equipment_name),
            ("조회 건수", f"{len(rows)}건"),
            ("집계 기준", "실제 대여 이력"),
            ("출력일", date.today().isoformat()),
        )
        tables = [
            (
                "대여 상세 내역",
                (
                    "번호",
                    "대여일시",
                    "장비명",
                    "대여자 아이디",
                    "이름",
                    "연락처",
                    "반납 예정일",
                    "반납일시 / 상태",
                ),
                rows,
            )
        ]
        suffix = ".xlsx" if file_format == "xlsx" else ".pdf"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        output_path = handle.name
        handle.close()
        try:
            exporter = (
                stats_export.export_excel
                if file_format == "xlsx"
                else stats_export.export_pdf
            )
            exporter(output_path, "장비 대여 상세 내역", details, tables)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            if os.path.exists(output_path):
                os.remove(output_path)
            return _json_error(str(exc), 500)

        def remove_export():
            try:
                os.remove(output_path)
            except OSError:
                pass

        safe_equipment = secure_filename(equipment_name) or "all"
        filename = f"rental-detail-{safe_equipment}-{detail_start}-{detail_end}{suffix}"
        response = send_file(
            output_path, as_attachment=True, download_name=filename
        )
        response.call_on_close(remove_export)
        return response

    @app.errorhandler(413)
    def upload_too_large(_error):
        return _json_error("이미지는 12MB 이하만 업로드할 수 있습니다.", 413)

    return app


def local_addresses(port, scheme="http"):
    addresses = []

    def add_address(address):
        url = f"{scheme}://{address}:{port}"
        if url not in addresses:
            addresses.append(url)

    add_address("127.0.0.1")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            add_address(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if not address.startswith("127."):
                add_address(address)
    except OSError:
        pass
    return addresses


def server_is_running(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return response.status == 200 and b'"ok":true' in response.read()
    except (OSError, URLError):
        return False


def main():
    host = os.environ.get("ERM_HOST", "0.0.0.0")
    port = int(os.environ.get("ERM_PORT", "8765"))
    cert_file = os.environ.get("ERM_CERT_FILE", "").strip()
    key_file = os.environ.get("ERM_KEY_FILE", "").strip()
    ssl_context = (cert_file, key_file) if cert_file and key_file else None
    scheme = "https" if ssl_context else "http"
    if ssl_context is None and server_is_running(port):
        webbrowser.open(f"http://127.0.0.1:{port}")
        return

    database.init_db()
    app = create_app()
    addresses = local_addresses(port, scheme)
    print("장비 대여 관리 서버가 시작되었습니다.")
    print(f"모바일 연결 PIN: {database.get_access_pin()}")
    for address in addresses:
        print(f"  {address}")
    if os.environ.get("ERM_NO_BROWSER") != "1":
        threading.Timer(1, lambda: webbrowser.open(addresses[0])).start()
    app.run(
        host=host,
        port=port,
        threaded=True,
        use_reloader=False,
        ssl_context=ssl_context,
    )


if __name__ == "__main__":
    main()
