"""ERM native app hub. Independent of the legacy web database after import."""
import argparse
import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request

KINDS = {"equipment", "renter", "rental", "override"}


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, revision INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices(
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    admin INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY, device TEXT NOT NULL, request TEXT NOT NULL,
                    result TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('workspace', ?)", (str(uuid.uuid4()),))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @property
    def workspace(self):
        with self.db() as c:
            return c.execute("SELECT value FROM meta WHERE key='workspace'").fetchone()[0]

    def register(self, name, admin=False, device_id=None):
        token = secrets.token_urlsafe(32)
        device = str(uuid.UUID(device_id)) if device_id else str(uuid.uuid4())
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            if admin:
                previous = c.execute("SELECT id FROM devices WHERE admin=1").fetchone()
                if previous:
                    device = previous["id"]
            elif c.execute("SELECT 1 FROM devices WHERE id=? AND admin=1", (device,)).fetchone():
                raise Conflict("기준 PC의 식별자로 휴대폰을 등록할 수 없습니다.")
            c.execute("""INSERT INTO devices(id,name,token_hash,admin) VALUES(?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                         token_hash=excluded.token_hash, revoked=0""",
                      (device, str(name)[:100], hashlib.sha256(token.encode()).hexdigest(), int(admin)))
        return {"device_id": device, "token": token, "workspace": self.workspace}

    def authenticate(self, token):
        with self.db() as c:
            row = c.execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0",
                            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def snapshot(c):
        return [dict(r) | {"data": json.loads(r["data"])}
                for r in c.execute("SELECT * FROM records ORDER BY id")]

    @staticmethod
    def record(c, record_id, kind=None):
        row = c.execute("SELECT * FROM records WHERE id=? AND deleted=0", (record_id,)).fetchone()
        if not row or (kind and row["kind"] != kind):
            raise Conflict("연결된 기록이 없거나 삭제되었습니다.")
        return dict(row) | {"data": json.loads(row["data"])}

    @staticmethod
    def active(c):
        return [json.loads(r[0]) for r in c.execute(
            "SELECT data FROM records WHERE kind='rental' AND deleted=0")
                if not json.loads(r[0]).get("return_date")]

    @staticmethod
    def write(c, record_id, kind, revision, data, deleted=False):
        c.execute("""INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                     revision=excluded.revision, deleted=excluded.deleted, data=excluded.data""",
                  (record_id, kind, revision, int(deleted), json.dumps(data, ensure_ascii=False)))

    @staticmethod
    def text(data, field):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 500:
            raise Conflict(f"{field}: 필수 값이 없거나 너무 깁니다.")
        return value.strip()

    @staticmethod
    def timestamp(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(timezone.utc)

    def apply(self, c, op):
        if not isinstance(op.get("data"), dict):
            raise Conflict("작업 데이터는 JSON 객체여야 합니다.")
        record_id = str(uuid.UUID(op["entity_id"]))
        kind, action = op["kind"], op["action"]
        if kind not in KINDS or action not in {"put", "delete", "return"}:
            raise Conflict("지원하지 않는 작업입니다.")
        old = c.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        revision = old["revision"] if old else 0
        if old and old["kind"] != kind:
            raise Conflict("기록 종류가 일치하지 않습니다.")
        if action == "return":
            current = self.record(c, record_id, "rental")
            if current["data"].get("return_date"):
                return  # Returning the same rental on two devices is idempotent.
            data = current["data"]
            returned = self.timestamp(op["data"]["return_date"])
            if returned < self.timestamp(data["rent_date"]):
                raise Conflict("반납 시각은 대여 시각보다 빠를 수 없습니다.")
            data["return_date"] = returned.isoformat()
            self.write(c, record_id, kind, revision + 1, data)
            return
        if op.get("base_revision") != revision:
            raise Conflict("다른 기기가 먼저 수정했습니다. 최신 내용과 비교해 다시 저장하세요.")
        if old and old["deleted"] and not (kind == "override" and action == "put"):
            raise Conflict("삭제된 기록은 덮어쓸 수 없습니다.")
        if action == "delete":
            if not old or kind == "rental":
                raise Conflict("삭제할 수 없는 기록입니다.")
            reference = "equipment_id" if kind == "equipment" else "renter_id"
            if kind in {"equipment", "renter"} and any(
                    r[reference] == record_id for r in self.active(c)):
                raise Conflict("대여 중인 기록이 있어 삭제할 수 없습니다.")
            self.write(c, record_id, kind, revision + 1, json.loads(old["data"]), True)
            return
        data = op["data"].copy()
        if kind == "equipment":
            data["name"] = self.text(data, "name")
            data["category"] = self.text(data, "category")
            quantity = data.get("quantity")
            if type(quantity) is not int or not 1 <= quantity <= 9999:
                raise Conflict("장비 수량은 1~9999 사이의 정수여야 합니다.")
            if quantity < sum(r["equipment_id"] == record_id for r in self.active(c)):
                raise Conflict("대여 중인 수량보다 적게 변경할 수 없습니다.")
            photo = data.get("photo", "")
            if not isinstance(photo, str) or len(photo) > 4 * 1024 * 1024:
                raise Conflict("사진은 3MB 이하로 등록하세요.")
            if photo:
                base64.b64decode(photo, validate=True)
        elif kind == "renter":
            for field in ("name", "user_id"):
                data[field] = self.text(data, field)
            for r in c.execute("SELECT id,data FROM records WHERE kind='renter' AND deleted=0"):
                if r["id"] != record_id and json.loads(r["data"])["user_id"] == data["user_id"]:
                    raise Conflict("이미 사용 중인 대여자 아이디입니다.")
        elif kind == "rental":
            if old:
                raise Conflict("대여 기록은 수정 대신 반납 처리하세요.")
            equipment = self.record(c, data["equipment_id"], "equipment")["data"]
            renter = self.record(c, data["renter_id"], "renter")["data"]
            if sum(r["equipment_id"] == data["equipment_id"] for r in self.active(c)) >= equipment["quantity"]:
                raise Conflict("다른 기기의 대여로 남은 수량이 없습니다.")
            data["rent_date"] = self.timestamp(data["rent_date"]).isoformat()
            if data.get("due_date"):
                datetime.strptime(data["due_date"], "%Y-%m-%d")
            data.update(equipment_name=equipment["name"], renter_name=renter["name"],
                        user_id=renter["user_id"], renter_phone=renter.get("phone", ""), return_date="")
        elif kind == "override":
            self.text(data, "equipment_id")
            self.text(data, "equipment_name")
            datetime.strptime(data["stat_month"], "%Y-%m")
            for key in ("rent_count", "return_count"):
                if type(data.get(key)) is not int or data[key] < 0:
                    raise Conflict("통계 건수는 0 이상의 정수여야 합니다.")
            # Stable key ensures that concurrent first edits also conflict.
            expected = str(uuid.uuid5(uuid.UUID(self.workspace),
                                      f"override:{data['equipment_id']}:{data['stat_month']}"))
            if record_id != expected:
                raise Conflict("통계 식별자가 올바르지 않습니다.")
        self.write(c, record_id, kind, revision + 1, data)

    def sync(self, device, body):
        if body.get("workspace") != self.workspace:
            raise Conflict("다른 작업 공간입니다. 기존 대기 작업을 먼저 확인하세요.")
        ops = body.get("operations", [])
        if not isinstance(ops, list) or len(ops) > 100:
            raise Conflict("한 번에 최대 100개 작업을 전송할 수 있습니다.")
        results = []
        with self.db() as c:
            c.execute("BEGIN IMMEDIATE")
            for op in ops:
                if not isinstance(op, dict) or not isinstance(op.get("id"), str):
                    raise Conflict("작업 형식이 올바르지 않습니다.")
                encoded = json.dumps(op, sort_keys=True, ensure_ascii=False)
                previous = c.execute("SELECT * FROM operations WHERE id=?", (op["id"],)).fetchone()
                if previous:
                    if previous["device"] != device or previous["request"] != encoded:
                        raise Conflict("이미 사용된 작업 ID입니다.")
                    results.append(json.loads(previous["result"]))
                    continue
                c.execute("SAVEPOINT operation")
                try:
                    self.apply(c, op)
                    result = {"id": op["id"], "status": "applied", "message": "반영 완료"}
                except (ValueError, TypeError, KeyError, OverflowError) as exc:
                    c.execute("ROLLBACK TO operation")
                    result = {"id": op["id"], "status": "conflict", "message": str(exc)}
                finally:
                    c.execute("RELEASE operation")
                c.execute("INSERT INTO operations VALUES(?,?,?,?)",
                          (op["id"], device, encoded, json.dumps(result, ensure_ascii=False)))
                results.append(result)
            records = self.snapshot(c)
        return {"workspace": self.workspace, "results": results, "records": records}

    def import_legacy(self, source):
        """One-time read-only import, including all history (not just recent 500)."""
        source = Path(source).resolve()
        namespace = uuid.UUID(self.workspace)
        def identifier(kind, value):
            return str(uuid.uuid5(namespace, f"legacy:{kind}:{value}"))
        legacy = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        legacy.row_factory = sqlite3.Row
        try:
            legacy.execute("BEGIN")
            with self.db() as c:
                c.execute("BEGIN IMMEDIATE")
                if c.execute("SELECT 1 FROM records LIMIT 1").fetchone() or c.execute(
                        "SELECT 1 FROM meta WHERE key='legacy_import'").fetchone():
                    raise Conflict("기존 데이터 가져오기는 빈 작업 공간에서 한 번만 가능합니다.")
                for kind, table in (("equipment", "equipment"), ("renter", "renter"),
                                    ("rental", "rental"), ("override", "stat_override")):
                    if not legacy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                        continue
                    for row in legacy.execute(f"SELECT * FROM {table}"):
                        data = dict(row)
                        record_id = identifier(kind, data.pop("id"))
                        for field, target in (("equipment_id", "equipment"), ("renter_id", "renter")):
                            if field in data:
                                data[field] = identifier(target, data[field])
                        if kind == "equipment":
                            raw = data.pop("image_path", "")
                            image = source.parent / "images" / raw.replace("\\", "/").rsplit("/", 1)[-1]
                            data["photo"] = base64.b64encode(image.read_bytes()).decode() if raw and image.is_file() else ""
                        if kind == "rental":
                            for key in ("rent_date", "return_date"):
                                data[key] = self.timestamp(data[key]).isoformat() if data.get(key) else ""
                        if kind == "override":
                            record_id = str(uuid.uuid5(namespace, f"override:{data['equipment_id']}:{data['stat_month']}"))
                        self.write(c, record_id, kind, 1, data)
                c.execute("INSERT INTO meta VALUES('legacy_import',?)", (str(source),))
        finally:
            legacy.close()


def create_app(store):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 48 * 1024 * 1024
    pairing = {"code": "", "expires": 0, "attempts": 0}
    lock = threading.Lock()

    @app.before_request
    def auth():
        if request.path == "/v2/pair":
            return None
        request.device = store.authenticate(request.headers.get("Authorization", "").removeprefix("Bearer "))
        if not request.device:
            return jsonify(message="기기 인증이 만료되었거나 해제되었습니다."), 401

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Conflict)
    def conflict(exc):
        return jsonify(message=str(exc)), 409

    @app.post("/v2/pairing")
    def new_pairing():
        if not request.device["admin"]:
            return jsonify(message="기준 PC에서만 연결 코드를 발급할 수 있습니다."), 403
        with lock:
            pairing.update(code=secrets.token_hex(4).upper(), expires=time.time() + 180, attempts=0)
            return jsonify(code=pairing["code"], expires_in=180)

    @app.post("/v2/pair")
    def pair():
        body = request.get_json(silent=True) or {}
        with lock:
            pairing["attempts"] += 1
            if (pairing["expires"] < time.time() or pairing["attempts"] > 10 or
                    not secrets.compare_digest(str(body.get("code", "")), pairing["code"])):
                return jsonify(message="연결 코드가 잘못되었거나 만료되었습니다."), 401
            try:
                result = store.register(body.get("name", "Android"), device_id=body.get("device_id"))
            except (ValueError, TypeError, AttributeError) as exc:
                return jsonify(message=str(exc)), 400
            pairing["expires"] = 0
            return jsonify(result)

    @app.post("/v2/sync")
    def sync():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(message="JSON 객체가 필요합니다."), 400
        return jsonify(store.sync(request.device["id"], body))

    @app.get("/v2/devices")
    def devices():
        if not request.device["admin"]:
            return jsonify(message="관리자 권한이 필요합니다."), 403
        with store.db() as c:
            return jsonify(devices=[dict(r) for r in c.execute("SELECT id,name,revoked FROM devices WHERE admin=0")])

    @app.delete("/v2/devices/<device>")
    def revoke(device):
        if not request.device["admin"]:
            return jsonify(message="관리자 권한이 필요합니다."), 403
        with store.db() as c:
            c.execute("UPDATE devices SET revoked=1 WHERE id=? AND admin=0", (device,))
        return jsonify(ok=True)

    @app.post("/v2/import")
    def import_data():
        if not request.device["admin"]:
            return jsonify(message="관리자 권한이 필요합니다."), 403
        body = request.get_json(silent=True) or {}
        try:
            store.import_legacy(body["path"])
        except (OSError, sqlite3.Error, KeyError, ValueError) as exc:
            return jsonify(message=f"가져오기 실패: {exc}"), 409
        return jsonify(ok=True)

    return app


def certificate(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    cert_path, key_path = directory / "hub.crt", directory / "hub.key"
    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ERM local hub")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .sign(key, hashes.SHA256()))
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        os.chmod(key_path, 0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    return str(cert_path), str(key_path), cert.fingerprint(hashes.SHA256()).hex()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    directory = Path(args.data_dir)
    store = Store(directory / "hub.db")
    # Single running hub per data directory, including across Windows app instances.
    lock_file = open(directory / "hub.lock", "a+b")
    if os.name == "nt":
        import msvcrt
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    cert, key, fingerprint = certificate(directory)
    credentials = store.register("Windows 기준 PC", admin=True)
    from werkzeug.serving import make_server
    server = make_server("0.0.0.0", args.port, create_app(store), threaded=True, ssl_context=(cert, key))
    addresses = {"127.0.0.1"}
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    print(json.dumps(credentials | {"fingerprint": fingerprint,
          "url": f"https://127.0.0.1:{args.port}",
          "addresses": [f"https://{a}:{args.port}" for a in sorted(addresses)]}), flush=True)
    # Parent app owns the pipe. A crash or app exit closes it and stops the hub.
    def watch_parent():
        sys.stdin.read()
        server.shutdown()
    threading.Thread(target=watch_parent, daemon=True).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
