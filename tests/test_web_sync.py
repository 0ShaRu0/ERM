import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import database

try:
    from web_app import create_app
except ModuleNotFoundError:
    create_app = None


class TemporaryDatabaseTestCase(unittest.TestCase):
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

    def add_equipment_and_renter(self, quantity=1):
        database.add_equipment("카메라", "영상", quantity)
        ok, _ = database.add_renter(
            "mobile01", "모바일 사용자", "010-1234-5678"
        )
        self.assertTrue(ok)
        return database.list_equipment()[0]["id"], database.list_renters()[0]["id"]


class SyncOperationTests(TemporaryDatabaseTestCase):
    def test_offline_operations_are_idempotent_and_return_local_rental(self):
        equipment_id, renter_id = self.add_equipment_and_renter()
        rent_operation = {
            "operation_id": "rent-operation-1",
            "type": "rent",
            "database_id": database.get_database_id(),
            "occurred_at": "2026-09-23T10:00:00+09:00",
            "payload": {
                "equipment_id": equipment_id,
                "renter_id": renter_id,
                "due_date": "2026-09-30",
            },
        }

        first = database.apply_sync_operations("phone-a", [rent_operation])[0]
        repeated = database.apply_sync_operations("phone-a", [rent_operation])[0]
        self.assertEqual(first["status"], "applied")
        self.assertEqual(repeated["entity_id"], first["entity_id"])
        self.assertEqual(len(database.list_rentals(active_only=False)), 1)

        conflict = database.apply_sync_operations(
            "phone-b",
            [
                {
                    **rent_operation,
                    "operation_id": "rent-operation-2",
                }
            ],
        )[0]
        self.assertEqual(conflict["status"], "conflict")
        self.assertIn("대여 가능한", conflict["message"])

        returned = database.apply_sync_operations(
            "phone-a",
            [
                {
                    "operation_id": "return-operation-1",
                    "type": "return",
                    "database_id": database.get_database_id(),
                    "occurred_at": "2026-09-24T11:00:00+09:00",
                    "payload": {"rental_id": "local:rent-operation-1"},
                }
            ],
        )[0]
        self.assertEqual(returned["status"], "applied")
        self.assertEqual(database.list_rentals(active_only=True), [])

    def test_invalid_operation_is_recorded_as_conflict(self):
        result = database.apply_sync_operations(
            "phone-a",
            [
                {
                    "operation_id": "invalid-operation",
                    "type": "unknown",
                    "database_id": database.get_database_id(),
                    "occurred_at": "invalid-date",
                    "payload": {},
                }
            ],
        )[0]
        self.assertEqual(result["status"], "conflict")
        repeated = database.apply_sync_operations(
            "phone-a",
            [
                {
                    "operation_id": "invalid-operation",
                    "type": "rent",
                    "database_id": database.get_database_id(),
                    "payload": {},
                }
            ],
        )[0]
        self.assertEqual(repeated["status"], "conflict")

    def test_equipment_delete_cannot_orphan_concurrent_rental(self):
        _, renter_id = self.add_equipment_and_renter()
        for index in range(10):
            name = f"동시성 장비 {index}"
            database.add_equipment(name, "테스트", 1)
            equipment_id = next(
                row["id"] for row in database.list_equipment() if row["name"] == name
            )
            barrier = threading.Barrier(2)

            def rent():
                barrier.wait()
                return database.apply_sync_operations(
                    "concurrent-phone",
                    [
                        {
                            "operation_id": f"concurrent-rent-{index}",
                            "type": "rent",
                            "database_id": database.get_database_id(),
                            "occurred_at": "2026-09-24T12:00:00+09:00",
                            "payload": {
                                "equipment_id": equipment_id,
                                "renter_id": renter_id,
                                "due_date": "",
                            },
                        }
                    ],
                )[0]

            def delete():
                barrier.wait()
                return database.delete_equipment(equipment_id)

            with ThreadPoolExecutor(max_workers=2) as executor:
                rent_result = executor.submit(rent)
                delete_result = executor.submit(delete)
                synced = rent_result.result()
                deleted, _ = delete_result.result()

            active = [
                row
                for row in database.list_rentals(active_only=True, limit=10000)
                if row["equipment_id"] == equipment_id
            ]
            equipment = database.get_equipment(equipment_id)
            self.assertFalse(active and equipment is None)
            if synced["status"] == "applied":
                self.assertIsNotNone(equipment)
                self.assertFalse(deleted)


@unittest.skipIf(create_app is None, "Flask is not installed")
class WebApiTests(TemporaryDatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.headers = {
            "X-ERM-Client": "web",
            "X-ERM-Key": database.get_access_pin(),
        }

    def test_mobile_api_flow(self):
        unauthorized = self.client.get("/api/snapshot")
        self.assertEqual(unauthorized.status_code, 401)
        forbidden = self.client.post(
            "/api/renters",
            json={"user_id": "x", "name": "차단"},
            headers={"X-ERM-Key": database.get_access_pin()},
        )
        self.assertEqual(forbidden.status_code, 403)

        equipment_response = self.client.post(
            "/api/equipment",
            data={"name": "무선 마이크", "category": "음향", "quantity": "1"},
            headers=self.headers,
        )
        self.assertEqual(equipment_response.status_code, 200)
        renter_response = self.client.post(
            "/api/renters",
            json={
                "user_id": "web01",
                "name": "웹 사용자",
                "phone": "010-9999-8888",
            },
            headers=self.headers,
        )
        self.assertEqual(renter_response.status_code, 200)

        snapshot = self.client.get(
            "/api/snapshot", headers=self.headers
        ).get_json()
        self.assertEqual(len(snapshot["equipment"]), 1)
        self.assertEqual(len(snapshot["renters"]), 1)
        self.assertTrue(snapshot["connection_urls"])

        sync_response = self.client.post(
            "/api/sync",
            json={
                "client_id": "api-phone",
                "operations": [
                    {
                        "operation_id": "api-rent-1",
                        "type": "rent",
                        "database_id": snapshot["database_id"],
                        "occurred_at": "2026-09-23T12:30:00+09:00",
                        "payload": {
                            "equipment_id": snapshot["equipment"][0]["id"],
                            "renter_id": snapshot["renters"][0]["id"],
                            "due_date": "2026-09-30",
                        },
                    }
                ],
            },
            headers=self.headers,
        )
        self.assertEqual(sync_response.status_code, 200)
        synced = sync_response.get_json()
        self.assertEqual(synced["results"][0]["status"], "applied")
        self.assertEqual(len(synced["snapshot"]["active_rentals"]), 1)

        stats = self.client.get(
            "/api/stats?year=2026&start_date=2026-09-23&end_date=2026-09-23",
            headers=self.headers,
        ).get_json()
        self.assertEqual(len(stats["details"]), 1)
        self.assertEqual(stats["details"][0]["renter_name"], "웹 사용자")

        excel = self.client.get(
            "/api/stats/export?format=xlsx&year=2026&start_date=2026-09-23&end_date=2026-09-23",
            headers=self.headers,
        )
        self.assertEqual(excel.status_code, 200)
        self.assertGreater(len(excel.data), 100)
        excel.close()

        pdf = self.client.get(
            "/api/stats/export?format=pdf&year=2026&start_date=2026-09-23&end_date=2026-09-23",
            headers=self.headers,
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        pdf.close()

        blocked_delete = self.client.delete(
            f"/api/equipment/{snapshot['equipment'][0]['id']}",
            headers=self.headers,
        )
        self.assertEqual(blocked_delete.status_code, 409)

        service_worker = self.client.get("/sw.js")
        self.assertEqual(service_worker.status_code, 200)
        self.assertEqual(service_worker.headers["Service-Worker-Allowed"], "/")
        service_worker.close()

        mobile_page = self.client.get(
            "/", environ_base={"REMOTE_ADDR": "192.168.0.20"}
        )
        self.assertEqual(mobile_page.status_code, 200)
        self.assertNotIn(database.get_access_pin().encode(), mobile_page.data)


if __name__ == "__main__":
    unittest.main()
