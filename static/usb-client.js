(() => {
  "use strict";
  window.ERMUsbClient = { setup(onApplied) {
    const button = document.querySelector("#usbSync");
    const status = document.querySelector("#usbSyncStatus");
    const role = document.querySelector("#usbRole");
    const preference = document.querySelector("#usbPreference");
    const hinted = new URLSearchParams(location.search).get("usb_role");
    role.value = ["pc", "phone"].includes(hinted) ? hinted : /Android/i.test(navigator.userAgent) ? "phone" : "pc";
    const showPreference = () => { document.querySelector("#usbConflictLabel").hidden = role.value !== "pc"; };
    role.addEventListener("change", showPreference); showPreference();
    const delay = () => new Promise(resolve => setTimeout(resolve, 700));
    button.addEventListener("click", async () => {
      let token, round, clientId, acknowledged = false;
      const endpoint = new URL("usb-sync/", new URL("./", location.href));
      const call = async (path, body) => {
        const response = await fetch(new URL(path, endpoint), { method: body ? "POST" : "GET", cache: "no-store",
          headers: { "Content-Type": "application/json", "X-ERM-USB": token || "" },
          body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(15000) });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "USB 연결 요청이 실패했습니다.");
        return result;
      };
      if (role.value === "pc" && preference.value && !window.confirm("양쪽에서 같은 기록을 다르게 수정한 경우 선택한 기기의 내용을 사용합니다. 양쪽의 새 기록은 유지합니다. 먼저 백업한 후 계속할까요?")) return;
      button.disabled = true; role.disabled = true; preference.disabled = true;
      try {
        status.textContent = "USB 연결을 확인하고 있습니다…";
        token = (await call("session")).token;
        clientId = localStorage.getItem("erm-client-id");
        const backup = await window.ERMLocal.backup();
        let result = await call("submit", { role: role.value, client_id: clientId, preference: preference.value, backup });
        round = result.round;
        const deadline = Date.now() + 180000;
        const getStatus = () => call(`status?round=${encodeURIComponent(round)}&client_id=${encodeURIComponent(clientId)}`);
        while (result.stage === "waiting") {
          status.textContent = "상대 기기를 기다립니다. PC와 휴대폰 양쪽에서 USB 동기화 버튼을 누르세요. 대기 중에는 데이터를 변경하지 마세요.";
          if (Date.now() > deadline) throw new Error("상대 기기 대기 시간이 지났습니다. 양쪽에서 다시 시도하세요.");
          await delay(); result = await getStatus();
        }
        if (result.stage === "failed") throw new Error(result.error);
        if (result.stage !== "ready") throw new Error("이미 종료된 동기화입니다. 다시 시도하세요.");
        status.textContent = "양쪽 기록을 비교하고 있습니다…";
        const merged = window.ERMUsbMerge.merge(result.pc.data, result.phone.data, result.preference);
        // No local write takes place if reconciliation or revision validation fails.
        await window.ERMLocal.applyUsb(merged, backup.data.version);
        await onApplied();
        result = await call("ack", { round, client_id: clientId, ok: true });
        acknowledged = true;
        while (result.stage !== "complete") {
          if (result.stage === "failed") throw new Error(result.error);
          status.textContent = "이 기기에 반영했습니다. 상대 기기의 저장 완료를 기다립니다…";
          if (Date.now() > deadline) throw new Error("상대 기기 저장 확인이 지연되었습니다. USB 연결 후 양쪽에서 다시 동기화하세요.");
          await delay(); result = await getStatus();
        }
        status.textContent = "양쪽 기기에 동기화를 완료했습니다. PC 도구에서 Enter를 누르고 USB를 분리해도 됩니다.";
      } catch (error) {
        if (round && !acknowledged) {
          try { await call("ack", { round, client_id: clientId, ok: false, error: error.message }); } catch { /* reconnect and retry */ }
        }
        status.textContent = round
          ? `동기화를 완료하지 못했습니다: ${error.message} 일부 기기에 이미 반영됐을 수 있습니다. 내용을 확인하고 양쪽에서 다시 시도하세요.`
          : "USB 연결을 찾지 못했습니다. PC에서 USB_데이터_동기화.bat을 실행하고, 설치할 때 사용한 브라우저와 localhost:8766 주소로 여세요.";
      } finally {
        button.disabled = false; role.disabled = false; preference.disabled = false;
      }
    });
  } };
})();
