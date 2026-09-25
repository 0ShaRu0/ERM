import os
import tempfile
import unittest

import database
import stats_export


class RentalDetailTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        self.original_image_dir = database.IMAGE_DIR
        database.DB_PATH = os.path.join(self.temp_dir.name, "rental.db")
        database.IMAGE_DIR = os.path.join(self.temp_dir.name, "images")
        database.init_db()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        database.IMAGE_DIR = self.original_image_dir
        self.temp_dir.cleanup()

    def _add_equipment(self, name, quantity=5):
        database.add_equipment(name, "테스트", quantity)
        with database.db() as conn:
            return conn.execute(
                "SELECT id FROM equipment WHERE name = ?", (name,)
            ).fetchone()["id"]

    def _add_renter(self, user_id, name, phone):
        ok, _ = database.add_renter(user_id, name, phone)
        self.assertTrue(ok)
        with database.db() as conn:
            return conn.execute(
                "SELECT id FROM renter WHERE user_id = ?", (user_id,)
            ).fetchone()["id"]

    def _add_rental(self, equipment_id, renter_id, rent_date):
        ok, _ = database.add_rental(equipment_id, renter_id)
        self.assertTrue(ok)
        with database.db() as conn:
            rental_id = conn.execute("SELECT MAX(id) FROM rental").fetchone()[0]
            conn.execute(
                "UPDATE rental SET rent_date = ? WHERE id = ?",
                (rent_date, rental_id),
            )
        return rental_id

    def test_filters_by_inclusive_date_range_and_equipment(self):
        camera_id = self._add_equipment("카메라")
        tripod_id = self._add_equipment("삼각대")
        kim_id = self._add_renter("user01", "김민수", "010-1111-2222")
        lee_id = self._add_renter("user02", "이영희", "010-3333-4444")

        first_id = self._add_rental(
            camera_id, kim_id, "2026-09-01 00:00:00"
        )
        second_id = self._add_rental(
            camera_id, lee_id, "2026-09-01 23:59:59"
        )
        third_id = self._add_rental(
            tripod_id, kim_id, "2026-09-02 00:00:00"
        )

        rows = database.list_rental_details("2026-09-01", "2026-09-01")
        self.assertEqual([row["id"] for row in rows], [second_id, first_id])
        self.assertEqual(rows[0]["renter_name"], "이영희")
        self.assertEqual(rows[0]["renter_phone"], "010-3333-4444")

        rows = database.list_rental_details(start_date="2026-09-02")
        self.assertEqual([row["id"] for row in rows], [third_id])

        rows = database.list_rental_details(end_date="2026-09-01")
        self.assertEqual([row["id"] for row in rows], [second_id, first_id])

        rows = database.list_rental_details(equipment_id=camera_id)
        self.assertEqual([row["id"] for row in rows], [second_id, first_id])

        rows = database.list_rental_details(
            "2026-09-01", "2026-09-01", tripod_id
        )
        self.assertEqual(rows, [])

        rows = database.list_rental_details(end_date="9999-12-31")
        self.assertEqual(
            [row["id"] for row in rows], [third_id, second_id, first_id]
        )

    def test_snapshot_survives_renter_deletion(self):
        equipment_id = self._add_equipment("무전기", quantity=1)
        renter_id = self._add_renter(
            "radio01", "박지훈", "010-5555-6666"
        )
        rental_id = self._add_rental(
            equipment_id, renter_id, "2026-09-03 10:30:00"
        )

        ok, _ = database.return_rental(rental_id)
        self.assertTrue(ok)
        ok, _ = database.delete_renter(renter_id)
        self.assertTrue(ok)
        ok, _ = database.delete_equipment(equipment_id)
        self.assertTrue(ok)

        row = database.list_rental_details(equipment_id=equipment_id)[0]
        self.assertEqual(row["user_id"], "radio01")
        self.assertEqual(row["renter_name"], "박지훈")
        self.assertEqual(row["renter_phone"], "010-5555-6666")

        item = next(
            row
            for row in database.rental_equipment_items()
            if row["equipment_id"] == equipment_id
        )
        self.assertEqual(item["equipment_name"], "무전기")
        self.assertEqual(item["is_current"], 0)

    def test_legacy_database_migration_backfills_snapshot_once(self):
        equipment_id = self._add_equipment("노트북", quantity=1)
        renter_id = self._add_renter(
            "legacy01", "기존 사용자", "010-7777-8888"
        )
        with database.db() as conn:
            conn.execute("DROP TABLE rental")
            conn.execute(
                """CREATE TABLE rental (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       equipment_id INTEGER NOT NULL,
                       equipment_name TEXT NOT NULL,
                       renter_id INTEGER NOT NULL,
                       user_id TEXT NOT NULL,
                       rent_date TEXT NOT NULL,
                       due_date TEXT NOT NULL DEFAULT '',
                       return_date TEXT
                   )"""
            )
            conn.execute(
                """INSERT INTO rental
                   (equipment_id, equipment_name, renter_id, user_id, rent_date)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    equipment_id,
                    "노트북",
                    renter_id,
                    "legacy01",
                    "2026-08-20 09:00:00",
                ),
            )

        database.init_db()
        row = database.list_rental_details()[0]
        self.assertEqual(row["renter_name"], "기존 사용자")
        self.assertEqual(row["renter_phone"], "010-7777-8888")

        with database.db() as conn:
            conn.execute(
                "UPDATE renter SET phone = ? WHERE id = ?",
                ("010-0000-0000", renter_id),
            )
        database.init_db()
        row = database.list_rental_details()[0]
        self.assertEqual(row["renter_phone"], "010-7777-8888")

        with database.db() as conn:
            conn.execute(
                "UPDATE rental SET renter_name = '', renter_phone = ''"
            )
        database.init_db()
        row = database.list_rental_details()[0]
        self.assertEqual(row["renter_name"], "기존 사용자")
        self.assertEqual(row["renter_phone"], "010-0000-0000")

        with database.db() as conn:
            indexes = {
                row["name"] for row in conn.execute("PRAGMA index_list(rental)")
            }
        self.assertIn("idx_rental_equipment_rent_date", indexes)

    def test_excel_formula_like_values_are_written_as_text(self):
        self.assertEqual(stats_export._excel_value("=1+1"), "'=1+1")
        self.assertEqual(stats_export._excel_value("user01"), "user01")
        self.assertEqual(stats_export._excel_value(3), 3)


if __name__ == "__main__":
    unittest.main()
