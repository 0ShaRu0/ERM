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


def yearly_stats():
    with db() as conn:
        rents = dict(
            conn.execute(
                "SELECT substr(rent_date,1,4) y, COUNT(*) c FROM rental GROUP BY y"
            ).fetchall()
        )
        rets = dict(
            conn.execute(
                """SELECT substr(return_date,1,4) y, COUNT(*) c FROM rental
                   WHERE return_date IS NOT NULL GROUP BY y"""
            ).fetchall()
        )
    years = sorted(set(rents) | set(rets), reverse=True)
    return [(y, rents.get(y, 0), rets.get(y, 0)) for y in years]


def monthly_stats(year):
    with db() as conn:
        rents = dict(
            conn.execute(
                """SELECT CAST(substr(rent_date,6,2) AS INT) m, COUNT(*) c
                   FROM rental WHERE substr(rent_date,1,4)=? GROUP BY m""",
                (year,),
            ).fetchall()
        )
        rets = dict(
            conn.execute(
                """SELECT CAST(substr(return_date,6,2) AS INT) m, COUNT(*) c
                   FROM rental
                   WHERE return_date IS NOT NULL AND substr(return_date,1,4)=?
                   GROUP BY m""",
                (year,),
            ).fetchall()
        )
    return [(m, rents.get(m, 0), rets.get(m, 0)) for m in range(1, 13)]


def stat_years():
    with db() as conn:
        rows = conn.execute(
            """SELECT substr(rent_date,1,4) y FROM rental
               UNION
               SELECT substr(return_date,1,4) FROM rental
               WHERE return_date IS NOT NULL
               ORDER BY y DESC"""
        ).fetchall()
    return [r[0] for r in rows]


def equipment_stat_items():
    with db() as conn:
        return conn.execute(
            """WITH stat_items AS (
                   SELECT equipment_id, equipment_name FROM rental
                   GROUP BY equipment_id, equipment_name
                   UNION
                   SELECT id, name FROM equipment
               )
               SELECT stat_items.equipment_id,
                      stat_items.equipment_name,
                      equipment.id IS NOT NULL AS is_current,
                      (SELECT COUNT(*) FROM equipment numbered
                       WHERE numbered.id <= stat_items.equipment_id)
                          AS display_number
               FROM stat_items
               LEFT JOIN equipment
                 ON equipment.id = stat_items.equipment_id
               ORDER BY stat_items.equipment_name, stat_items.equipment_id"""
        ).fetchall()


def equipment_totals():
    with db() as conn:
        return conn.execute(
            """SELECT rental.equipment_id,
                       rental.equipment_name,
                       equipment.id IS NOT NULL AS is_current,
                       (SELECT COUNT(*) FROM equipment numbered
                        WHERE numbered.id <= rental.equipment_id)
                           AS display_number,
                       COUNT(*) AS rent_count,
                       SUM(CASE WHEN return_date IS NULL THEN 1 ELSE 0 END)
                           AS active_count,
                       MAX(rent_date) AS last_rent
               FROM rental
               LEFT JOIN equipment ON equipment.id = rental.equipment_id
               GROUP BY rental.equipment_id, rental.equipment_name
               ORDER BY rent_count DESC, rental.equipment_name,
                        rental.equipment_id"""
        ).fetchall()


def monthly_stats_by_equipment(year, equipment_id):
    with db() as conn:
        rents = dict(
            conn.execute(
                """SELECT CAST(substr(rent_date,6,2) AS INT) m, COUNT(*) c
                    FROM rental
                    WHERE substr(rent_date,1,4)=? AND equipment_id=?
                    GROUP BY m""",
                (year, equipment_id),
            ).fetchall()
        )
        rets = dict(
            conn.execute(
                """SELECT CAST(substr(return_date,6,2) AS INT) m, COUNT(*) c
                    FROM rental
                    WHERE return_date IS NOT NULL
                      AND substr(return_date,1,4)=? AND equipment_id=?
                    GROUP BY m""",
                (year, equipment_id),
            ).fetchall()
        )
    return [(m, rents.get(m, 0), rets.get(m, 0)) for m in range(1, 13)]
