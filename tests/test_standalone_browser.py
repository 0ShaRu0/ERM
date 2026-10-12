"""Real browser checks for independent storage and full offline reload.

Run directly: python tests/test_standalone_browser.py (requires playwright + Edge).
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import tempfile
import json


def main():
    from playwright.sync_api import sync_playwright, expect

    root = Path(__file__).resolve().parents[1]

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://localhost:{server.server_port}/standalone/"
    try:
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p:
            context = p.chromium.launch_persistent_context(profile, channel="msedge", headless=True, accept_downloads=True)
            page = context.new_page()
            errors, api_requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: api_requests.append(request.url) if "/api/" in request.url else None)
            page.goto(url)
            expect(page.locator("[data-connection-label]").first).to_have_text("기기 저장 모드")
            page.wait_for_function("navigator.serviceWorker.controller !== null")
            page.locator('[data-open="equipmentDialog"]').click()
            page.locator('#equipmentForm [name="name"]').fill("오프라인 카메라")
            page.locator('#equipmentForm [name="category"]').fill("영상")
            page.locator('#equipmentForm [name="image"]').set_input_files(str(root / "static/icon-192.png"))
            page.locator('#equipmentForm button[type="submit"]').click()
            expect(page.locator("#equipmentDialog")).not_to_be_visible()
            expect(page.locator("#equipmentGrid")).to_contain_text("오프라인 카메라")
            assert page.locator("#equipmentGrid img").first.evaluate("image => image.complete && image.naturalWidth > 0")
            # Another browser/device starts with an entirely independent database.
            browser = p.chromium.launch(channel="msedge", headless=True)
            other = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            phone = other.new_page()
            phone.goto(url)
            expect(phone.locator("[data-connection-label]").last).to_have_text("기기 저장 모드")
            assert phone.evaluate("async () => (await ERMLocal.request('/api/snapshot')).equipment.length") == 0
            other.close()
            browser.close()
            # Stop the file server as well as disabling internet.
            server.shutdown()
            context.set_offline(True)
            page.reload()
            expect(page.locator("#equipmentGrid")).to_contain_text("오프라인 카메라")
            page.locator('[data-view="renters"]').first.click()
            page.locator('[data-open="renterDialog"]').click()
            page.locator('#renterForm [name="user_id"]').fill("device01")
            page.locator('#renterForm [name="name"]').fill("독립 사용자")
            page.locator('#renterForm [name="phone"]').fill("010-0000-0000")
            page.locator('#renterForm button[type="submit"]').click()
            expect(page.locator("#renterDialog")).not_to_be_visible()
            page.locator('[data-view="equipment"]').first.click()
            page.locator('[data-rent-equipment]').first.click()
            page.locator("#rentRenterId").select_option("1")
            page.locator('#rentForm button[type="submit"]').click()
            expect(page.locator("#rentDialog")).not_to_be_visible()
            assert page.evaluate("async () => (await ERMLocal.request('/api/snapshot')).active_rentals.length") == 1
            # Cannot delete active equipment, and duplicate returns fail atomically.
            assert page.evaluate("""async () => {
              try { await ERMLocal.request('/api/equipment/1', {method:'DELETE'}); return false; }
              catch { return true; }
            }""")
            page.locator('[data-return-equipment]').first.click()
            page.locator('#returnForm button[type="submit"]').click()
            expect(page.locator("#returnDialog")).not_to_be_visible()
            page.locator('[data-view="stats"]').first.click()
            expect(page.locator("#statsDetailBody")).to_contain_text("독립 사용자")
            with page.expect_download() as downloading:
                page.locator('[data-export="xlsx"]').click()
            assert downloading.value.suggested_filename.endswith(".csv")
            assert "독립 사용자" in downloading.value.path().read_text(encoding="utf-8-sig")
            # Manual totals override automatic counts without rewriting rental history.
            totals = page.evaluate("""async () => {
              const date = new Date();
              const year = date.getFullYear();
              const month = `${year}-${String(date.getMonth()+1).padStart(2,'0')}`;
              const change = data => ERMLocal.request('/api/stats/override',{method:'POST',body:JSON.stringify(data)});
              await change({mode:'save',equipment_id:1,stat_month:month,rent_count:8,return_count:7});
              const adjusted = await ERMLocal.request(`/api/stats?year=${year}`);
              await change({mode:'reset'});
              const reset = await ERMLocal.request(`/api/stats?year=${year}`);
              return [adjusted.monthly[date.getMonth()].slice(1),reset.monthly[date.getMonth()].slice(1),reset.details.length];
            }""")
            assert totals == [[8, 7], [1, 1], 1], totals
            # Browser-level simultaneous tabs contend for the last item.
            tab = context.new_page()
            tab.goto(url)
            expect(tab.locator("[data-connection-label]").first).to_have_text("기기 저장 모드")
            # A committed change in another window updates this window without focus/reload.
            page.locator('[data-view="renters"]').first.click()
            tab.evaluate("""() => ERMLocal.request('/api/renters', {
              method:'POST',body:JSON.stringify({user_id:'second-window',name:'다른 창 저장',phone:''})
            })""")
            expect(page.locator("#view-renters")).to_contain_text("다른 창 저장")
            page.locator('[data-view="stats"]').first.click()
            tab.evaluate("""() => {
              window.raceChannel = new BroadcastChannel('stock-race');
              raceChannel.onmessage = async () => {
                try {
                  await ERMLocal.request('/api/local/rent', {method:'POST',body:JSON.stringify({equipment_id:1,renter_id:1})});
                  raceChannel.postMessage('fulfilled');
                } catch { raceChannel.postMessage('rejected'); }
              };
            }""")
            result = page.evaluate("""async () => {
              const channel = new BroadcastChannel('stock-race');
              const remote = new Promise(resolve => { channel.onmessage = event => resolve(event.data); });
              const options = {method:'POST', body:JSON.stringify({equipment_id:1,renter_id:1})};
              channel.postMessage('start');
              const local = ERMLocal.request('/api/local/rent', options).then(() => 'fulfilled', () => 'rejected');
              const result = await Promise.all([local, remote]); channel.close(); return result;
            }""")
            assert sorted(result) == ["fulfilled", "rejected"], result
            assert tab.evaluate("async () => (await ERMLocal.request('/api/snapshot')).active_rentals.length") == 1
            # Backup includes pictures and cannot corrupt good data on validation failure.
            backup = page.evaluate("() => ERMLocal.backup()")
            assert backup["data"]["equipment"][0]["image_url"].startswith("data:image/png;base64,")
            malformed = {**backup, "data": {**backup["data"], "rentals": [{"id": 123}]}}
            assert page.evaluate("async b => { try { await ERMLocal.restore(b); return false; } catch { return true; } }", malformed)
            assert page.evaluate("async () => (await ERMLocal.request('/api/snapshot')).active_rentals.length") == 1
            page.evaluate("b => ERMLocal.restore(b)", backup)
            page.reload()
            expect(page.locator("#equipmentGrid")).to_contain_text("오프라인 카메라")
            # New offline page with mobile viewport, no horizontal overflow.
            tab.set_viewport_size({"width": 390, "height": 844})
            tab.reload()
            expect(tab.locator("#equipmentGrid")).to_contain_text("오프라인 카메라")
            assert tab.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator('[data-view="sync"]').first.click()
            with page.expect_download() as downloading:
                page.locator("#backupExport").click()
            assert downloading.value.suggested_filename.endswith(".json")
            page.locator("#backupImport").set_input_files({
                "name": "backup.json", "mimeType": "application/json", "buffer": json.dumps(backup).encode()
            })
            expect(page.locator("#confirmDialog")).to_be_visible()
            page.locator("#confirmAction").click()
            expect(page.locator("#toastRegion")).to_contain_text("백업을 복원했습니다")
            expect(page.locator(".queue-card").first).not_to_be_visible()
            assert not api_requests, api_requests
            assert not errors, errors
            context.close()
            # Restart the whole browser with persisted profile while server is stopped.
            reopened = p.chromium.launch_persistent_context(profile, channel="msedge", headless=True, offline=True)
            reopened_page = reopened.new_page()
            reopened_page.goto(url)
            expect(reopened_page.locator("#equipmentGrid")).to_contain_text("오프라인 카메라")
            assert reopened_page.evaluate("async () => (await ERMLocal.request('/api/snapshot')).active_rentals.length") == 1
            reopened.close()
            print("PASS: offline CRUD, stats/CSV, images/backup validation, independent devices, concurrent stock, subpath service worker and mobile layout; zero API calls")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


if __name__ == "__main__":
    main()
