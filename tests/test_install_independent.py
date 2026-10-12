import contextlib
import io
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from tools import install_independent as installer


class InstallDeliveryTests(unittest.TestCase):
    def test_usb_delivers_only_app_and_cleans_its_mapping(self):
        if not (installer.ROOT / "standalone/index.html").exists():
            self.skipTest("Build standalone app first")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        calls = []

        def adb(_executable, *arguments):
            calls.append(arguments)
            if arguments == ("-d", "get-serialno"):
                return "test-phone"
            if arguments[-1] == "get-state":
                return "device"
            return ""

        def finish(_prompt):
            with urlopen(f"http://localhost:{port}/") as response:
                self.assertIn(b'content="independent"', response.read())
            for path in ("rental.db", "api/snapshot", "static/"):
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"http://localhost:{port}/{path}")
                self.assertEqual(error.exception.code, 404)
            return ""

        with patch("sys.argv", ["install", "--port", str(port)]), patch.object(installer, "find_adb", return_value="adb"), patch.object(installer, "adb_run", side_effect=adb), patch("builtins.input", side_effect=finish), contextlib.redirect_stdout(io.StringIO()):
            installer.main()
        self.assertIn(("-s", "test-phone", "reverse", "--no-rebind", f"tcp:{port}", f"tcp:{port}"), calls)
        self.assertEqual(calls[-1], ("-s", "test-phone", "reverse", "--remove", f"tcp:{port}"))
        with socket.socket() as probe:
            probe.settimeout(1)
            self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_missing_usb_device_does_not_start_delivery(self):
        with patch("sys.argv", ["install"]), patch.object(installer, "find_adb", return_value="adb"), patch.object(installer, "adb_run", side_effect=RuntimeError("no devices")), patch.object(installer, "ThreadingHTTPServer") as server:
            with self.assertRaisesRegex(RuntimeError, "USB"):
                installer.main()
            server.assert_not_called()
