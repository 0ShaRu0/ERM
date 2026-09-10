import os
import shutil
import sqlite3
import sys
import uuid
from contextlib import contextmanager

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "rental.db")
IMAGE_DIR = os.path.join(BASE_DIR, "images")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
                rent_date TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                due_date TEXT NOT NULL DEFAULT '',
                return_date TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_rental_rent_date ON rental(rent_date);
            CREATE INDEX IF NOT EXISTS idx_rental_return_date ON rental(return_date);

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
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(equipment)")
        }
        if "quantity" not in columns:
            conn.execute(
                "ALTER TABLE equipment ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1"
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
    eq = get_equipment(equipment_id)
    if not eq:
        return False, "장비를 찾을 수 없습니다."
    if eq["active_count"]:
        return False, "대여 중인 장비는 삭제할 수 없습니다."
    with db() as conn:
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
               (equipment_id, equipment_name, renter_id, user_id, due_date)
               VALUES (?, ?, ?, ?, ?)""",
            (eq["id"], eq["name"], rn["id"], rn["user_id"], due_date),
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


def get_active_rental(equipment_id):
    rentals = list_active_rentals(equipment_id)
    return rentals[0] if rentals else None


def list_active_rentals(equipment_id):
    with db() as conn:
        return conn.execute(
            """SELECT rental.*, renter.name AS renter_name
               FROM rental
               JOIN renter ON renter.id = rental.renter_id
               WHERE rental.equipment_id = ? AND rental.return_date IS NULL
               ORDER BY rental.rent_date DESC, rental.id DESC""",
            (equipment_id,),
        ).fetchall()



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
