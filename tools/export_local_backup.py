"""Read the legacy SQLite database without modifying it; export a portable backup."""
import argparse
import base64
from datetime import datetime
import json
import mimetypes
from pathlib import Path
import sqlite3
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parents[1] / "rental.db")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = args.database.resolve()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = lambda table: [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        equipment, renters, rentals, overrides = (rows(table) for table in ("equipment", "renter", "rental", "stat_override"))
    for item in equipment:
        image_path = item.pop("image_path", "")
        item["image_url"] = ""
        if image_path:
            path = source.parent / "images" / Path(image_path).name
            if not path.is_file():
                raise FileNotFoundError(f"Image missing: {path}. Restore it before exporting.")
            mime = mimetypes.guess_type(path.name)[0]
            if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}:
                raise ValueError(f"Unsupported image: {path}")
            item["image_url"] = f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    equipment_ids, renter_ids = {}, {}
    for kind, items in (("equipment", equipment), ("renters", renters), ("rentals", rentals), ("overrides", overrides)):
        for item in items:
            item["_sync"] = {"id": str(uuid.uuid4()), "rev": str(uuid.uuid4()), "parents": []}
            if kind == "equipment":
                equipment_ids[item["id"]] = item["_sync"]["id"]
            if kind == "renters":
                renter_ids[item["id"]] = item["_sync"]["id"]
    for item in rentals + overrides:
        item["_equipment_uid"] = equipment_ids.setdefault(item["equipment_id"], str(uuid.uuid4()))
    for item in rentals:
        item["_renter_uid"] = renter_ids.setdefault(item["renter_id"], str(uuid.uuid4()))
    backup = {"app": "erm-independent", "exported_at": datetime.now().isoformat(), "data": {
        "schema": 1, "database_id": str(uuid.uuid4()), "version": 0,
        "equipment": equipment, "renters": renters, "rentals": rentals, "overrides": overrides,
        "sync_equipment_ids": equipment_ids, "sync_renter_ids": renter_ids, "sync_tombstones": [],
    }}
    # Refuse to overwrite an existing backup.
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(backup, output, ensure_ascii=False)
    print(f"Backup written: {args.output}")


if __name__ == "__main__":
    main()
