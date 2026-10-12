"""Two separate browser/device stores reconcile through the real USB HTTP broker."""
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.install_independent import AppFiles
from tools.usb_broker import UsbBroker


def main():
    from playwright.sync_api import sync_playwright, expect

    root = Path(__file__).resolve().parents[1]
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(AppFiles, directory=str(root / "standalone")))
    server.usb_broker = UsbBroker()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://localhost:{server.server_port}/"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            pc_context, phone_context = browser.new_context(), browser.new_context()
            pc, phone = pc_context.new_page(), phone_context.new_page()
            for page, role in [(pc, "pc"), (phone, "phone")]:
                page.goto(url + f"?usb_role={role}")
                expect(page.locator("[data-connection-label]").first).to_have_text("기기 저장 모드")
                page.wait_for_function("navigator.serviceWorker.controller !== null")
                page.evaluate("""async role => {
                  const equipment = new FormData();
                  equipment.set('name',role+' camera');equipment.set('category','camera');equipment.set('quantity','1');
                  await ERMLocal.request('/api/equipment',{method:'POST',body:equipment});
                  await ERMLocal.request('/api/renters',{method:'POST',body:JSON.stringify({user_id:role,name:role,phone:''})});
                }""", role)

            def synchronize(success=True):
                for page in [pc, phone]:
                    expect(page.locator("#usbSync")).to_be_enabled()
                pc.locator("#usbSync").click()
                expect(pc.locator("#usbSyncStatus")).to_contain_text("상대 기기를 기다립니다")
                phone.locator("#usbSync").click()
                for page in [pc, phone]:
                    expect(page.locator("#usbSyncStatus")).to_contain_text("동기화를 완료했습니다" if success else "동기화를 완료하지 못했습니다", timeout=20000)
                    expect(page.locator("#usbSync")).to_be_enabled()

            def data(page):
                return page.evaluate("async () => (await ERMLocal.backup()).data")

            def equivalent():
                first, second = data(pc), data(phone)
                first.pop("version"); second.pop("version")
                assert first == second
                return first

            synchronize()
            combined = equivalent()
            assert len(combined["equipment"]) == len(combined["renters"]) == 2
            assert {e["name"] for e in combined["equipment"]} == {"pc camera", "phone camera"}
            # Repeating a sync is idempotent.
            synchronize()
            assert len(equivalent()["equipment"]) == 2
            # Both devices independently rent the same last unit while offline.
            for context, page in [(pc_context, pc), (phone_context, phone)]:
                context.set_offline(True)
                page.evaluate("""async () => {
                  const data = await ERMLocal.request('/api/snapshot');
                  await ERMLocal.request('/api/local/rent',{method:'POST',body:JSON.stringify({
                    equipment_id:data.equipment.find(e=>e.name==='pc camera').id,renter_id:data.renters[0].id})});
                }""")
            before_pc, before_phone = data(pc), data(phone)
            pc_context.set_offline(False); phone_context.set_offline(False)
            synchronize(False)
            assert data(pc) == before_pc and data(phone) == before_phone
            expect(pc.locator("#usbSyncStatus")).to_contain_text("수량 충돌")
            # Correct the duplicate loan on the phone; keep its returned history.
            phone.evaluate("""async () => {
              const snapshot = await ERMLocal.request('/api/snapshot');
              await ERMLocal.request('/api/local/return',{method:'POST',body:JSON.stringify({rental_id:snapshot.active_rentals[0].id})});
            }""")
            synchronize()
            combined = equivalent()
            assert len(combined["rentals"]) == 2
            assert len([r for r in combined["rentals"] if not r["return_date"]]) == 1
            # Deletion on one device cannot be resurrected by the other device.
            pc.evaluate("""async () => {
              const snapshot = await ERMLocal.request('/api/snapshot');
              await ERMLocal.request('/api/equipment/'+snapshot.equipment.find(e=>e.name==='phone camera').id,{method:'DELETE'});
            }""")
            synchronize()
            assert len(equivalent()["equipment"]) == 1
            synchronize()
            assert len(equivalent()["equipment"]) == 1
            # A snapshot submitted before a subsequent local edit must not replace it.
            assert pc.evaluate("""async () => {
              const before = (await ERMLocal.backup()).data;
              await ERMLocal.request('/api/renters',{method:'POST',body:JSON.stringify({user_id:'during-sync',name:'new',phone:''})});
              try { await ERMLocal.applyUsb(before,before.version); return false; }
              catch { return (await ERMLocal.request('/api/snapshot')).renters.some(r=>r.user_id==='during-sync'); }
            }""")
            synchronize()
            assert len(equivalent()["renters"]) == 3
            # Shared manual totals: reject divergent edits; explicit PC preference resolves.
            change = """async values => {
              const snapshot = await ERMLocal.request('/api/snapshot');
              return ERMLocal.request('/api/stats/override',{method:'POST',body:JSON.stringify({
                equipment_id:snapshot.equipment[0].id,stat_month:'2040-01',return_count:0,...values})});
            }"""
            pc.evaluate(change, {"mode": "add", "rent_count": 1})
            synchronize()
            pc.evaluate(change, {"mode": "save", "rent_count": 3})
            phone.evaluate(change, {"mode": "save", "rent_count": 4})
            before_pc, before_phone = data(pc), data(phone)
            synchronize(False)
            assert data(pc) == before_pc and data(phone) == before_phone
            pc.locator("#usbPreference").select_option("pc")
            pc.once("dialog", lambda dialog: dialog.accept())
            synchronize()
            assert equivalent()["overrides"][0]["rent_count"] == 3
            pc.locator("#usbPreference").select_option("")
            synchronize()
            # One side changes after submission: don't report success or lose the edit.
            phone.evaluate("""() => {
              const apply = ERMLocal.applyUsb;
              ERMLocal.applyUsb = async (...args) => {
                ERMLocal.applyUsb = apply;
                await ERMLocal.request('/api/renters',{method:'POST',body:JSON.stringify({user_id:'late-edit',name:'late',phone:''})});
                return apply(...args);
              };
            }""")
            synchronize(False)
            assert any(r["user_id"] == "late-edit" for r in data(phone)["renters"])
            synchronize()
            assert len(equivalent()["renters"]) == 4
            # App remains usable after closing USB rendezvous.
            server.shutdown()
            for context, page in [(pc_context, pc), (phone_context, phone)]:
                context.set_offline(True); page.reload()
                expect(page.locator("[data-connection-label]").first).to_have_text("기기 저장 모드")
                assert len(data(page)["rentals"]) == 2
            browser.close()
            print("PASS: separate device databases, USB HTTP exchange, ID collision handling, idempotency, stock conflict/no writes, returns, tombstones, stale-version protection and disconnected reload")
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


if __name__ == "__main__":
    main()
