import unittest

from tools.usb_broker import UsbBroker


class UsbBrokerTests(unittest.TestCase):
    def submit(self, broker, role):
        return broker.submit({"role": role, "client_id": role, "backup": {"app": "erm-independent", "data": {"equipment": []}}})

    def test_completion_requires_two_acknowledgements_and_releases_data(self):
        broker = UsbBroker()
        first = self.submit(broker, "pc")
        self.assertEqual(first["stage"], "waiting")
        self.assertNotIn("phone", first)
        self.assertEqual(self.submit(broker, "phone")["stage"], "ready")
        result = broker.acknowledge({"round": first["round"], "client_id": "pc", "ok": True})
        self.assertEqual(result["stage"], "ready")
        result = broker.acknowledge({"round": first["round"], "client_id": "phone", "ok": True})
        self.assertEqual(result["stage"], "complete")
        self.assertTrue(all(item["backup"] is None for item in broker.submissions.values()))

    def test_retry_rejects_old_round_and_cannot_reuse_stale_ready_payload(self):
        broker = UsbBroker()
        first = self.submit(broker, "pc")
        self.submit(broker, "phone")
        with self.assertRaises(ValueError):
            self.submit(broker, "pc")
        self.assertEqual(broker.stage, "failed")
        fresh = self.submit(broker, "pc")
        self.assertNotEqual(first["round"], fresh["round"])
        with self.assertRaises(ValueError):
            broker.acknowledge({"round": first["round"], "client_id": "phone", "ok": True})
        self.assertEqual(broker.stage, "waiting")

    def test_failure_cannot_be_overridden_by_other_devices_success(self):
        broker = UsbBroker()
        first = self.submit(broker, "pc")
        self.submit(broker, "phone")
        broker.acknowledge({"round": first["round"], "client_id": "phone", "ok": False, "error": "changed"})
        result = broker.acknowledge({"round": first["round"], "client_id": "pc", "ok": True})
        self.assertEqual(result["stage"], "failed")
        self.assertEqual(result["error"], "changed")
