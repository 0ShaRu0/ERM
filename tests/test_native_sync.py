from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor

from native_service.server import Conflict, Store, certificate, create_app


class NativeSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "hub.db")
        self.admin = self.store.register("PC", admin=True)
        self.phone = self.store.register("Android")
        self.app = create_app(self.store)
        self.client = self.app.test_client()
        self.equipment = str(uuid.uuid4())
        self.renter = str(uuid.uuid4())

    def operation(self, kind, data, entity=None, revision=0, action="put"):
        return {"id": str(uuid.uuid4()), "entity_id": entity or str(uuid.uuid4()),
                "kind": kind, "action": action, "base_revision": revision, "data": data}

    def sync(self, ops, device=None):
        return self.store.sync((device or self.phone)["device_id"],
                               {"workspace": self.store.workspace, "operations": ops})

    def seed(self):
        return [self.operation("equipment", {"name": "카메라", "category": "영상", "quantity": 1}, self.equipment),
                self.operation("renter", {"name": "대여자", "user_id": "u1", "phone": "010"}, self.renter)]

    def rental(self):
        return self.operation("rental", {"equipment_id": self.equipment, "renter_id": self.renter,
                                        "rent_date": "2026-09-01T10:00:00+09:00", "due_date": "2026-09-30"})

    def headers(self, credentials=None):
        return {"Authorization": "Bearer " + (credentials or self.admin)["token"]}

    def test_offline_create_rent_return_in_order_and_replay_after_lost_response(self):
        rental = self.rental()
        returned = self.operation("rental", {"return_date": "2026-09-02T10:00:00+09:00"}, rental["entity_id"], 1, "return")
        ops = self.seed() + [rental, returned]
        first = self.sync(ops)
        self.assertTrue(all(r["status"] == "applied" for r in first["results"]))
        self.assertEqual(first, self.sync(ops))
        self.assertEqual(len(first["records"]), 3)
        # A second device can download the same canonical state.
        self.assertEqual(first["records"], self.sync([], self.admin)["records"])
        restarted = Store(self.store.path)
        self.assertEqual(first["records"], restarted.sync(self.phone["device_id"],
            {"workspace": restarted.workspace, "operations": []})["records"])

    def test_concurrent_last_item_has_one_winner(self):
        self.sync(self.seed())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.sync([self.rental()]), range(2)))
        self.assertEqual(sorted(r["results"][0]["status"] for r in results), ["applied", "conflict"])
        self.assertEqual(len([r for r in self.sync([])["records"] if r["kind"] == "rental"]), 1)

    def test_concurrent_edits_conflict_without_overwrite(self):
        self.sync(self.seed())
        a = self.operation("equipment", {"name": "PC 변경", "category": "영상", "quantity": 2}, self.equipment, 1)
        b = self.operation("equipment", {"name": "폰 변경", "category": "영상", "quantity": 3}, self.equipment, 1)
        self.assertEqual(self.sync([a])["results"][0]["status"], "applied")
        self.assertEqual(self.sync([b])["results"][0]["status"], "conflict")
        eq = next(r for r in self.sync([])["records"] if r["kind"] == "equipment")
        self.assertEqual(eq["data"]["name"], "PC 변경")

    def test_delete_tombstone_preserves_history_and_rejects_stale_edit(self):
        rental = self.rental()
        self.sync(self.seed() + [rental])
        deletion = self.operation("equipment", {}, self.equipment, 1, "delete")
        self.assertEqual(self.sync([deletion])["results"][0]["status"], "conflict")
        returned = self.operation("rental", {"return_date": "2026-09-02T10:00:00+09:00"}, rental["entity_id"], 1, "return")
        self.sync([returned])
        # Two independent return operations must not double-return the rental.
        self.assertEqual(self.sync([{**returned, "id": str(uuid.uuid4())}])["results"][0]["status"], "applied")
        self.assertEqual(self.sync([{**deletion, "id": str(uuid.uuid4())}])["results"][0]["status"], "applied")
        stale = self.operation("equipment", {"name": "복원", "category": "영상", "quantity": 1}, self.equipment, 2)
        self.assertEqual(self.sync([stale])["results"][0]["status"], "conflict")
        self.assertTrue(any(r["kind"] == "rental" and r["data"]["equipment_name"] == "카메라" for r in self.sync([])["records"]))

    def test_pairing_is_one_time_and_device_revocation_blocks_reads(self):
        self.assertEqual(self.client.post('/v2/sync', json={}).status_code, 401)
        self.assertEqual(self.client.post('/v2/pairing', headers=self.headers(self.phone)).status_code, 403)
        code = self.client.post('/v2/pairing', headers=self.headers()).json['code']
        self.assertEqual(self.client.post('/v2/pair', json={'code': 'wrong'}).status_code, 401)
        paired = self.client.post('/v2/pair', json={'code': code, 'name': 'new phone'})
        self.assertEqual(paired.status_code, 200)
        self.assertEqual(self.client.post('/v2/pair', json={'code': code}).status_code, 401)
        self.assertEqual(self.client.post('/v2/sync', headers=self.headers(paired.json),
            json={'workspace': self.store.workspace}).status_code, 200)
        self.client.delete('/v2/devices/' + paired.json['device_id'], headers=self.headers())
        self.assertEqual(self.client.post('/v2/sync', headers=self.headers(paired.json), json={}).status_code, 401)

    def test_wrong_workspace_and_cross_device_operation_replay_rejected(self):
        with self.assertRaises(Conflict):
            self.store.sync(self.phone['device_id'], {'workspace': 'other', 'operations': self.seed()})
        ops = self.seed()
        self.sync(ops)
        with self.assertRaises(Conflict):
            self.sync(ops, self.admin)
        with self.assertRaises(Conflict):
            self.sync([{**ops[0], 'data': {'quantity': 99}}])

    def test_token_rotation_preserves_operation_owner_after_restart_and_repair(self):
        ops = self.seed()
        first = self.sync(ops, self.admin)
        new_admin = self.store.register('Restarted PC', admin=True)
        self.assertEqual(new_admin['device_id'], self.admin['device_id'])
        self.assertIsNone(self.store.authenticate(self.admin['token']))
        self.assertEqual(first, self.sync(ops, new_admin))
        rental = self.rental()
        applied = self.sync([rental])
        new_phone = self.store.register('Re-paired phone', device_id=self.phone['device_id'])
        self.assertEqual(applied, self.sync([rental], new_phone))
        self.assertIsNone(self.store.authenticate(self.phone['token']))
        with self.assertRaises(Conflict):
            self.store.register('Invalid phone', device_id=new_admin['device_id'])

    def test_statistics_override_reset_and_reapply(self):
        self.sync(self.seed())
        key = str(uuid.uuid5(uuid.UUID(self.store.workspace), f'override:{self.equipment}:2026-09'))
        data = {'equipment_id': self.equipment, 'equipment_name': '카메라', 'stat_month': '2026-09',
                'rent_count': 10, 'return_count': 9}
        ops = [self.operation('override', data, key), self.operation('override', {}, key, 1, 'delete'),
               self.operation('override', {**data, 'rent_count': 12}, key, 2)]
        self.assertTrue(all(r['status'] == 'applied' for r in self.sync(ops)['results']))

    def test_invalid_payload_is_conflict_and_other_operations_continue(self):
        bad = self.operation('equipment', [], str(uuid.uuid4()))
        result = self.sync([bad] + self.seed())
        self.assertEqual([r['status'] for r in result['results']], ['conflict', 'applied', 'applied'])

    def test_import_is_read_only_includes_entire_history_and_is_one_time(self):
        source = Path(self.temp.name) / 'legacy.db'
        with closing(sqlite3.connect(source)) as c:
            c.executescript('''
                CREATE TABLE equipment(id INTEGER, name TEXT, category TEXT, quantity INTEGER, image_path TEXT);
                INSERT INTO equipment VALUES(1,'카메라','영상',2,'');
                CREATE TABLE renter(id INTEGER, name TEXT, user_id TEXT, phone TEXT);
                INSERT INTO renter VALUES(1,'사용자','a','010');
                CREATE TABLE rental(id INTEGER, equipment_id INTEGER, renter_id INTEGER,
                    equipment_name TEXT, rent_date TEXT, return_date TEXT);
            ''')
            c.executemany('INSERT INTO rental VALUES(?,1,1,?,?,?)',
                          [(i, '카메라', '2026-01-01 10:00:00', '2026-01-02 10:00:00') for i in range(1, 602)])
            c.commit()
        before = source.read_bytes()
        self.store.import_legacy(source)
        records = self.sync([])['records']
        self.assertEqual(len([r for r in records if r['kind'] == 'rental']), 601)
        self.assertEqual(source.read_bytes(), before)
        with self.assertRaises(Conflict):
            self.store.import_legacy(source)

    def test_certificate_fingerprint_is_persistent(self):
        first = certificate(Path(self.temp.name))
        second = certificate(Path(self.temp.name))
        self.assertEqual(first, second)
        self.assertEqual(len(first[2]), 64)


if __name__ == '__main__':
    unittest.main()
