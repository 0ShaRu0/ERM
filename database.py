import json
import os
import secrets
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "rental.db")
IMAGE_DIR = os.path.join(BASE_DIR, "images")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def db():
    conn = get_conn()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    os.makedirs(IMAGE_DIR, exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS equipment (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT '대여 가능',
                image_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS renter (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS rental (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL,
                equipment_name TEXT NOT NULL,
                renter_id INTEGER NOT NULL,
                user_id TEXT NOT NULL,
                renter_name TEXT NOT NULL DEFAULT '',
                renter_phone TEXT NOT NULL DEFAULT '',
                rent_date TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                due_date TEXT NOT NULL DEFAULT '',
                return_date TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_rental_rent_date ON rental(rent_date);
            CREATE INDEX IF NOT EXISTS idx_rental_return_date ON rental(return_date);
            CREATE INDEX IF NOT EXISTS idx_rental_equipment_rent_date
                ON rental(equipment_id, rent_date);

            CREATE TABLE IF NOT EXISTS stat_override (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                equipment_id INTEGER NOT NULL,
                equipment_name TEXT NOT NULL,
                stat_month TEXT NOT NULL,
                rent_count INTEGER NOT NULL DEFAULT 0 CHECK (rent_count >= 0),
                return_count INTEGER NOT NULL DEFAULT 0 CHECK (return_count >= 0),
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(equipment_id, stat_month)
            );

            CREATE INDEX IF NOT EXISTS idx_stat_override_month
                ON stat_override(stat_month);
            CREATE INDEX IF NOT EXISTS idx_stat_override_equipment
                ON stat_override(equipment_id, stat_month);

            CREATE TABLE IF NOT EXISTS sync_operation (
                operation_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                operation_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                entity_id INTEGER,
                processed_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_sync_operation_client
                ON sync_operation(client_id, processed_at);

            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            INSERT OR IGNORE INTO app_meta (key, value)
            VALUES ('data_version', '1');
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('database_id', ?)",
            (str(uuid.uuid4()),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_meta (key, value) VALUES ('access_pin', ?)",
            (f"{secrets.randbelow(1000000):06d}",),
        )
        conn.execute(
            """DELETE FROM sync_operation
               WHERE processed_at < datetime('now', '-365 days')"""
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(equipment)")
        }
        if "quantity" not in columns:
            conn.execute(
                "ALTER TABLE equipment ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1"
            )
        rental_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(rental)")
        }
        added_renter_name = "renter_name" not in rental_columns
        added_renter_phone = "renter_phone" not in rental_columns
        if added_renter_name:
            conn.execute(
                "ALTER TABLE rental ADD COLUMN renter_name TEXT NOT NULL DEFAULT ''"
            )
        if added_renter_phone:
            conn.execute(
                "ALTER TABLE rental ADD COLUMN renter_phone TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            """UPDATE rental
               SET renter_name = COALESCE(
                       (SELECT name FROM renter WHERE renter.id = rental.renter_id),
                       renter_name
                   ),
                   renter_phone = COALESCE(
                       (SELECT phone FROM renter WHERE renter.id = rental.renter_id),
                       renter_phone
                   )
               WHERE renter_name = ''"""
        )
        conn.execute(
            """UPDATE equipment
               SET status = CASE
                   WHEN (SELECT COUNT(*) FROM rental
                         WHERE equipment_id = equipment.id
                           AND return_date IS NULL) >= quantity
                   THEN '대여중'
                   ELSE '대여 가능'
               END"""
        )


def save_image(src_path):
    ext = os.path.splitext(src_path)[1].lower()
    filename = uuid.uuid4().hex + ext
    dest = os.path.join(IMAGE_DIR, filename)
    shutil.copy2(src_path, dest)
    return dest


def delete_image(image_path):
    if image_path and os.path.exists(image_path):
        try:
            os.remove(image_path)
        except OSError:
            pass


def add_equipment(name, category, quantity, image_src=None):
    image_path = save_image(image_src) if image_src else ""
    with db() as conn:
        conn.execute(
            """INSERT INTO equipment (name, category, quantity, image_path)
               VALUES (?, ?, ?, ?)""",
            (name, category, quantity, image_path),
        )


def list_equipment(keyword=""):
    sql = """SELECT e.*,
                    (SELECT COUNT(*) FROM equipment numbered
                     WHERE numbered.id <= e.id) AS display_number,
                    COUNT(r.id) AS active_count,
                    e.quantity - COUNT(r.id) AS available_count
             FROM equipment e
             LEFT JOIN rental r
               ON r.equipment_id = e.id AND r.return_date IS NULL"""
    params = ()
    if keyword:
        sql += " WHERE e.name LIKE ? OR e.category LIKE ?"
        params = (f"%{keyword}%", f"%{keyword}%")
    sql += " GROUP BY e.id ORDER BY e.id DESC"
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def get_equipment(equipment_id):
    with db() as conn:
        return conn.execute(
            """SELECT e.*,
                      (SELECT COUNT(*) FROM equipment numbered
                       WHERE numbered.id <= e.id) AS display_number,
                      COUNT(r.id) AS active_count,
                      e.quantity - COUNT(r.id) AS available_count
               FROM equipment e
               LEFT JOIN rental r
                 ON r.equipment_id = e.id AND r.return_date IS NULL
               WHERE e.id = ?
               GROUP BY e.id""",
            (equipment_id,),
        ).fetchone()


def delete_equipment(equipment_id):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        eq = conn.execute(
            """SELECT equipment.*,
                      (SELECT COUNT(*) FROM rental
                       WHERE rental.equipment_id = equipment.id
                         AND rental.return_date IS NULL) AS active_count
               FROM equipment WHERE id = ?""",
            (equipment_id,),
        ).fetchone()
        if not eq:
            return False, "장비를 찾을 수 없습니다."
        if eq["active_count"]:
            return False, "대여 중인 장비는 삭제할 수 없습니다."
        conn.execute("DELETE FROM equipment WHERE id = ?", (equipment_id,))
    delete_image(eq["image_path"])
    return True, f"[{eq['name']}] 장비가 삭제되었습니다."


def add_renter(user_id, name, phone=""):
    with db() as conn:
        exists = conn.execute(
            "SELECT 1 FROM renter WHERE user_id = ?", (user_id,)
        ).fetchone()
        if exists:
            return False, "이미 존재하는 아이디입니다."
        conn.execute(
            "INSERT INTO renter (user_id, name, phone) VALUES (?, ?, ?)",
            (user_id, name, phone),
        )
    return True, f"[{user_id}] 대여자가 등록되었습니다."


def list_renters(keyword=""):
    sql = "SELECT * FROM renter"
    params = ()
    if keyword:
        sql += " WHERE user_id LIKE ? OR name LIKE ? OR phone LIKE ?"
        params = (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%")
    sql += " ORDER BY id"
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def delete_renter(renter_id):
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rn = conn.execute("SELECT * FROM renter WHERE id = ?", (renter_id,)).fetchone()
        if not rn:
            return False, "대여자를 찾을 수 없습니다."
        active = conn.execute(
            "SELECT 1 FROM rental WHERE renter_id = ? AND return_date IS NULL",
            (renter_id,),
        ).fetchone()
        if active:
            return False, "반납하지 않은 장비가 있어 삭제할 수 없습니다."
        conn.execute("DELETE FROM renter WHERE id = ?", (renter_id,))
    return True, f"[{rn['user_id']}] 대여자가 삭제되었습니다."


def add_rental(equipment_id, renter_id, due_date=""):
    with db() as conn:
        eq = conn.execute(
            "SELECT * FROM equipment WHERE id = ?", (equipment_id,)
        ).fetchone()
        if not eq:
            return False, "장비를 찾을 수 없습니다."
        active_count = conn.execute(
            """SELECT COUNT(*) FROM rental
               WHERE equipment_id = ? AND return_date IS NULL""",
            (equipment_id,),
        ).fetchone()[0]
        if active_count >= eq["quantity"]:
            return False, "대여 가능한 장비가 없습니다."
        rn = conn.execute(
            "SELECT * FROM renter WHERE id = ?", (renter_id,)
        ).fetchone()
        if not rn:
            return False, "대여자를 찾을 수 없습니다."
        conn.execute(
            """INSERT INTO rental
               (equipment_id, equipment_name, renter_id, user_id,
                renter_name, renter_phone, due_date)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                eq["id"],
                eq["name"],
                rn["id"],
                rn["user_id"],
                rn["name"],
                rn["phone"],
                due_date,
            ),
        )
        conn.execute(
            "UPDATE equipment SET status = ? WHERE id = ?",
            (
                "대여중" if active_count + 1 >= eq["quantity"] else "대여 가능",
                eq["id"],
            ),
        )
    available = eq["quantity"] - active_count - 1
    return (
        True,
        f"[{eq['name']}] → [{rn['user_id']}] 대여 처리되었습니다. "
        f"(잔여 {available}개)",
    )


def return_rental(rental_id):
    with db() as conn:
        r = conn.execute(
            "SELECT * FROM rental WHERE id = ?", (rental_id,)
        ).fetchone()
        if not r or r["return_date"]:
            return False, "대여 기록을 찾을 수 없습니다."
        conn.execute(
            "UPDATE rental SET return_date = datetime('now','localtime') WHERE id = ?",
            (rental_id,),
        )
        eq = conn.execute(
            "SELECT quantity FROM equipment WHERE id = ?", (r["equipment_id"],)
        ).fetchone()
        active_count = conn.execute(
            """SELECT COUNT(*) FROM rental
               WHERE equipment_id = ? AND return_date IS NULL""",
            (r["equipment_id"],),
        ).fetchone()[0]
        conn.execute(
            "UPDATE equipment SET status = ? WHERE id = ?",
            (
                "대여중" if active_count >= eq["quantity"] else "대여 가능",
                r["equipment_id"],
            ),
        )
    available = eq["quantity"] - active_count
    return True, f"[{r['equipment_name']}] 반납 처리되었습니다. (잔여 {available}개)"


def list_rentals(active_only=True, limit=500):
    sql = "SELECT * FROM rental"
    if active_only:
        sql += " WHERE return_date IS NULL"
    sql += " ORDER BY rent_date DESC, id DESC LIMIT ?"
    with db() as conn:
        return conn.execute(sql, (limit,)).fetchall()


def rental_equipment_items():
    with db() as conn:
        return conn.execute(
            """WITH rented_equipment AS (
                   SELECT equipment_id, MAX(equipment_name) AS equipment_name
                   FROM rental
                   GROUP BY equipment_id
               )
               SELECT equipment_id, equipment_name, is_current, display_number
               FROM (
                   SELECT equipment.id AS equipment_id,
                          equipment.name AS equipment_name,
                          1 AS is_current,
                          (SELECT COUNT(*) FROM equipment numbered
                           WHERE numbered.id <= equipment.id) AS display_number
                   FROM equipment
                   UNION ALL
                   SELECT rented_equipment.equipment_id,
                          rented_equipment.equipment_name,
                          0 AS is_current,
                          NULL AS display_number
                   FROM rented_equipment
                   LEFT JOIN equipment
                     ON equipment.id = rented_equipment.equipment_id
                   WHERE equipment.id IS NULL
               )
               ORDER BY equipment_name, equipment_id"""
        ).fetchall()


def list_rental_details(start_date="", end_date="", equipment_id=None):
    sql = """SELECT id,
                    equipment_id,
                    equipment_name,
                    renter_id,
                    user_id,
                    renter_name,
                    renter_phone,
                    rent_date,
                    due_date,
                    return_date
             FROM rental
             WHERE 1 = 1"""
    params = []
    if start_date:
        start = date.fromisoformat(start_date).isoformat()
        sql += " AND rent_date >= ?"
        params.append(f"{start} 00:00:00")
    if end_date:
        end = date.fromisoformat(end_date)
        if end == date.max:
            end_exclusive = "9999-12-32 00:00:00"
        else:
            end_exclusive = f"{(end + timedelta(days=1)).isoformat()} 00:00:00"
        sql += " AND rent_date < ?"
        params.append(end_exclusive)
    if equipment_id is not None:
        sql += " AND equipment_id = ?"
        params.append(equipment_id)
    sql += " ORDER BY rent_date DESC, id DESC"
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def get_active_rental(equipment_id):
    rentals = list_active_rentals(equipment_id)
    return rentals[0] if rentals else None


def list_active_rentals(equipment_id):
    with db() as conn:
        return conn.execute(
            """SELECT rental.*
               FROM rental
               WHERE rental.equipment_id = ? AND rental.return_date IS NULL
               ORDER BY rental.rent_date DESC, rental.id DESC""",
            (equipment_id,),
        ).fetchall()


def get_data_version():
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'data_version'"
        ).fetchone()
    return int(row["value"]) if row else 1


def get_database_id():
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'database_id'"
        ).fetchone()
    return row["value"] if row else ""


def get_access_pin():
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'access_pin'"
        ).fetchone()
    return row["value"] if row else ""


def snapshot_rows():
    with db() as conn:
        conn.execute("BEGIN")
        version = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'data_version'"
        ).fetchone()
        database_id = conn.execute(
            "SELECT value FROM app_meta WHERE key = 'database_id'"
        ).fetchone()
        equipment = conn.execute(
            """SELECT e.*,
                      (SELECT COUNT(*) FROM equipment numbered
                       WHERE numbered.id <= e.id) AS display_number,
                      COUNT(r.id) AS active_count,
                      e.quantity - COUNT(r.id) AS available_count
               FROM equipment e
               LEFT JOIN rental r
                 ON r.equipment_id = e.id AND r.return_date IS NULL
               GROUP BY e.id ORDER BY e.id DESC"""
        ).fetchall()
        renters = conn.execute("SELECT * FROM renter ORDER BY id").fetchall()
        active_rentals = conn.execute(
            """SELECT * FROM rental WHERE return_date IS NULL
               ORDER BY rent_date DESC, id DESC"""
        ).fetchall()
        recent_rentals = conn.execute(
            """SELECT * FROM rental
               ORDER BY rent_date DESC, id DESC LIMIT 500"""
        ).fetchall()
    return {
        "version": int(version["value"]) if version else 1,
        "database_id": database_id["value"] if database_id else "",
        "equipment": equipment,
        "renters": renters,
        "active_rentals": active_rentals,
        "recent_rentals": recent_rentals,
    }


def _bump_data_version(conn):
    conn.execute(
        """INSERT INTO app_meta (key, value) VALUES ('data_version', '1')
           ON CONFLICT(key) DO UPDATE
           SET value = CAST(value AS INTEGER) + 1"""
    )


def bump_data_version():
    with db() as conn:
        _bump_data_version(conn)


def _normalize_event_time(value):
    if not value:
        return datetime.now().replace(microsecond=0).isoformat(sep=" ")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    parsed = parsed.replace(microsecond=0)
    now = datetime.now().replace(microsecond=0)
    if parsed > now + timedelta(days=1):
        raise ValueError("작업 시각이 현재보다 너무 미래입니다.")
    if parsed < now - timedelta(days=365):
        raise ValueError("1년이 지난 오프라인 작업은 자동 반영할 수 없습니다.")
    return parsed.isoformat(sep=" ")


def _resolve_synced_rental_id(conn, rental_id):
    if isinstance(rental_id, str) and rental_id.startswith("local:"):
        operation_id = rental_id.split(":", 1)[1]
        source = conn.execute(
            """SELECT entity_id FROM sync_operation
               WHERE operation_id = ?
                 AND operation_type = 'rent'
                 AND status = 'applied'""",
            (operation_id,),
        ).fetchone()
        if not source or source["entity_id"] is None:
            raise ValueError("연결된 오프라인 대여 작업을 찾을 수 없습니다.")
        return source["entity_id"]
    return int(rental_id)


def _apply_rent_operation(conn, payload, occurred_at):
    equipment_id = int(payload["equipment_id"])
    renter_id = int(payload["renter_id"])
    due_date = str(payload.get("due_date", "")).strip()
    if due_date:
        date.fromisoformat(due_date)

    equipment = conn.execute(
        "SELECT * FROM equipment WHERE id = ?", (equipment_id,)
    ).fetchone()
    if not equipment:
        raise ValueError("장비를 찾을 수 없습니다.")
    renter = conn.execute(
        "SELECT * FROM renter WHERE id = ?", (renter_id,)
    ).fetchone()
    if not renter:
        raise ValueError("대여자를 찾을 수 없습니다.")
    active_count = conn.execute(
        """SELECT COUNT(*) FROM rental
           WHERE equipment_id = ? AND return_date IS NULL""",
        (equipment_id,),
    ).fetchone()[0]
    if active_count >= equipment["quantity"]:
        raise ValueError("대여 가능한 장비가 없습니다.")

    cursor = conn.execute(
        """INSERT INTO rental
           (equipment_id, equipment_name, renter_id, user_id,
            renter_name, renter_phone, rent_date, due_date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            equipment_id,
            equipment["name"],
            renter_id,
            renter["user_id"],
            renter["name"],
            renter["phone"],
            occurred_at,
            due_date,
        ),
    )
    available = equipment["quantity"] - active_count - 1
    conn.execute(
        "UPDATE equipment SET status = ? WHERE id = ?",
        ("대여중" if available == 0 else "대여 가능", equipment_id),
    )
    return cursor.lastrowid, f"[{equipment['name']}] 대여가 반영되었습니다."


def _apply_return_operation(conn, payload, occurred_at):
    rental_id = _resolve_synced_rental_id(conn, payload["rental_id"])
    rental = conn.execute(
        "SELECT * FROM rental WHERE id = ?", (rental_id,)
    ).fetchone()
    if not rental:
        raise ValueError("대여 기록을 찾을 수 없습니다.")
    if rental["return_date"]:
        raise ValueError("이미 반납된 대여 기록입니다.")
    if occurred_at < rental["rent_date"]:
        raise ValueError("반납 시각은 대여 시각보다 빠를 수 없습니다.")

    equipment = conn.execute(
        "SELECT * FROM equipment WHERE id = ?", (rental["equipment_id"],)
    ).fetchone()
    if not equipment:
        raise ValueError("장비가 삭제되어 반납 상태를 갱신할 수 없습니다.")
    conn.execute(
        "UPDATE rental SET return_date = ? WHERE id = ?",
        (occurred_at, rental_id),
    )
    active_count = conn.execute(
        """SELECT COUNT(*) FROM rental
           WHERE equipment_id = ? AND return_date IS NULL""",
        (rental["equipment_id"],),
    ).fetchone()[0]
    conn.execute(
        "UPDATE equipment SET status = ? WHERE id = ?",
        (
            "대여중" if active_count >= equipment["quantity"] else "대여 가능",
            rental["equipment_id"],
        ),
    )
    return rental_id, f"[{rental['equipment_name']}] 반납이 반영되었습니다."


def apply_sync_operations(client_id, operations):
    results = []
    for operation in operations:
        if not isinstance(operation, dict):
            results.append(
                {
                    "operation_id": "",
                    "status": "conflict",
                    "message": "동기화 작업 형식이 올바르지 않습니다.",
                    "entity_id": None,
                }
            )
            continue
        operation_id = str(operation.get("operation_id", "")).strip()
        operation_type = str(operation.get("type", "")).strip()
        payload = operation.get("payload") or {}
        if not operation_id:
            results.append(
                {
                    "operation_id": "",
                    "status": "conflict",
                    "message": "작업 고유번호가 없습니다.",
                    "entity_id": None,
                }
            )
            continue

        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """SELECT operation_id, status, message, entity_id
                   FROM sync_operation WHERE operation_id = ?""",
                (operation_id,),
            ).fetchone()
            if existing:
                results.append(dict(existing))
                continue

            entity_id = None
            status = "applied"
            occurred_at = datetime.now().replace(microsecond=0).isoformat(sep=" ")
            try:
                expected_database_id = conn.execute(
                    "SELECT value FROM app_meta WHERE key = 'database_id'"
                ).fetchone()["value"]
                if operation.get("database_id") != expected_database_id:
                    raise ValueError(
                        "이 작업은 다른 중앙 데이터베이스에서 생성되었습니다."
                    )
                occurred_at = _normalize_event_time(operation.get("occurred_at"))
                if operation_type == "rent":
                    entity_id, message = _apply_rent_operation(
                        conn, payload, occurred_at
                    )
                elif operation_type == "return":
                    entity_id, message = _apply_return_operation(
                        conn, payload, occurred_at
                    )
                else:
                    raise ValueError("지원하지 않는 동기화 작업입니다.")
            except (KeyError, OverflowError, TypeError, ValueError) as exc:
                status = "conflict"
                message = str(exc) or "작업을 반영할 수 없습니다."

            conn.execute(
                """INSERT INTO sync_operation
                   (operation_id, client_id, operation_type, payload,
                    occurred_at, status, message, entity_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation_id,
                    client_id,
                    operation_type,
                    json.dumps(payload, ensure_ascii=False),
                    occurred_at,
                    status,
                    message,
                    entity_id,
                ),
            )
            if status == "applied":
                _bump_data_version(conn)
            results.append(
                {
                    "operation_id": operation_id,
                    "status": status,
                    "message": message,
                    "entity_id": entity_id,
                }
            )
    return results



def _effective_monthly_cte():
    return """
        WITH rent_months AS (
            SELECT equipment_id,
                   MAX(equipment_name) AS equipment_name,
                   substr(rent_date,1,7) AS stat_month,
                   COUNT(*) AS rent_count
            FROM rental
            GROUP BY equipment_id, stat_month
        ),
        return_months AS (
            SELECT equipment_id,
                   MAX(equipment_name) AS equipment_name,
                   substr(return_date,1,7) AS stat_month,
                   COUNT(*) AS return_count
            FROM rental
            WHERE return_date IS NOT NULL
            GROUP BY equipment_id, stat_month
        ),
        base_monthly AS (
            SELECT COALESCE(rent_months.equipment_id, return_months.equipment_id)
                       AS equipment_id,
                   COALESCE(rent_months.equipment_name, return_months.equipment_name)
                       AS equipment_name,
                   COALESCE(rent_months.stat_month, return_months.stat_month)
                       AS stat_month,
                   COALESCE(rent_months.rent_count, 0) AS base_rent_count,
                   COALESCE(return_months.return_count, 0) AS base_return_count
            FROM rent_months
            LEFT JOIN return_months
              ON return_months.equipment_id = rent_months.equipment_id
             AND return_months.stat_month = rent_months.stat_month
            UNION ALL
            SELECT return_months.equipment_id,
                   return_months.equipment_name,
                   return_months.stat_month,
                   0 AS base_rent_count,
                   return_months.return_count AS base_return_count
            FROM return_months
            LEFT JOIN rent_months
              ON rent_months.equipment_id = return_months.equipment_id
             AND rent_months.stat_month = return_months.stat_month
            WHERE rent_months.equipment_id IS NULL
        ),
        monthly_keys AS (
            SELECT equipment_id, stat_month FROM base_monthly
            UNION
            SELECT equipment_id, stat_month FROM stat_override
        ),
        stat_names AS (
            SELECT monthly_keys.equipment_id,
                   COALESCE(
                       equipment.name,
                       (SELECT rental.equipment_name FROM rental
                        WHERE rental.equipment_id = monthly_keys.equipment_id
                        ORDER BY rental.rent_date DESC, rental.id DESC LIMIT 1),
                       (SELECT stat_override.equipment_name FROM stat_override
                        WHERE stat_override.equipment_id = monthly_keys.equipment_id
                        ORDER BY stat_override.updated_at DESC,
                                 stat_override.id DESC LIMIT 1)
                   ) AS equipment_name
            FROM monthly_keys
            LEFT JOIN equipment ON equipment.id = monthly_keys.equipment_id
            GROUP BY monthly_keys.equipment_id
        ),
        effective_monthly AS (
            SELECT monthly_keys.equipment_id,
                   stat_names.equipment_name,
                   monthly_keys.stat_month,
                   base_monthly.equipment_id IS NOT NULL AS base_exists,
                   COALESCE(base_monthly.base_rent_count, 0) AS base_rent_count,
                   COALESCE(base_monthly.base_return_count, 0)
                       AS base_return_count,
                   stat_override.rent_count AS override_rent_count,
                   stat_override.return_count AS override_return_count,
                   COALESCE(stat_override.rent_count,
                            base_monthly.base_rent_count, 0) AS rent_count,
                   COALESCE(stat_override.return_count,
                            base_monthly.base_return_count, 0) AS return_count,
                   CASE
                       WHEN stat_override.id IS NULL THEN '자동'
                       WHEN stat_override.rent_count = 0
                        AND stat_override.return_count = 0 THEN '제거됨'
                       ELSE '수정됨'
                   END AS source_status,
                   equipment.id IS NOT NULL AS is_current,
                   (SELECT COUNT(*) FROM equipment numbered
                    WHERE numbered.id <= monthly_keys.equipment_id)
                       AS display_number
            FROM monthly_keys
            LEFT JOIN base_monthly
              ON base_monthly.equipment_id = monthly_keys.equipment_id
             AND base_monthly.stat_month = monthly_keys.stat_month
            LEFT JOIN stat_override
              ON stat_override.equipment_id = monthly_keys.equipment_id
             AND stat_override.stat_month = monthly_keys.stat_month
            LEFT JOIN stat_names
              ON stat_names.equipment_id = monthly_keys.equipment_id
            LEFT JOIN equipment
              ON equipment.id = monthly_keys.equipment_id
        )
    """


def _base_monthly_counts(conn, equipment_id, stat_month):
    row = conn.execute(
        _effective_monthly_cte()
        + """SELECT base_exists, base_rent_count, base_return_count
             FROM effective_monthly
             WHERE equipment_id = ? AND stat_month = ?""",
        (equipment_id, stat_month),
    ).fetchone()
    if not row or not row["base_exists"]:
        return None
    return row["base_rent_count"], row["base_return_count"]


def _stat_equipment_name(conn, equipment_id):
    row = conn.execute(
        "SELECT name FROM equipment WHERE id = ?", (equipment_id,)
    ).fetchone()
    if row:
        return row["name"]
    row = conn.execute(
        """SELECT equipment_name FROM rental
           WHERE equipment_id = ?
           ORDER BY rent_date DESC, id DESC LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    if row:
        return row["equipment_name"]
    row = conn.execute(
        """SELECT equipment_name FROM stat_override
           WHERE equipment_id = ?
           ORDER BY updated_at DESC, id DESC LIMIT 1""",
        (equipment_id,),
    ).fetchone()
    return row["equipment_name"] if row else None


def list_effective_monthly_stats():
    with db() as conn:
        return conn.execute(
            _effective_monthly_cte()
            + """SELECT * FROM effective_monthly
                 ORDER BY stat_month DESC, equipment_name, equipment_id"""
        ).fetchall()


def add_stat_override(equipment_id, stat_month, rent_count, return_count):
    if rent_count < 0 or return_count < 0:
        return False, "건수는 0 이상의 숫자로 입력하세요."
    if rent_count == 0 and return_count == 0:
        return False, "대여 또는 반납 건수를 1건 이상 입력하세요."
    with db() as conn:
        exists = conn.execute(
            _effective_monthly_cte()
            + """SELECT 1 FROM effective_monthly
                 WHERE equipment_id = ? AND stat_month = ?""",
            (equipment_id, stat_month),
        ).fetchone()
        if exists:
            return False, "이미 존재하는 통계 월입니다. 변경 저장을 사용하세요."
        name = _stat_equipment_name(conn, equipment_id)
        if not name:
            return False, "장비를 찾을 수 없습니다."
        conn.execute(
            """INSERT INTO stat_override
               (equipment_id, equipment_name, stat_month, rent_count, return_count)
               VALUES (?, ?, ?, ?, ?)""",
            (equipment_id, name, stat_month, rent_count, return_count),
        )
    return True, "통계 월이 추가되었습니다."


def save_stat_override(equipment_id, stat_month, rent_count, return_count):
    if rent_count < 0 or return_count < 0:
        return False, "건수는 0 이상의 숫자로 입력하세요."
    with db() as conn:
        base_counts = _base_monthly_counts(conn, equipment_id, stat_month)
        existing = conn.execute(
            """SELECT 1 FROM stat_override
               WHERE equipment_id = ? AND stat_month = ?""",
            (equipment_id, stat_month),
        ).fetchone()
        if base_counts is None and not existing:
            return False, "저장할 통계를 찾을 수 없습니다."
        if base_counts == (rent_count, return_count):
            conn.execute(
                """DELETE FROM stat_override
                   WHERE equipment_id = ? AND stat_month = ?""",
                (equipment_id, stat_month),
            )
            return True, "자동 통계로 복원되었습니다."
        name = _stat_equipment_name(conn, equipment_id)
        if not name:
            return False, "장비를 찾을 수 없습니다."
        conn.execute(
            """INSERT INTO stat_override
               (equipment_id, equipment_name, stat_month, rent_count, return_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(equipment_id, stat_month) DO UPDATE SET
                   equipment_name = excluded.equipment_name,
                   rent_count = excluded.rent_count,
                   return_count = excluded.return_count,
                   updated_at = datetime('now','localtime')""",
            (equipment_id, name, stat_month, rent_count, return_count),
        )
    return True, "통계가 저장되었습니다."


def remove_stat_override(equipment_id, stat_month):
    with db() as conn:
        base_counts = _base_monthly_counts(conn, equipment_id, stat_month)
        if base_counts is None:
            existing = conn.execute(
                """SELECT 1 FROM stat_override
                   WHERE equipment_id = ? AND stat_month = ?""",
                (equipment_id, stat_month),
            ).fetchone()
            if not existing:
                return False, "제거할 통계를 찾을 수 없습니다."
            conn.execute(
                """DELETE FROM stat_override
                   WHERE equipment_id = ? AND stat_month = ?""",
                (equipment_id, stat_month),
            )
            return True, "추가한 통계 월이 제거되었습니다."
        name = _stat_equipment_name(conn, equipment_id)
        if not name:
            return False, "장비를 찾을 수 없습니다."
        conn.execute(
            """INSERT INTO stat_override
               (equipment_id, equipment_name, stat_month, rent_count, return_count)
               VALUES (?, ?, ?, 0, 0)
               ON CONFLICT(equipment_id, stat_month) DO UPDATE SET
                   equipment_name = excluded.equipment_name,
                   rent_count = 0,
                   return_count = 0,
                   updated_at = datetime('now','localtime')""",
            (equipment_id, name, stat_month),
        )
    return True, "자동 통계가 제거됨으로 표시되었습니다."


def reset_stat_overrides():
    with db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM stat_override").fetchone()[0]
        conn.execute("DELETE FROM stat_override")
    return count


def yearly_stats():
    with db() as conn:
        rows = conn.execute(
            _effective_monthly_cte()
            + """SELECT substr(stat_month,1,4) AS year,
                        SUM(rent_count) AS rent_count,
                        SUM(return_count) AS return_count
                 FROM effective_monthly
                 GROUP BY year
                 HAVING SUM(effective_monthly.rent_count) > 0
                    OR SUM(effective_monthly.return_count) > 0
                 ORDER BY year DESC"""
        ).fetchall()
    return [(row["year"], row["rent_count"], row["return_count"]) for row in rows]


def monthly_stats(year):
    with db() as conn:
        rows = conn.execute(
            _effective_monthly_cte()
            + """SELECT CAST(substr(stat_month,6,2) AS INT) AS month,
                        SUM(rent_count) AS rent_count,
                        SUM(return_count) AS return_count
                 FROM effective_monthly
                 WHERE substr(stat_month,1,4) = ?
                 GROUP BY month""",
            (year,),
        ).fetchall()
    stats = {row["month"]: row for row in rows}
    return [
        (m, stats[m]["rent_count"], stats[m]["return_count"])
        if m in stats else (m, 0, 0)
        for m in range(1, 13)
    ]


def stat_years():
    with db() as conn:
        rows = conn.execute(
            _effective_monthly_cte()
            + """SELECT substr(stat_month,1,4) AS year
                 FROM effective_monthly
                 GROUP BY year
                 HAVING SUM(effective_monthly.rent_count) > 0
                    OR SUM(effective_monthly.return_count) > 0
                 ORDER BY year DESC"""
        ).fetchall()
    return [r["year"] for r in rows]


def equipment_stat_items():
    with db() as conn:
        return conn.execute(
            _effective_monthly_cte()
            + """SELECT equipment_id,
                        equipment_name,
                        is_current,
                        display_number
                 FROM effective_monthly
                 GROUP BY equipment_id, equipment_name
                 UNION
                 SELECT id,
                        name,
                        1 AS is_current,
                        (SELECT COUNT(*) FROM equipment numbered
                         WHERE numbered.id <= equipment.id) AS display_number
                 FROM equipment
                 ORDER BY equipment_name, equipment_id"""
        ).fetchall()


def equipment_totals():
    with db() as conn:
        return conn.execute(
            _effective_monthly_cte()
            + """SELECT effective_monthly.equipment_id,
                        effective_monthly.equipment_name,
                        effective_monthly.is_current,
                        effective_monthly.display_number,
                        SUM(effective_monthly.rent_count) AS rent_count,
                        SUM(effective_monthly.return_count) AS return_count,
                        (SELECT COUNT(*) FROM rental
                         WHERE rental.equipment_id = effective_monthly.equipment_id
                           AND rental.return_date IS NULL) AS active_count,
                        MAX(CASE WHEN effective_monthly.rent_count > 0
                                 THEN effective_monthly.stat_month END) AS last_rent
                 FROM effective_monthly
                 GROUP BY effective_monthly.equipment_id,
                          effective_monthly.equipment_name
                 HAVING SUM(effective_monthly.rent_count) > 0
                    OR SUM(effective_monthly.return_count) > 0
                    OR (SELECT COUNT(*) FROM rental
                        WHERE rental.equipment_id = effective_monthly.equipment_id
                          AND rental.return_date IS NULL) > 0
                 ORDER BY rent_count DESC, equipment_name, equipment_id"""
        ).fetchall()


def monthly_stats_by_equipment(year, equipment_id):
    with db() as conn:
        rows = conn.execute(
            _effective_monthly_cte()
            + """SELECT CAST(substr(stat_month,6,2) AS INT) AS month,
                        rent_count,
                        return_count
                 FROM effective_monthly
                 WHERE substr(stat_month,1,4) = ? AND equipment_id = ?""",
            (year, equipment_id),
        ).fetchall()
    stats = {row["month"]: row for row in rows}
    return [
        (m, stats[m]["rent_count"], stats[m]["return_count"])
        if m in stats else (m, 0, 0)
        for m in range(1, 13)
    ]
