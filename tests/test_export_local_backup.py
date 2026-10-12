import base64
import json
from pathlib import Path
import subprocess
import sys

from test_web_sync import TemporaryDatabaseTestCase
import database


class LegacyExportTests(TemporaryDatabaseTestCase):
    def test_export_preserves_source_and_embeds_photos(self):
        equipment_id, renter_id = self.add_equipment_and_renter()
        image = Path(database.IMAGE_DIR) / "sample.png"
        image.write_bytes((Path(__file__).resolve().parents[1] / "static/icon-192.png").read_bytes())
        with database.db() as conn:
            conn.execute("UPDATE equipment SET image_path=? WHERE id=?", (str(image), equipment_id))
        source = Path(database.DB_PATH)
        before = source.read_bytes()
        output = Path(self.temp_dir.name) / "backup.json"
        script = Path(__file__).resolve().parents[1] / "tools/export_local_backup.py"
        subprocess.run([sys.executable, str(script), "--database", str(source), "--output", str(output)], check=True, capture_output=True)
        backup = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(backup["app"], "erm-independent")
        self.assertEqual(backup["data"]["renters"][0]["id"], renter_id)
        encoded = backup["data"]["equipment"][0]["image_url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(encoded), image.read_bytes())
        self.assertEqual(source.read_bytes(), before)
        overwrite = subprocess.run([sys.executable, str(script), "--database", str(source), "--output", str(output)], capture_output=True)
        self.assertNotEqual(overwrite.returncode, 0)
