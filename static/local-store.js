/* Standalone storage. All changes commit atomically, including across app tabs. */
(() => {
  "use strict";
  const DB_NAME = "erm-independent-v1";
  let opening;
  const changes = typeof BroadcastChannel === "function" ? new BroadcastChannel(DB_NAME) : null;
  const listeners = new Set();
  if (changes) changes.onmessage = () => listeners.forEach(listener => listener());
  const same = (a, b) => String(a) === String(b);
  const fail = (message) => { throw new Error(message); };
  const timestamp = () => {
    const d = new Date();
    return new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  };
  const initial = () => ({ schema: 1, database_id: crypto.randomUUID(), version: 0,
    equipment: [], renters: [], rentals: [], overrides: [] });
  function open() {
    if (!opening) opening = new Promise((resolve, reject) => {
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => request.result.createObjectStore("data");
      request.onsuccess = () => {
        request.result.onversionchange = () => { request.result.close(); opening = null; };
        resolve(request.result);
      };
      request.onerror = () => { opening = null; reject(request.error); };
      request.onblocked = () => { opening = null; reject(new Error("다른 앱 창을 닫고 다시 실행하세요.")); };
    });
    return opening;
  }
  async function transaction(write, action, track = true) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction("data", write ? "readwrite" : "readonly");
      const store = tx.objectStore("data");
      let result, failure;
      store.get("main").onsuccess = (event) => {
        try {
          const data = event.target.result || initial();
          if (write) window.ERMUsbMerge.prepare(data);
          const before = write && track ? structuredClone(data) : null;
          result = action(data);
          if (before) window.ERMUsbMerge.track(before, data);
          if (write) { data.version++; store.put(data, "main"); }
        } catch (error) { failure = error; tx.abort(); }
      };
      tx.oncomplete = () => {
        if (write) changes?.postMessage("committed");
        resolve(result);
      };
      tx.onabort = tx.onerror = () => reject(failure || tx.error || new Error("기기에 저장하지 못했습니다. 저장 공간을 확인하세요."));
    });
  }
  const nextId = (data, kind) => {
    data.sequences ||= {};
    const ids = data[kind].map(item => item.id);
    if (kind === "equipment") ids.push(...data.rentals.map(item => item.equipment_id), ...data.overrides.map(item => item.equipment_id));
    if (kind === "renters") ids.push(...data.rentals.map(item => item.renter_id));
    const previous = ids.reduce((max, id) => Math.max(max, Number(id) || 0), data.sequences[kind] || 0);
    return (data.sequences[kind] = previous + 1);
  };
  const active = (data) => data.rentals.filter(item => !item.return_date);
  function snapshot(data) {
    return { database_id: data.database_id, version: data.version, equipment: data.equipment.map((item, index) => {
      const count = active(data).filter(r => same(r.equipment_id, item.id)).length;
      return { ...item, display_number: index + 1, active_count: count,
        available_count: item.quantity - count, status: count < item.quantity ? "대여 가능" : "대여중" };
    }), renters: data.renters, active_rentals: active(data).slice().reverse(), recent_rentals: data.rentals.slice().reverse() };
  }
  function monthly(data, overrides = true) {
    const map = new Map();
    const get = (id, name, month) => {
      const key = `${id}:${month}`;
      if (!map.has(key)) map.set(key, { equipment_id: id, equipment_name: name, stat_month: month, rent_count: 0, return_count: 0 });
      return map.get(key);
    };
    for (const rental of data.rentals) {
      get(rental.equipment_id, rental.equipment_name, rental.rent_date.slice(0, 7)).rent_count++;
      if (rental.return_date) get(rental.equipment_id, rental.equipment_name, rental.return_date.slice(0, 7)).return_count++;
    }
    if (overrides) for (const item of data.overrides) Object.assign(get(item.equipment_id, item.equipment_name, item.stat_month), item);
    return [...map.values()];
  }
  function stats(data, parameters) {
    const year = parameters.get("year") || String(new Date().getFullYear());
    if (!/^\d{4}$/.test(year)) fail("기준 연도를 확인하세요.");
    const start = parameters.get("start_date") || `${year}-01-01`;
    const end = parameters.get("end_date") || `${year}-12-31`;
    if (start > end) fail("시작일은 종료일보다 늦을 수 없습니다.");
    const id = parameters.get("equipment_id");
    const rows = monthly(data);
    const sum = (items, field) => items.reduce((total, row) => total + row[field], 0);
    const years = [...new Set([year, ...rows.map(row => row.stat_month.slice(0, 4))])].sort().reverse();
    const items = new Map();
    for (const r of data.rentals) items.set(r.equipment_id, { equipment_id: r.equipment_id, equipment_name: r.equipment_name, is_current: 0 });
    for (const r of data.overrides) items.set(r.equipment_id, { equipment_id: r.equipment_id, equipment_name: r.equipment_name, is_current: 0 });
    data.equipment.forEach((e, i) => items.set(e.id, { equipment_id: e.id, equipment_name: e.name, display_number: i + 1, is_current: 1 }));
    return { years, selected_year: year, monthly: Array.from({ length: 12 }, (_, i) => {
      const month = `${year}-${String(i + 1).padStart(2, "0")}`;
      const filtered = rows.filter(row => row.stat_month === month && (!id || same(id, row.equipment_id)));
      return [i + 1, sum(filtered, "rent_count"), sum(filtered, "return_count")];
    }), yearly: years.map(y => {
      const filtered = rows.filter(row => row.stat_month.startsWith(y));
      return [y, sum(filtered, "rent_count"), sum(filtered, "return_count")];
    }), equipment_items: [...items.values()], equipment_totals: [...items.values()].map(item => {
      const filtered = rows.filter(row => same(row.equipment_id, item.equipment_id));
      return { ...item, rent_count: sum(filtered, "rent_count"), return_count: sum(filtered, "return_count"),
        active_count: active(data).filter(r => same(r.equipment_id, item.equipment_id)).length };
    }), details: data.rentals.filter(r => r.rent_date.slice(0, 10) >= start && r.rent_date.slice(0, 10) <= end && (!id || same(id, r.equipment_id))).slice().reverse() };
  }
  async function imageData(file) {
    if (!file?.size) return "";
    if (file.size > 12 * 1024 * 1024) fail("이미지는 12MB 이하로 선택하세요.");
    if (!/^image\/(png|jpeg|webp|gif|bmp)$/.test(file.type)) fail("지원하지 않는 이미지 형식입니다.");
    const bitmap = await createImageBitmap(file);
    bitmap.close();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }
  function count(value, min = 0, max = 999999999) {
    const n = Number(value);
    if (!Number.isSafeInteger(n) || n < min || n > max) fail("수량과 건수를 올바른 정수로 입력하세요.");
    return n;
  }
  async function request(url, options = {}) {
    const target = new URL(url, location.origin);
    const method = options.method || "GET";
    let body = options.body instanceof FormData ? Object.fromEntries(options.body) : JSON.parse(options.body || "{}");
    if (target.pathname === "/api/equipment" && method === "POST") body.image_url = await imageData(body.image);
    return transaction(method !== "GET", data => {
      const path = target.pathname;
      if (path === "/api/snapshot") return snapshot(data);
      if (path === "/api/stats") return stats(data, target.searchParams);
      if (path === "/api/equipment" && method === "POST") {
        const name = String(body.name || "").trim(), category = String(body.category || "").trim();
        if (!name || !category) fail("장비명과 분류를 입력하세요.");
        data.equipment.push({ id: nextId(data, "equipment"), name, category, quantity: count(body.quantity, 1, 9999), image_url: body.image_url });
      } else if (path === "/api/renters" && method === "POST") {
        const user_id = String(body.user_id || "").trim(), name = String(body.name || "").trim();
        if (!user_id || !name) fail("아이디와 이름을 입력하세요.");
        if (data.renters.some(r => r.user_id === user_id)) fail("이미 등록된 대여자 아이디입니다.");
        data.renters.push({ id: nextId(data, "renters"), user_id, name, phone: String(body.phone || "").trim() });
      } else if (/^\/api\/(equipment|renters)\/\d+$/.test(path) && method === "DELETE") {
        const [, , kind, id] = path.split("/");
        const field = kind === "equipment" ? "equipment_id" : "renter_id";
        if (active(data).some(r => same(r[field], id))) fail("대여 중인 기록이 있어 삭제할 수 없습니다.");
        data[kind] = data[kind].filter(item => !same(item.id, id));
      } else if (path === "/api/local/rent" && method === "POST") {
        const equipment = data.equipment.find(e => same(e.id, body.equipment_id));
        const renter = data.renters.find(r => same(r.id, body.renter_id));
        if (!equipment || !renter) fail("장비와 대여자를 다시 선택하세요.");
        if (active(data).filter(r => same(r.equipment_id, equipment.id)).length >= equipment.quantity) fail("대여 가능한 수량이 없습니다.");
        if (body.due_date && !validDate(body.due_date)) fail("반납 예정일을 확인하세요.");
        data.rentals.push({ id: nextId(data, "rentals"), equipment_id: equipment.id, equipment_name: equipment.name,
          renter_id: renter.id, user_id: renter.user_id, renter_name: renter.name, renter_phone: renter.phone,
          rent_date: timestamp(), due_date: body.due_date || "", return_date: null });
      } else if (path === "/api/local/return" && method === "POST") {
        const rental = active(data).find(r => same(r.id, body.rental_id));
        if (!rental) fail("이미 반납했거나 대여 기록이 없습니다.");
        rental.return_date = timestamp();
      } else if (path === "/api/stats/override" && method === "POST") {
        if (body.mode === "reset") data.overrides = [];
        else {
          if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(body.stat_month)) fail("통계 월을 확인하세요.");
          const id = count(body.equipment_id, 1);
          const equipment = data.equipment.find(e => same(e.id, id));
          const history = data.rentals.find(r => same(r.equipment_id, id)) || data.overrides.find(r => same(r.equipment_id, id));
          if (!equipment && !history) fail("장비를 찾을 수 없습니다.");
          const matches = r => same(r.equipment_id, id) && r.stat_month === body.stat_month;
          const base = monthly(data, false).find(matches), existing = data.overrides.find(matches);
          if (body.mode === "add" && (base || existing)) fail("이미 존재하는 통계 월입니다. 변경 저장을 사용하세요.");
          if (["save", "remove"].includes(body.mode) && !base && !existing) fail("저장할 통계를 찾을 수 없습니다. 신규 추가를 사용하세요.");
          if (!["add", "save", "remove"].includes(body.mode)) fail("통계 처리 방식을 확인하세요.");
          const rent = count(body.rent_count), returned = count(body.return_count);
          if (body.mode === "add" && !rent && !returned) fail("대여 또는 반납 건수를 1건 이상 입력하세요.");
          data.overrides = data.overrides.filter(r => !matches(r));
          if (body.mode !== "remove" || base) data.overrides.push({ ...existing, equipment_id: id,
            equipment_name: equipment?.name || history.equipment_name, stat_month: body.stat_month,
            rent_count: body.mode === "remove" ? 0 : rent, return_count: body.mode === "remove" ? 0 : returned });
        }
      } else fail("지원하지 않는 기기 작업입니다.");
      return { ok: true, message: "이 기기에 저장했습니다." };
    });
  }
  function validDate(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
      !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  }
  function validateBackup(source) {
    const data = structuredClone(source);
    if (data?.schema !== 1 || typeof data.database_id !== "string") fail("이음 백업 파일이 아닙니다.");
    for (const kind of ["equipment", "renters", "rentals", "overrides"]) {
      if (!Array.isArray(data[kind])) fail("백업 데이터 형식이 올바르지 않습니다.");
      const ids = new Set();
      for (const item of data[kind]) {
        if (!item || typeof item !== "object") fail("잘못된 백업 항목입니다.");
        if (kind !== "overrides") {
          if (typeof item.id !== "number") fail("잘못된 기록 번호입니다.");
          count(item.id, 1); if (ids.has(item.id)) fail("중복된 기록 번호입니다."); ids.add(item.id);
        }
      }
    }
    const text = (value) => { if (typeof value !== "string") fail("잘못된 백업 문자열입니다."); };
    for (const e of data.equipment) {
      text(e.name); text(e.category); e.quantity = count(e.quantity, 1, 9999);
      if (e.image_url && !/^data:image\/(png|jpeg|webp|gif|bmp);base64,[A-Za-z0-9+/=]+$/.test(e.image_url)) fail("잘못된 백업 이미지입니다.");
    }
    const users = new Set();
    for (const r of data.renters) {
      text(r.user_id); text(r.name); text(r.phone);
      if (users.has(r.user_id)) fail("중복된 대여자 아이디입니다."); users.add(r.user_id);
    }
    for (const r of data.rentals) {
      r.equipment_id = count(r.equipment_id, 1); r.renter_id = count(r.renter_id, 1);
      for (const field of ["equipment_name", "user_id", "renter_name", "renter_phone"]) text(r[field]);
      for (const field of ["rent_date", "return_date"]) {
        if (field === "return_date" && r[field] === null) continue;
        text(r[field]);
        if (!validDate(r[field].slice(0, 10)) || Number.isNaN(Date.parse(r[field]))) fail("잘못된 대여 일시입니다.");
      }
      if (r.due_date && !validDate(r.due_date)) fail("잘못된 반납 예정일입니다.");
      if (!r.return_date && (!data.equipment.some(e => same(e.id, r.equipment_id)) || !data.renters.some(e => same(e.id, r.renter_id)))) fail("대여 중인 장비 또는 대여자가 누락되었습니다.");
    }
    for (const e of data.equipment) if (active(data).filter(r => same(r.equipment_id, e.id)).length > e.quantity) fail("장비 수량보다 활성 대여가 많습니다.");
    const months = new Set();
    for (const o of data.overrides) {
      o.equipment_id = count(o.equipment_id, 1); o.rent_count = count(o.rent_count); o.return_count = count(o.return_count); text(o.equipment_name);
      if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(o.stat_month)) fail("잘못된 통계 월입니다.");
      const key = `${o.equipment_id}:${o.stat_month}`;
      if (months.has(key)) fail("중복된 통계 월입니다."); months.add(key);
    }
    data.version = count(data.version || 0);
    const sequences = data.sequences || {};
    data.sequences = {};
    for (const key of ["equipment", "renters", "rentals"]) data.sequences[key] = count(sequences[key] || 0);
    return data;
  }
  window.ERMLocal = {
    onChange: listener => { listeners.add(listener); return () => listeners.delete(listener); },
    request,
    initialize: () => transaction(true, () => true),
    backup: () => transaction(false, data => ({ app: "erm-independent", exported_at: timestamp(), data })),
    applyUsb: (merged, expectedVersion) => {
      const validated = validateBackup(merged);
      return transaction(true, current => {
        if (current.version !== expectedVersion) fail("동기화 대기 중 이 기기의 데이터가 변경되었습니다. 양쪽에서 다시 동기화하세요.");
        Object.assign(current, validated, { version: current.version });
        return true;
      }, false);
    },
    restore: async (backup) => {
      if (backup?.app !== "erm-independent") fail("이음 독립 앱 백업 파일을 선택하세요.");
      const data = validateBackup(backup.data);
      return transaction(true, current => { Object.assign(current, data); return true; });
    }
  };
})();
