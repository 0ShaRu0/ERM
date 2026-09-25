(() => {
  "use strict";

  const DB_NAME = "erm-pwa";
  const DB_VERSION = 1;
  const POLL_INTERVAL = 15_000;
  const API_TIMEOUT = 10_000;
  const VIEW_META = {
    equipment: ["장비", "EQUIPMENT DESK"],
    rentals: ["대여 내역", "RENTAL FLOW"],
    renters: ["대여자 관리", "MEMBER DIRECTORY"],
    stats: ["통계", "RENTAL REPORT"],
    sync: ["동기화", "CONNECTION HUB"]
  };
  const ICONS = new Set([
    "alert", "box", "calendar", "check", "clock", "close", "download",
    "link", "plus", "return", "search", "swap", "sync", "trash", "users", "wifi"
  ]);

  const state = {
    snapshot: emptySnapshot(),
    operations: [],
    apiReachable: null,
    lastContact: null,
    lastSync: null,
    syncing: false,
    syncPromise: null,
    activeView: "equipment",
    rentalTab: "active",
    stats: null,
    statsLoaded: false,
    clientId: "",
    accessKey: "",
    authPromptBlocked: false,
    lastFocused: null
  };

  let dbPromise;

  class ApiError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  function $(selector, root = document) {
    return root.querySelector(selector);
  }

  function $$(selector, root = document) {
    return [...root.querySelectorAll(selector)];
  }

  function emptySnapshot() {
    return {
      version: null,
      database_id: null,
      access_pin: "",
      server_time: null,
      connection_urls: [],
      equipment: [],
      renters: [],
      active_rentals: [],
      recent_rentals: []
    };
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#039;"
    })[character]);
  }

  function icon(name, className = "") {
    const safeName = ICONS.has(name) ? name : "box";
    const safeClass = className ? ` class="${escapeHtml(className)}"` : "";
    return `<svg${safeClass} aria-hidden="true"><use href="#i-${safeName}"></use></svg>`;
  }

  function safeImageUrl(value) {
    if (!value) return "";
    try {
      const url = new URL(String(value), window.location.origin);
      if (url.origin !== window.location.origin || !["http:", "https:"].includes(url.protocol)) return "";
      return url.href;
    } catch {
      return "";
    }
  }

  function numberValue(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function sameId(first, second) {
    return String(first) === String(second);
  }

  function payloadId(value) {
    const text = String(value);
    return /^\d+$/.test(text) ? Number(text) : text;
  }

  function createUuid() {
    if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
  }

  function localTimestamp(date = new Date()) {
    const offsetMinutes = -date.getTimezoneOffset();
    const shifted = new Date(date.getTime() + offsetMinutes * 60_000);
    const base = shifted.toISOString().slice(0, -1);
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const absolute = Math.abs(offsetMinutes);
    const hours = String(Math.floor(absolute / 60)).padStart(2, "0");
    const minutes = String(absolute % 60).padStart(2, "0");
    return `${base}${sign}${hours}:${minutes}`;
  }

  function parseDate(value) {
    if (!value) return null;
    const text = String(value).trim();
    const parsed = new Date(text.includes("T") ? text : text.replace(" ", "T"));
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function formatDateTime(value) {
    const date = parseDate(value);
    if (!date) return value ? String(value) : "-";
    return new Intl.DateTimeFormat("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(date);
  }

  function formatDate(value) {
    if (!value) return "-";
    const date = parseDate(String(value).length === 10 ? `${value}T00:00:00` : value);
    if (!date) return String(value);
    return new Intl.DateTimeFormat("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).format(date);
  }

  function formatRelativeContact(date) {
    if (!date) return "서버 응답을 기다리고 있습니다.";
    const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
    if (seconds < 10) return "방금 서버 응답을 확인했습니다.";
    if (seconds < 60) return `${seconds}초 전에 서버 응답을 확인했습니다.`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}분 전에 서버 응답을 확인했습니다.`;
    return `${formatDateTime(date.toISOString())}에 연결했습니다.`;
  }

  function normalizeSnapshot(snapshot) {
    const source = snapshot && typeof snapshot === "object" ? snapshot : {};
    return {
      version: source.version ?? null,
      database_id: source.database_id ?? null,
      access_pin: source.access_pin ?? "",
      server_time: source.server_time ?? null,
      connection_urls: Array.isArray(source.connection_urls) ? source.connection_urls.map(String) : [],
      equipment: Array.isArray(source.equipment) ? source.equipment.map((item) => ({ ...item })) : [],
      renters: Array.isArray(source.renters) ? source.renters.map((item) => ({ ...item })) : [],
      active_rentals: Array.isArray(source.active_rentals) ? source.active_rentals.map((item) => ({ ...item })) : [],
      recent_rentals: Array.isArray(source.recent_rentals) ? source.recent_rentals.map((item) => ({ ...item })) : []
    };
  }

  function replaceServerSnapshot(snapshot) {
    state.snapshot = normalizeSnapshot(snapshot);
    state.operations
      .filter((operation) => operation.status === "pending")
      .sort((first, second) => numberValue(first.created_at) - numberValue(second.created_at))
      .forEach(applyOptimisticOperation);
  }

  function openDatabase() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      let timedOut = false;
      const timeout = setTimeout(
        () => {
          timedOut = true;
          reject(new Error("기기 저장소 응답 시간이 초과되었습니다."));
        },
        5_000
      );
      const request = indexedDB.open(DB_NAME, DB_VERSION);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains("snapshots")) database.createObjectStore("snapshots");
        if (!database.objectStoreNames.contains("operations")) {
          const store = database.createObjectStore("operations", { keyPath: "operation_id" });
          store.createIndex("created_at", "created_at");
          store.createIndex("status", "status");
        }
        if (!database.objectStoreNames.contains("meta")) database.createObjectStore("meta");
      };
      request.onsuccess = () => {
        clearTimeout(timeout);
        if (timedOut) {
          request.result.close();
          return;
        }
        resolve(request.result);
      };
      request.onerror = () => {
        clearTimeout(timeout);
        reject(request.error);
      };
    });
    return dbPromise;
  }

  async function dbRequest(storeName, mode, action) {
    const database = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(storeName, mode);
      const store = transaction.objectStore(storeName);
      const request = action(store);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      transaction.onerror = () => reject(transaction.error);
    });
  }

  function dbGet(store, key) {
    return dbRequest(store, "readonly", (objectStore) => objectStore.get(key));
  }

  function dbGetAll(store) {
    return dbRequest(store, "readonly", (objectStore) => objectStore.getAll());
  }

  function dbPut(store, value, key) {
    return dbRequest(store, "readwrite", (objectStore) => (
      key === undefined ? objectStore.put(value) : objectStore.put(value, key)
    ));
  }

  function dbDelete(store, key) {
    return dbRequest(store, "readwrite", (objectStore) => objectStore.delete(key));
  }

  async function saveQueuedOperation(operation) {
    const database = await openDatabase();
    return new Promise((resolve, reject) => {
      const transaction = database.transaction(["operations", "snapshots"], "readwrite");
      transaction.objectStore("operations").put(operation);
      transaction.objectStore("snapshots").put(state.snapshot, "latest");
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error || new Error("저장 트랜잭션이 중단되었습니다."));
    });
  }

  async function saveSnapshot() {
    await dbPut("snapshots", state.snapshot, "latest");
  }

  async function saveSnapshotBestEffort() {
    try {
      await saveSnapshot();
      return true;
    } catch (error) {
      console.warn("서버 데이터를 기기에 캐시하지 못했습니다.", error);
      return false;
    }
  }

  function getClientId() {
    const key = "erm-client-id";
    try {
      let value = localStorage.getItem(key);
      if (!value) {
        value = createUuid();
        localStorage.setItem(key, value);
      }
      return value;
    } catch {
      return createUuid();
    }
  }

  async function loadLocalData() {
    try {
      const [snapshot, operations, lastSync] = await Promise.all([
        dbGet("snapshots", "latest"),
        dbGetAll("operations"),
        dbGet("meta", "last-sync")
      ]);
      if (snapshot) state.snapshot = normalizeSnapshot(snapshot);
      state.operations = Array.isArray(operations)
        ? operations.sort((first, second) => numberValue(first.created_at) - numberValue(second.created_at))
        : [];
      state.lastSync = lastSync || null;
    } catch (error) {
      console.error("로컬 데이터베이스를 열 수 없습니다.", error);
      showToast("기기 저장소를 열 수 없습니다. 브라우저 저장소 설정을 확인하세요.", "error");
    }
  }

  function markServerReachable(reachable) {
    state.apiReachable = reachable;
    if (reachable) state.lastContact = new Date();
    updateConnectionUI();
  }

  async function apiFetch(url, options = {}) {
    const authRetry = options.authRetry !== false;
    const fetchOptions = { ...options };
    delete fetchOptions.authRetry;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT);
    let serverResponded = false;
    try {
      const response = await fetch(url, {
        cache: "no-store",
        ...fetchOptions,
        headers: {
          Accept: "application/json",
          "X-ERM-Client": "web",
          "X-ERM-Key": state.accessKey,
          ...(fetchOptions.headers || {})
        },
        signal: controller.signal
      });
      serverResponded = true;
      markServerReachable(true);
      const contentType = response.headers.get("content-type") || "";
      const body = contentType.includes("application/json") ? await response.json() : null;
      if (response.status === 401 && authRetry && !state.authPromptBlocked) {
        state.authPromptBlocked = true;
        const entered = window.prompt("PC 화면의 동기화 메뉴에 표시된 6자리 연결 PIN을 입력하세요.", "");
        if (entered?.trim()) {
          state.accessKey = entered.trim();
          state.authPromptBlocked = false;
          try { localStorage.setItem("erm-access-key", state.accessKey); } catch { /* 저장 불가 시 현재 세션만 사용 */ }
          return apiFetch(url, { ...options, authRetry: false });
        }
      }
      if (response.status === 401) markServerReachable(false);
      if (!response.ok || body?.ok === false) {
        throw new ApiError(body?.message || `요청을 처리하지 못했습니다. (${response.status})`, response.status);
      }
      return body;
    } catch (error) {
      if (!serverResponded) markServerReachable(false);
      if (error.name === "AbortError") throw new ApiError("서버 응답 시간이 초과되었습니다.");
      throw error instanceof ApiError ? error : new ApiError("서버에 연결할 수 없습니다.");
    } finally {
      clearTimeout(timeout);
    }
  }

  function updateConnectionUI() {
    const label = state.apiReachable === true ? "서버 연결됨" : state.apiReachable === false ? "오프라인" : "확인 중";
    $$('[data-connection-label]').forEach((element) => { element.textContent = label; });
    $$('[data-status-dot]').forEach((element) => {
      element.classList.toggle("is-online", state.apiReachable === true);
      element.classList.toggle("is-offline", state.apiReachable === false);
    });

    const offlineBanner = $("#offlineBanner");
    offlineBanner.hidden = state.apiReachable !== false;
    $$(".online-only").forEach((element) => {
      const blocked = element.dataset.blocked === "true";
      const busy = element.classList.contains("is-busy");
      element.disabled = state.apiReachable !== true || blocked || busy;
      if (state.apiReachable !== true) element.title = "서버 연결이 필요한 기능입니다.";
      else if (blocked) element.title = element.dataset.blockedTitle || "현재 상태에서는 사용할 수 없습니다.";
      else if (busy) element.title = "처리 중입니다.";
      else element.removeAttribute("title");
    });

    const badge = $("#connectionBadge");
    badge.textContent = label;
    badge.classList.toggle("is-online", state.apiReachable === true);
    badge.classList.toggle("is-offline", state.apiReachable === false);
    $("[data-last-contact]").textContent = formatRelativeContact(state.lastContact);

    if (state.syncing) {
      $("#syncHeroText").textContent = "기기에 저장된 작업을 서버로 전송하고 있습니다.";
    } else if (state.apiReachable === true) {
      $("#syncHeroText").textContent = "서버와 연결되어 있습니다. 변경 사항을 안전하게 동기화할 수 있습니다.";
    } else if (state.apiReachable === false) {
      $("#syncHeroText").textContent = "서버에 닿지 않습니다. 대여·반납 기록은 이 기기에 안전하게 보관됩니다.";
    } else {
      $("#syncHeroText").textContent = "서버와의 연결 상태를 확인하고 있습니다.";
    }
  }

  function renderAll() {
    renderEquipment();
    renderRentals();
    renderRenters();
    renderEquipmentSelects();
    renderSync();
    renderConnectionAddress();
    if (state.stats) renderStats();
    updateConnectionUI();
  }

  function renderConnectionAddress() {
    const urls = state.snapshot.connection_urls || [];
    const preferred = urls.find((url) => !url.includes("127.0.0.1") && !url.includes("localhost"));
    $("#serverOrigin").textContent = preferred || window.location.origin;
    $("#connectionPin").textContent = state.snapshot.access_pin || "------";
  }

  function renderEquipment() {
    const equipment = state.snapshot.equipment;
    const totalQuantity = equipment.reduce((sum, item) => sum + Math.max(0, numberValue(item.quantity)), 0);
    const totalActive = equipment.reduce((sum, item) => sum + Math.max(0, numberValue(item.active_count)), 0);
    const totalAvailable = equipment.reduce((sum, item) => (
      sum + Math.max(0, numberValue(item.available_count, numberValue(item.quantity) - numberValue(item.active_count)))
    ), 0);
    $("#availableTotal").textContent = totalAvailable.toLocaleString("ko-KR");
    $("#inventorySummary").textContent = equipment.length
      ? `${equipment.length.toLocaleString("ko-KR")}종 · 총 ${totalQuantity.toLocaleString("ko-KR")}개 · 현재 ${totalActive.toLocaleString("ko-KR")}개 대여 중`
      : "등록된 장비가 없습니다. 서버 연결 후 첫 장비를 등록하세요.";

    const keyword = $("#equipmentSearch")?.value.trim().toLocaleLowerCase("ko-KR") || "";
    const filtered = equipment.filter((item) => (
      `${item.name || ""} ${item.category || ""}`.toLocaleLowerCase("ko-KR").includes(keyword)
    ));
    const grid = $("#equipmentGrid");
    if (!filtered.length) {
      grid.innerHTML = emptyState(
        "box",
        keyword ? "검색 결과가 없습니다" : "등록된 장비가 없습니다",
        keyword ? "다른 장비명이나 분류로 검색해 보세요." : "온라인 상태에서 장비 등록 버튼으로 시작하세요."
      );
      return;
    }

    grid.innerHTML = filtered.map((item) => {
      const quantity = Math.max(0, numberValue(item.quantity));
      const active = Math.max(0, numberValue(item.active_count));
      const available = Math.max(0, numberValue(item.available_count, quantity - active));
      const stockPercent = quantity > 0 ? Math.min(100, Math.max(0, (available / quantity) * 100)) : 0;
      const imageUrl = safeImageUrl(item.image_url);
      const id = escapeHtml(item.id);
      const actions = [];
      if (available > 0) {
        actions.push(`<button class="button primary" type="button" data-rent-equipment="${id}">${icon("swap")}대여</button>`);
      }
      if (active > 0) {
        actions.push(`<button class="button secondary" type="button" data-return-equipment="${id}">${icon("return")}반납</button>`);
      }
      if (!actions.length) actions.push('<button class="button ghost" type="button" disabled>대여 가능 수량 없음</button>');
      const canDelete = state.apiReachable === true && active === 0;
      const deleteTitle = active > 0 ? "대여 중인 장비는 삭제할 수 없습니다." : "장비 삭제";
      return `
        <article class="equipment-card">
          <div class="equipment-visual">
            ${imageUrl
              ? `<img src="${escapeHtml(imageUrl)}" alt="${escapeHtml(item.name || "장비")}" loading="lazy">`
              : `<span class="image-placeholder">${icon("box")}</span>`}
            <span class="card-number">NO. ${escapeHtml(item.display_number ?? item.id)}</span>
            <span class="status-chip ${available === 0 ? "is-full" : ""}">${available > 0 ? "대여 가능" : "모두 대여 중"}</span>
          </div>
          <div class="equipment-content">
            <div class="equipment-title-row">
              <div><h3 title="${escapeHtml(item.name)}">${escapeHtml(item.name || "이름 없는 장비")}</h3><span class="equipment-category">${escapeHtml(item.category || "미분류")}</span></div>
              <div class="equipment-count"><strong>${available}</strong> / ${quantity}</div>
              <button class="icon-button delete-equipment online-only" type="button" data-delete-equipment="${id}" data-blocked="${active > 0}" data-blocked-title="${escapeHtml(deleteTitle)}" aria-label="${escapeHtml(item.name || "장비")} 삭제" title="${escapeHtml(deleteTitle)}" ${canDelete ? "" : "disabled"}>${icon("trash")}</button>
            </div>
            <div class="stock-meter" aria-hidden="true"><span style="--stock:${stockPercent.toFixed(2)}%"></span></div>
            <p class="stock-label"><span>가용 수량</span><span>${active}개 대여 중</span></p>
            <div class="card-actions ${actions.length === 1 ? "one-action" : ""}">${actions.join("")}</div>
          </div>
        </article>`;
    }).join("");
  }

  function rentalField(rental, ...keys) {
    for (const key of keys) {
      if (rental?.[key] !== undefined && rental[key] !== null && rental[key] !== "") return rental[key];
    }
    return "";
  }

  function rentalStatus(rental) {
    const returnedAt = rentalField(rental, "return_date", "returned_at");
    if (returnedAt) return { text: "반납 완료", className: "returned" };
    const dueDate = rentalField(rental, "due_date");
    const today = new Date();
    const todayText = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
    if (dueDate && String(dueDate).slice(0, 10) < todayText) return { text: "기한 초과", className: "overdue" };
    return { text: "대여 중", className: "" };
  }

  function renderRentals() {
    const active = state.snapshot.active_rentals;
    $("#activeRentalCount").textContent = active.length.toLocaleString("ko-KR");
    const source = state.rentalTab === "active" ? active : state.snapshot.recent_rentals;
    const list = $("#rentalList");
    if (!source.length) {
      list.innerHTML = emptyState(
        "swap",
        state.rentalTab === "active" ? "현재 대여 중인 장비가 없습니다" : "최근 대여 내역이 없습니다",
        state.rentalTab === "active" ? "모든 장비가 제자리에 있습니다." : "대여가 처리되면 이곳에 기록됩니다."
      );
      return;
    }
    list.innerHTML = source.map((rental) => renderRentalCard(rental)).join("");
  }

  function renderRentalCard(rental) {
    const id = rentalField(rental, "id", "rental_id");
    const equipmentName = rentalField(rental, "equipment_name", "name") || "장비";
    const renterName = rentalField(rental, "renter_name", "name") || "이름 없음";
    const userId = rentalField(rental, "user_id") || "-";
    const phone = rentalField(rental, "renter_phone", "phone") || "연락처 없음";
    const rentDate = rentalField(rental, "rent_date", "rented_at", "occurred_at");
    const dueDate = rentalField(rental, "due_date");
    const returnedAt = rentalField(rental, "return_date", "returned_at");
    const status = rentalStatus(rental);
    const localPending = String(id).startsWith("local:") || rental.__pending_return;
    return `
      <article class="rental-card">
        <div class="rental-symbol ${returnedAt ? "is-returned" : ""}">${icon(returnedAt ? "check" : "swap")}</div>
        <div class="rental-content">
          <div class="rental-top"><h3 title="${escapeHtml(equipmentName)}">${escapeHtml(equipmentName)}</h3><span class="badge ${status.className}">${escapeHtml(status.text)}</span></div>
          <p class="rental-person"><strong>${escapeHtml(renterName)}</strong> · ${escapeHtml(userId)} · ${escapeHtml(phone)}</p>
          <div class="rental-meta">
            <span>${icon("clock")}${escapeHtml(formatDateTime(rentDate))}</span>
            <span>${icon("calendar")}반납 예정 ${escapeHtml(formatDate(dueDate))}</span>
            ${returnedAt ? `<span>${icon("return")}반납 ${escapeHtml(formatDateTime(returnedAt))}</span>` : ""}
            ${localPending ? '<span class="badge pending">동기화 대기</span>' : ""}
          </div>
        </div>
        ${returnedAt ? "" : `<button class="button secondary" type="button" data-return-rental="${escapeHtml(id)}">${icon("return")}반납</button>`}
      </article>`;
  }

  function renderRenters() {
    const keyword = $("#renterSearch")?.value.trim().toLocaleLowerCase("ko-KR") || "";
    const renters = state.snapshot.renters.filter((renter) => (
      `${renter.user_id || ""} ${renter.name || ""} ${renter.phone || ""}`.toLocaleLowerCase("ko-KR").includes(keyword)
    ));
    $("#renterCount").textContent = `${renters.length.toLocaleString("ko-KR")}명`;
    const grid = $("#renterGrid");
    if (!renters.length) {
      grid.innerHTML = emptyState(
        "users",
        keyword ? "검색 결과가 없습니다" : "등록된 대여자가 없습니다",
        keyword ? "아이디, 이름 또는 연락처를 다시 확인하세요." : "온라인 상태에서 대여자를 먼저 등록하세요."
      );
      return;
    }
    const avatarColors = ["#0b6f6b", "#476e80", "#806548", "#685c82", "#397360"];
    grid.innerHTML = renters.map((renter) => {
      const name = String(renter.name || "?");
      const colorIndex = [...name].reduce((sum, character) => sum + character.charCodeAt(0), 0) % avatarColors.length;
      return `
        <article class="renter-card">
          <span class="renter-avatar" style="--avatar-color:${avatarColors[colorIndex]}">${escapeHtml([...name][0] || "?")}</span>
          <div class="renter-info"><h3>${escapeHtml(name)}</h3><p><code>${escapeHtml(renter.user_id || "-")}</code> · ${escapeHtml(renter.phone || "연락처 없음")}</p></div>
          <button class="icon-button online-only" type="button" data-delete-renter="${escapeHtml(renter.id)}" aria-label="${escapeHtml(name)} 대여자 삭제" ${state.apiReachable === true ? "" : "disabled"}>${icon("trash")}</button>
        </article>`;
    }).join("");
  }

  function equipmentOption(item) {
    const id = item.equipment_id ?? item.id;
    const name = item.equipment_name ?? item.name ?? "이름 없는 장비";
    const number = item.display_number ?? id;
    const suffix = item.is_current === 0 || item.is_current === false ? " (삭제됨)" : "";
    return `<option value="${escapeHtml(id)}">NO. ${escapeHtml(number)} · ${escapeHtml(name)}${suffix}</option>`;
  }

  function renderEquipmentSelects() {
    const items = state.stats?.equipment_items?.length ? state.stats.equipment_items : state.snapshot.equipment;
    const statsSelect = $("#statsEquipment");
    const statsValue = statsSelect.value;
    statsSelect.innerHTML = `<option value="">전체 장비</option>${items.map(equipmentOption).join("")}`;
    if ($$("option", statsSelect).some((option) => option.value === statsValue)) statsSelect.value = statsValue;

    const overrideSelect = $("#overrideEquipment");
    const overrideValue = overrideSelect.value;
    overrideSelect.innerHTML = items.length
      ? items.map(equipmentOption).join("")
      : '<option value="">등록된 장비 없음</option>';
    if ($$("option", overrideSelect).some((option) => option.value === overrideValue)) overrideSelect.value = overrideValue;
  }

  function monthlyItem(item, index) {
    if (Array.isArray(item)) {
      return { month: numberValue(item[0], index + 1), rent_count: numberValue(item[1]), return_count: numberValue(item[2]) };
    }
    return {
      month: numberValue(item?.month, index + 1),
      rent_count: numberValue(item?.rent_count ?? item?.rent),
      return_count: numberValue(item?.return_count ?? item?.returns)
    };
  }

  function yearlyItem(item) {
    if (Array.isArray(item)) return { year: item[0], rent_count: numberValue(item[1]), return_count: numberValue(item[2]) };
    return {
      year: item?.year ?? "-",
      rent_count: numberValue(item?.rent_count ?? item?.rent),
      return_count: numberValue(item?.return_count ?? item?.returns)
    };
  }

  function totalItem(item) {
    if (Array.isArray(item)) {
      return { equipment_name: item[0], rent_count: numberValue(item[1]), return_count: numberValue(item[2]), active_count: numberValue(item[3]) };
    }
    return {
      equipment_name: item?.equipment_name ?? item?.name ?? "이름 없는 장비",
      display_number: item?.display_number,
      rent_count: numberValue(item?.rent_count ?? item?.rent),
      return_count: numberValue(item?.return_count ?? item?.returns),
      active_count: numberValue(item?.active_count)
    };
  }

  function renderStats() {
    const stats = state.stats || {};
    const monthly = Array.isArray(stats.monthly) ? stats.monthly.map(monthlyItem) : [];
    const completeMonthly = Array.from({ length: 12 }, (_, index) => (
      monthly.find((item) => item.month === index + 1) || { month: index + 1, rent_count: 0, return_count: 0 }
    ));
    const yearly = Array.isArray(stats.yearly) ? stats.yearly.map(yearlyItem) : [];
    const totals = Array.isArray(stats.equipment_totals) ? stats.equipment_totals.map(totalItem) : [];
    const details = Array.isArray(stats.details) ? stats.details : [];
    const rentTotal = completeMonthly.reduce((sum, item) => sum + item.rent_count, 0);
    const returnTotal = completeMonthly.reduce((sum, item) => sum + item.return_count, 0);
    const activeTotal = totals.reduce((sum, item) => sum + item.active_count, 0);

    $("#statCards").innerHTML = `
      <article class="stat-card"><span>선택 연도 대여</span><strong>${rentTotal.toLocaleString("ko-KR")}</strong><small>건</small></article>
      <article class="stat-card amber"><span>선택 연도 반납</span><strong>${returnTotal.toLocaleString("ko-KR")}</strong><small>건</small></article>
      <article class="stat-card blue"><span>현재 대여 중</span><strong>${activeTotal.toLocaleString("ko-KR")}</strong><small>건</small></article>
      <article class="stat-card"><span>조회 상세</span><strong>${details.length.toLocaleString("ko-KR")}</strong><small>건</small></article>`;

    const maximum = Math.max(1, ...completeMonthly.flatMap((item) => [item.rent_count, item.return_count]));
    $("#monthlyChart").innerHTML = completeMonthly.map((item) => {
      const rentHeight = (item.rent_count / maximum) * 100;
      const returnHeight = (item.return_count / maximum) * 100;
      return `
        <div class="chart-month" aria-label="${item.month}월 대여 ${item.rent_count}건, 반납 ${item.return_count}건">
          <div class="chart-bars">
            <span class="chart-bar" style="--height:${rentHeight.toFixed(2)}%" title="대여 ${item.rent_count}건"></span>
            <span class="chart-bar return" style="--height:${returnHeight.toFixed(2)}%" title="반납 ${item.return_count}건"></span>
          </div><span>${item.month}월</span>
        </div>`;
    }).join("");

    $("#equipmentTotalsBody").innerHTML = totals.length ? totals.map((item) => `
      <tr><td>${item.display_number ? `NO. ${escapeHtml(item.display_number)} · ` : ""}${escapeHtml(item.equipment_name)}</td><td>${item.rent_count.toLocaleString("ko-KR")}</td><td>${item.return_count.toLocaleString("ko-KR")}</td><td>${item.active_count.toLocaleString("ko-KR")}</td></tr>`).join("")
      : '<tr><td class="table-empty" colspan="4">집계된 장비가 없습니다.</td></tr>';
    $("#yearlyStatsBody").innerHTML = yearly.length ? yearly.map((item) => `
      <tr><td>${escapeHtml(item.year)}</td><td>${item.rent_count.toLocaleString("ko-KR")}</td><td>${item.return_count.toLocaleString("ko-KR")}</td></tr>`).join("")
      : '<tr><td class="table-empty" colspan="3">연도별 통계가 없습니다.</td></tr>';

    $("#detailCount").textContent = `${details.length.toLocaleString("ko-KR")}건`;
    $("#statsDetailBody").innerHTML = details.length ? details.map((rental) => {
      const status = rentalStatus(rental);
      const returnedAt = rentalField(rental, "return_date", "returned_at");
      const statusText = returnedAt ? `반납 ${formatDateTime(returnedAt)}` : status.text;
      return `<tr>
        <td>${escapeHtml(formatDateTime(rentalField(rental, "rent_date", "rented_at")))}</td>
        <td>${escapeHtml(rentalField(rental, "equipment_name", "name") || "-")}</td>
        <td>${escapeHtml(rentalField(rental, "user_id") || "-")}</td>
        <td>${escapeHtml(rentalField(rental, "renter_name") || "-")}</td>
        <td>${escapeHtml(rentalField(rental, "renter_phone", "phone") || "-")}</td>
        <td>${escapeHtml(formatDate(rentalField(rental, "due_date")))}</td>
        <td><span class="badge ${status.className}">${escapeHtml(statusText)}</span></td>
      </tr>`;
    }).join("") : '<tr><td class="table-empty" colspan="7">선택한 조건의 상세 내역이 없습니다.</td></tr>';

    renderStatsYears(stats);
    renderEquipmentSelects();
    const start = $("#statsStartDate").value;
    const end = $("#statsEndDate").value;
    const year = $("#statsYear").value;
    const equipmentText = $("#statsEquipment").selectedOptions[0]?.textContent || "전체 장비";
    $("#statsRangeLabel").textContent = `${start || `${year}-01-01`} ~ ${end || `${year}-12-31`} · ${equipmentText}`;
  }

  function renderStatsYears(stats = {}) {
    const select = $("#statsYear");
    const selected = String(stats.selected_year ?? select.value ?? new Date().getFullYear());
    const years = Array.isArray(stats.years) ? stats.years.map(String) : [];
    if (!years.includes(selected)) years.push(selected);
    if (!years.length) years.push(String(new Date().getFullYear()));
    years.sort((first, second) => Number(second) - Number(first));
    select.innerHTML = years.map((year) => `<option value="${escapeHtml(year)}">${escapeHtml(year)}년</option>`).join("");
    select.value = selected;
  }

  function renderSync() {
    const pending = state.operations.filter((operation) => operation.status === "pending");
    const conflicts = state.operations.filter((operation) => operation.status === "conflict");
    $("#pendingCount").textContent = pending.length.toLocaleString("ko-KR");
    $("#conflictCount").textContent = conflicts.length.toLocaleString("ko-KR");
    const queueCount = pending.length + conflicts.length;
    for (const id of ["headerSyncCount", "sideSyncCount", "bottomSyncCount"]) {
      const element = $(`#${id}`);
      element.textContent = queueCount > 99 ? "99+" : String(queueCount);
      element.hidden = queueCount === 0;
    }
    $("#pendingList").innerHTML = pending.length
      ? pending.map((operation) => renderOperation(operation, false)).join("")
      : '<p class="operation-empty">전송을 기다리는 작업이 없습니다.</p>';
    $("#conflictList").innerHTML = conflicts.length
      ? conflicts.map((operation) => renderOperation(operation, true)).join("")
      : '<p class="operation-empty">확인이 필요한 충돌이 없습니다.</p>';
    $("#lastSyncTime").textContent = state.lastSync ? formatDateTime(state.lastSync) : "기록 없음";
  }

  function operationDescription(operation) {
    if (operation.type === "rent") {
      const equipment = state.snapshot.equipment.find((item) => sameId(item.id, operation.payload?.equipment_id));
      const renter = state.snapshot.renters.find((item) => sameId(item.id, operation.payload?.renter_id));
      return `${equipment?.name || "장비"} · ${renter?.name || "대여자"}`;
    }
    const rental = [...state.snapshot.active_rentals, ...state.snapshot.recent_rentals]
      .find((item) => sameId(rentalField(item, "id", "rental_id"), operation.payload?.rental_id));
    return rental ? `${rentalField(rental, "equipment_name") || "장비"} · ${rentalField(rental, "renter_name") || "대여자"}` : `대여 ${operation.payload?.rental_id || "-"}`;
  }

  function renderOperation(operation, conflict) {
    return `
      <article class="operation-item">
        <span class="operation-icon">${icon(operation.type === "rent" ? "swap" : "return")}</span>
        <div class="operation-info"><strong>${operation.type === "rent" ? "대여" : "반납"} · ${escapeHtml(operationDescription(operation))}</strong><span>${escapeHtml(formatDateTime(operation.occurred_at))}</span></div>
        ${conflict ? `<button class="icon-button" type="button" data-delete-conflict="${escapeHtml(operation.operation_id)}" aria-label="충돌 기록 삭제">${icon("trash")}</button>` : '<span class="badge pending">대기</span>'}
        ${conflict ? `<p class="operation-message">${escapeHtml(operation.message || "서버 데이터와 충돌했습니다.")}</p>` : ""}
      </article>`;
  }

  function emptyState(iconName, title, message) {
    return `<div class="empty-state">${icon(iconName)}<div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(message)}</p></div></div>`;
  }

  function setView(view) {
    if (!VIEW_META[view]) return;
    state.activeView = view;
    $$('[data-view-panel]').forEach((panel) => {
      const active = panel.dataset.viewPanel === view;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    $$('[data-view]').forEach((button) => {
      const active = button.dataset.view === view;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    $("#pageTitle").textContent = VIEW_META[view][0];
    $("#pageEyebrow").textContent = VIEW_META[view][1];
    document.title = `${VIEW_META[view][0]} | 이음`;
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (view === "stats" && state.apiReachable === true && !state.statsLoaded) void loadStats(true);
  }

  function showDialog(id) {
    const dialog = document.getElementById(id);
    if (!dialog || dialog.open) return;
    state.lastFocused = document.activeElement;
    dialog.returnValue = "";
    dialog.showModal();
    requestAnimationFrame(() => {
      const focusTarget = dialog.querySelector("input:not([type='hidden']), select, button:not([data-close])");
      focusTarget?.focus();
    });
  }

  function closeDialog(dialog) {
    if (dialog?.open) dialog.close();
  }

  function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.setAttribute("role", type === "error" ? "alert" : "status");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", type === "error" ? "#i-alert" : "#i-check");
    svg.append(use);
    const text = document.createElement("span");
    text.textContent = message;
    toast.append(svg, text);
    $("#toastRegion").append(toast);
    setTimeout(() => toast.remove(), 4_500);
  }

  function askConfirmation(title, message, actionLabel = "확인") {
    const dialog = $("#confirmDialog");
    $("#confirmTitle").textContent = title;
    $("#confirmMessage").textContent = message;
    $("#confirmAction").textContent = actionLabel;
    showDialog("confirmDialog");
    return new Promise((resolve) => {
      dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), { once: true });
    });
  }

  function requireOnline() {
    if (state.apiReachable === true) return true;
    showToast("서버 연결이 필요한 기능입니다. 동기화 화면에서 연결 상태를 확인하세요.", "error");
    return false;
  }

  async function refreshSnapshot({ silent = false } = {}) {
    try {
      const snapshot = await apiFetch("/api/snapshot");
      replaceServerSnapshot(snapshot);
      await saveSnapshotBestEffort();
      renderAll();
      return true;
    } catch (error) {
      if (!silent) showToast(error.message, "error");
      return false;
    }
  }

  async function queueOperation(type, payload) {
    const now = new Date();
    const operationId = createUuid();
    const operation = {
      operation_id: operationId,
      type,
      database_id: state.snapshot.database_id,
      occurred_at: localTimestamp(now),
      created_at: now.getTime(),
      payload,
      status: "pending",
      message: ""
    };
    const previousSnapshot = structuredClone(state.snapshot);
    state.operations.push(operation);
    applyOptimisticOperation(operation);
    try {
      await saveQueuedOperation(operation);
      renderAll();
      showToast(type === "rent" ? "대여 작업을 기기에 저장했습니다." : "반납 작업을 기기에 저장했습니다.");
      if (state.apiReachable === true) void syncPending({ silent: true });
      return true;
    } catch (error) {
      console.error("작업을 저장할 수 없습니다.", error);
      state.snapshot = previousSnapshot;
      state.operations = state.operations.filter((item) => item.operation_id !== operationId);
      renderAll();
      showToast("작업을 기기에 저장하지 못했습니다. 저장 공간을 확인하세요.", "error");
      return false;
    }
  }

  function applyOptimisticOperation(operation) {
    if (operation.type === "rent") {
      const equipment = state.snapshot.equipment.find((item) => sameId(item.id, operation.payload.equipment_id));
      const renter = state.snapshot.renters.find((item) => sameId(item.id, operation.payload.renter_id));
      if (!equipment || !renter) return;
      const activeCount = numberValue(equipment.active_count) + 1;
      const quantity = numberValue(equipment.quantity);
      equipment.active_count = activeCount;
      equipment.available_count = Math.max(0, quantity - activeCount);
      equipment.status = equipment.available_count > 0 ? "대여 가능" : "대여중";
      const rental = {
        id: `local:${operation.operation_id}`,
        equipment_id: equipment.id,
        equipment_name: equipment.name,
        renter_id: renter.id,
        user_id: renter.user_id,
        renter_name: renter.name,
        renter_phone: renter.phone,
        rent_date: operation.occurred_at,
        due_date: operation.payload.due_date || "",
        return_date: null,
        __local: true
      };
      state.snapshot.active_rentals.unshift(rental);
      state.snapshot.recent_rentals.unshift({ ...rental });
      return;
    }

    const rentalId = operation.payload.rental_id;
    const activeIndex = state.snapshot.active_rentals.findIndex((item) => sameId(rentalField(item, "id", "rental_id"), rentalId));
    if (activeIndex < 0) return;
    const [rental] = state.snapshot.active_rentals.splice(activeIndex, 1);
    const equipment = state.snapshot.equipment.find((item) => sameId(item.id, rental.equipment_id));
    if (equipment) {
      equipment.active_count = Math.max(0, numberValue(equipment.active_count) - 1);
      equipment.available_count = Math.max(0, numberValue(equipment.quantity) - numberValue(equipment.active_count));
      equipment.status = equipment.available_count > 0 ? "대여 가능" : "대여중";
    }
    const recent = state.snapshot.recent_rentals.find((item) => sameId(rentalField(item, "id", "rental_id"), rentalId));
    if (recent) {
      recent.return_date = operation.occurred_at;
      recent.__pending_return = true;
    } else {
      state.snapshot.recent_rentals.unshift({ ...rental, return_date: operation.occurred_at, __pending_return: true });
    }
  }

  async function syncPending({ silent = false } = {}) {
    if (state.syncPromise) return state.syncPromise;
    state.syncPromise = (async () => {
      state.syncing = true;
      updateConnectionUI();
      const pending = state.operations
        .filter((operation) => operation.status === "pending")
        .sort((first, second) => numberValue(first.created_at) - numberValue(second.created_at))
        .slice(0, 100);
      try {
        if (!pending.length) {
          const refreshed = await refreshSnapshot({ silent: true });
          if (!refreshed) throw new ApiError("서버에 연결할 수 없습니다.");
          state.lastSync = new Date().toISOString();
          await dbPut("meta", state.lastSync, "last-sync");
          if (!silent) showToast("서버의 최신 정보를 확인했습니다.");
          return true;
        }

        const response = await apiFetch("/api/sync", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            client_id: state.clientId,
            operations: pending.map(({ operation_id, type, database_id, occurred_at, payload }) => ({ operation_id, type, database_id, occurred_at, payload }))
          })
        });
        if (!Array.isArray(response?.results) || !response?.snapshot) {
          throw new ApiError("서버의 동기화 응답 형식이 올바르지 않습니다.");
        }
        const results = response.results;
        const appliedIds = new Set();
        for (const operation of pending) {
          const result = results.find((item) => item.operation_id === operation.operation_id);
          if (result?.status === "applied") {
            if (operation.type === "rent" && result.entity_id !== null && result.entity_id !== undefined) {
              const localRentalId = `local:${operation.operation_id}`;
              const dependentReturns = state.operations.filter((item) => (
                item.status === "pending" &&
                item.type === "return" &&
                sameId(item.payload?.rental_id, localRentalId)
              ));
              for (const dependent of dependentReturns) {
                dependent.payload.rental_id = result.entity_id;
                await dbPut("operations", dependent);
              }
            }
            appliedIds.add(operation.operation_id);
            await dbDelete("operations", operation.operation_id);
          } else if (result?.status === "conflict") {
            operation.status = "conflict";
            operation.message = result.message || "서버 데이터와 충돌했습니다.";
            operation.entity_id = result.entity_id ?? null;
            await dbPut("operations", operation);
          }
        }
        state.operations = state.operations.filter((operation) => !appliedIds.has(operation.operation_id));
        if (response?.snapshot) {
          replaceServerSnapshot(response.snapshot);
          await saveSnapshotBestEffort();
        } else {
          await refreshSnapshot({ silent: true });
        }
        state.lastSync = new Date().toISOString();
        await dbPut("meta", state.lastSync, "last-sync");
        state.statsLoaded = false;
        renderAll();
        if (state.activeView === "stats") await loadStats(false);
        const conflictCount = results.filter((item) => item.status === "conflict").length;
        if (!silent) {
          showToast(conflictCount ? `${conflictCount}개의 충돌을 확인해 주세요.` : `${appliedIds.size}개 작업을 동기화했습니다.`, conflictCount ? "error" : "success");
        }
        return true;
      } catch (error) {
        if (!silent) showToast(error.message, "error");
        return false;
      } finally {
        state.syncing = false;
        state.syncPromise = null;
        updateConnectionUI();
        renderSync();
        if (
          state.apiReachable === true &&
          state.operations.some((operation) => operation.status === "pending")
        ) {
          setTimeout(() => { void syncPending({ silent: true }); }, 0);
        }
      }
    })();
    return state.syncPromise;
  }

  async function deleteConflict(operationId) {
    await dbDelete("operations", operationId);
    state.operations = state.operations.filter((operation) => operation.operation_id !== operationId);
    renderSync();
    showToast("충돌 기록을 삭제했습니다.");
  }

  function openRentDialog(equipmentId) {
    const equipment = state.snapshot.equipment.find((item) => sameId(item.id, equipmentId));
    if (!equipment) return;
    const available = numberValue(equipment.available_count, numberValue(equipment.quantity) - numberValue(equipment.active_count));
    if (available <= 0) {
      showToast("대여 가능한 수량이 없습니다.", "error");
      return;
    }
    $("#rentEquipmentId").value = equipment.id;
    $("#rentEquipmentSummary").innerHTML = `<strong>${escapeHtml(equipment.name)}</strong><span>${escapeHtml(equipment.category || "미분류")} · ${available}개 대여 가능</span>`;
    const renterSelect = $("#rentRenterId");
    renterSelect.innerHTML = state.snapshot.renters.length
      ? `<option value="">대여자를 선택하세요</option>${state.snapshot.renters.map((renter) => `<option value="${escapeHtml(renter.id)}">${escapeHtml(renter.name)} · ${escapeHtml(renter.user_id)}</option>`).join("")}`
      : '<option value="">등록된 대여자가 없습니다</option>';
    $("#rentForm button[type='submit']").disabled = state.snapshot.renters.length === 0;
    showDialog("rentDialog");
  }

  function openReturnDialog({ equipmentId = null, rentalId = null } = {}) {
    let rentals = state.snapshot.active_rentals;
    if (equipmentId !== null) rentals = rentals.filter((rental) => sameId(rental.equipment_id, equipmentId));
    if (rentalId !== null) rentals = rentals.filter((rental) => sameId(rentalField(rental, "id", "rental_id"), rentalId));
    if (!rentals.length) {
      showToast("반납할 활성 대여를 찾을 수 없습니다.", "error");
      return;
    }
    $("#returnRentalOptions").innerHTML = rentals.map((rental, index) => {
      const id = rentalField(rental, "id", "rental_id");
      return `<label class="return-option"><input type="radio" name="rental_id" value="${escapeHtml(id)}" ${index === 0 ? "checked" : ""} required><span><strong>${escapeHtml(rentalField(rental, "equipment_name") || "장비")} · ${escapeHtml(rentalField(rental, "renter_name") || "대여자")}</strong><span>${escapeHtml(rentalField(rental, "user_id") || "-")} · 대여 ${escapeHtml(formatDateTime(rentalField(rental, "rent_date", "rented_at")))}</span></span></label>`;
    }).join("");
    showDialog("returnDialog");
  }

  async function loadStats(showErrors = true) {
    if (!requireOnline()) return;
    const form = $("#statsFilterForm");
    const submit = form.querySelector("button[type='submit']");
    submit.classList.add("is-busy");
    submit.disabled = true;
    const parameters = new URLSearchParams();
    const year = $("#statsYear").value || String(new Date().getFullYear());
    parameters.set("year", year);
    for (const [name, value] of [
      ["start_date", $("#statsStartDate").value],
      ["end_date", $("#statsEndDate").value],
      ["equipment_id", $("#statsEquipment").value]
    ]) {
      if (value) parameters.set(name, value);
    }
    try {
      state.stats = await apiFetch(`/api/stats?${parameters}`);
      state.statsLoaded = true;
      renderStats();
    } catch (error) {
      if (showErrors) showToast(error.message, "error");
    } finally {
      submit.classList.remove("is-busy");
      submit.disabled = state.apiReachable !== true;
    }
  }

  async function downloadReport(format) {
    if (!requireOnline()) return;
    const parameters = new URLSearchParams({
      format,
      year: $("#statsYear").value || String(new Date().getFullYear())
    });
    for (const [name, value] of [
      ["start_date", $("#statsStartDate").value],
      ["end_date", $("#statsEndDate").value],
      ["equipment_id", $("#statsEquipment").value]
    ]) {
      if (value) parameters.set(name, value);
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), API_TIMEOUT * 3);
    let serverResponded = false;
    try {
      const response = await fetch(`/api/stats/export?${parameters}`, {
        cache: "no-store",
        headers: { "X-ERM-Key": state.accessKey },
        signal: controller.signal
      });
      serverResponded = true;
      markServerReachable(true);
      if (!response.ok) {
        let message = `파일을 만들지 못했습니다. (${response.status})`;
        if ((response.headers.get("content-type") || "").includes("application/json")) {
          const body = await response.json();
          message = body.message || message;
        }
        throw new ApiError(message, response.status);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      const date = new Date().toISOString().slice(0, 10);
      anchor.href = objectUrl;
      anchor.download = `rental-stats-${date}.${format}`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
      showToast(`${format === "pdf" ? "PDF" : "Excel"} 파일 다운로드를 시작했습니다.`);
    } catch (error) {
      if (!serverResponded) markServerReachable(false);
      showToast(error.name === "AbortError" ? "파일 생성 시간이 초과되었습니다." : error.message, "error");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function submitJsonMutation(url, options, successFallback) {
    if (!requireOnline()) return null;
    const response = await apiFetch(url, options);
    showToast(response?.message || successFallback);
    await refreshSnapshot({ silent: true });
    return response;
  }

  function setButtonBusy(button, busy) {
    if (!button) return;
    button.classList.toggle("is-busy", busy);
    button.disabled = busy || (button.classList.contains("online-only") && state.apiReachable !== true);
  }

  function setupEvents() {
    document.addEventListener("click", async (event) => {
      const viewButton = event.target.closest("[data-view]");
      if (viewButton) {
        setView(viewButton.dataset.view);
        return;
      }
      const viewJump = event.target.closest("[data-view-jump]");
      if (viewJump) {
        setView(viewJump.dataset.viewJump);
        return;
      }
      const closeButton = event.target.closest("[data-close]");
      if (closeButton) {
        closeDialog(closeButton.closest("dialog"));
        return;
      }
      const openButton = event.target.closest("[data-open]");
      if (openButton) {
        if (openButton.classList.contains("online-only") && !requireOnline()) return;
        showDialog(openButton.dataset.open);
        return;
      }
      const rentButton = event.target.closest("[data-rent-equipment]");
      if (rentButton) {
        openRentDialog(rentButton.dataset.rentEquipment);
        return;
      }
      const returnEquipment = event.target.closest("[data-return-equipment]");
      if (returnEquipment) {
        openReturnDialog({ equipmentId: returnEquipment.dataset.returnEquipment });
        return;
      }
      const returnRental = event.target.closest("[data-return-rental]");
      if (returnRental) {
        openReturnDialog({ rentalId: returnRental.dataset.returnRental });
        return;
      }
      const syncButton = event.target.closest('[data-action="sync"]');
      if (syncButton) {
        state.authPromptBlocked = false;
        setButtonBusy(syncButton, true);
        await syncPending({ silent: false });
        setButtonBusy(syncButton, false);
        return;
      }
      const deleteEquipment = event.target.closest("[data-delete-equipment]");
      if (deleteEquipment) {
        if (!requireOnline()) return;
        const equipment = state.snapshot.equipment.find((item) => sameId(item.id, deleteEquipment.dataset.deleteEquipment));
        if (!equipment) return;
        const confirmed = await askConfirmation("장비를 삭제할까요?", `‘${equipment.name}’ 장비와 이미지를 삭제합니다. 기존 대여 내역은 유지됩니다.`, "삭제");
        if (!confirmed) return;
        try {
          await submitJsonMutation(`/api/equipment/${encodeURIComponent(equipment.id)}`, { method: "DELETE" }, "장비를 삭제했습니다.");
        } catch (error) {
          showToast(error.message, "error");
        }
        return;
      }
      const deleteRenter = event.target.closest("[data-delete-renter]");
      if (deleteRenter) {
        if (!requireOnline()) return;
        const renter = state.snapshot.renters.find((item) => sameId(item.id, deleteRenter.dataset.deleteRenter));
        if (!renter) return;
        const confirmed = await askConfirmation("대여자를 삭제할까요?", `‘${renter.name}’ 대여자를 삭제합니다. 활성 대여가 있으면 삭제할 수 없습니다.`, "삭제");
        if (!confirmed) return;
        try {
          await submitJsonMutation(`/api/renters/${encodeURIComponent(renter.id)}`, { method: "DELETE" }, "대여자를 삭제했습니다.");
        } catch (error) {
          showToast(error.message, "error");
        }
        return;
      }
      const deleteConflictButton = event.target.closest("[data-delete-conflict]");
      if (deleteConflictButton) {
        const confirmed = await askConfirmation("충돌 기록을 삭제할까요?", "이 기록은 서버에 다시 전송되지 않습니다.", "기록 삭제");
        if (confirmed) await deleteConflict(deleteConflictButton.dataset.deleteConflict);
        return;
      }
      const exportButton = event.target.closest("[data-export]");
      if (exportButton) {
        await downloadReport(exportButton.dataset.export);
        return;
      }
      const copyButton = event.target.closest('[data-action="copy-origin"]');
      if (copyButton) {
        try {
          await navigator.clipboard.writeText($("#serverOrigin").textContent || window.location.origin);
          showToast("서버 주소를 복사했습니다.");
        } catch {
          showToast("주소를 복사하지 못했습니다. 주소를 길게 눌러 복사하세요.", "error");
        }
        return;
      }
      const resetButton = event.target.closest('[data-action="reset-stats"]');
      if (resetButton) {
        if (!requireOnline()) return;
        const confirmed = await askConfirmation("통계 조정을 초기화할까요?", "모든 수동 조정을 삭제하고 자동 집계 값으로 되돌립니다.", "전체 초기화");
        if (!confirmed) return;
        try {
          const response = await apiFetch("/api/stats/override", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ equipment_id: null, stat_month: "", rent_count: 0, return_count: 0, mode: "reset" })
          });
          closeDialog($("#statsOverrideDialog"));
          showToast(response?.message || "통계 조정을 초기화했습니다.");
          await loadStats(false);
        } catch (error) {
          showToast(error.message, "error");
        }
      }
    });

    $("#equipmentSearch").addEventListener("input", renderEquipment);
    $("#renterSearch").addEventListener("input", renderRenters);
    $$('[data-rental-tab]').forEach((button) => {
      button.addEventListener("click", () => {
        state.rentalTab = button.dataset.rentalTab;
        $$('[data-rental-tab]').forEach((tab) => {
          const active = tab === button;
          tab.classList.toggle("is-active", active);
          tab.setAttribute("aria-selected", String(active));
        });
        renderRentals();
      });
    });

    $("#rentForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const data = new FormData(form);
      const button = form.querySelector("button[type='submit']");
      setButtonBusy(button, true);
      try {
        const saved = await queueOperation("rent", {
          equipment_id: payloadId(data.get("equipment_id")),
          renter_id: payloadId(data.get("renter_id")),
          due_date: String(data.get("due_date") || "")
        });
        if (saved) {
          closeDialog($("#rentDialog"));
          form.reset();
        }
      } finally {
        setButtonBusy(button, false);
      }
    });

    $("#returnForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const rentalId = new FormData(form).get("rental_id");
      const button = form.querySelector("button[type='submit']");
      setButtonBusy(button, true);
      try {
        const saved = await queueOperation("return", { rental_id: payloadId(rentalId) });
        if (saved) closeDialog($("#returnDialog"));
      } finally {
        setButtonBusy(button, false);
      }
    });

    $("#equipmentForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!requireOnline()) return;
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const button = form.querySelector("button[type='submit']");
      setButtonBusy(button, true);
      try {
        const response = await apiFetch("/api/equipment", { method: "POST", body: new FormData(form) });
        showToast(response?.message || "장비를 등록했습니다.");
        form.reset();
        closeDialog($("#equipmentDialog"));
        await refreshSnapshot({ silent: true });
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    $("#renterForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!requireOnline()) return;
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const button = form.querySelector("button[type='submit']");
      const data = new FormData(form);
      setButtonBusy(button, true);
      try {
        const response = await apiFetch("/api/renters", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            user_id: String(data.get("user_id") || "").trim(),
            name: String(data.get("name") || "").trim(),
            phone: String(data.get("phone") || "").trim()
          })
        });
        showToast(response?.message || "대여자를 등록했습니다.");
        form.reset();
        closeDialog($("#renterDialog"));
        await refreshSnapshot({ silent: true });
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    $("#statsFilterForm").addEventListener("submit", (event) => {
      event.preventDefault();
      void loadStats(true);
    });

    $("#statsOverrideForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!requireOnline()) return;
      const form = event.currentTarget;
      if (!form.reportValidity()) return;
      const button = form.querySelector("button[type='submit']");
      const data = new FormData(form);
      setButtonBusy(button, true);
      try {
        const response = await apiFetch("/api/stats/override", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            equipment_id: payloadId(data.get("equipment_id")),
            stat_month: String(data.get("stat_month") || ""),
            rent_count: numberValue(data.get("rent_count")),
            return_count: numberValue(data.get("return_count")),
            mode: String(data.get("mode") || "save")
          })
        });
        showToast(response?.message || "통계를 조정했습니다.");
        closeDialog($("#statsOverrideDialog"));
        await loadStats(false);
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        setButtonBusy(button, false);
      }
    });

    $("#overrideMode").addEventListener("change", (event) => {
      const removeMode = event.target.value === "remove";
      for (const name of ["rent_count", "return_count"]) {
        const input = $(`[name='${name}']`, $("#statsOverrideForm"));
        input.disabled = removeMode;
        if (removeMode) input.value = "0";
      }
    });

    $$("dialog.modal").forEach((dialog) => {
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog && dialog.id !== "confirmDialog") closeDialog(dialog);
      });
      dialog.addEventListener("close", () => {
        if (state.lastFocused instanceof HTMLElement && document.contains(state.lastFocused)) state.lastFocused.focus();
      });
    });

    window.addEventListener("online", () => { void syncPending({ silent: true }); });
    window.addEventListener("offline", () => markServerReachable(false));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") void syncPending({ silent: true });
    });
  }

  async function initialize() {
    state.clientId = getClientId();
    const bootstrapKey = document.querySelector('meta[name="erm-bootstrap-key"]')?.content || "";
    try {
      state.accessKey = bootstrapKey || localStorage.getItem("erm-access-key") || "";
      if (bootstrapKey) localStorage.setItem("erm-access-key", bootstrapKey);
    } catch {
      state.accessKey = bootstrapKey;
    }
    $("#serverOrigin").textContent = window.location.origin;
    $("#clientIdLabel").textContent = state.clientId;
    renderStatsYears({ selected_year: new Date().getFullYear(), years: [new Date().getFullYear()] });
    renderStats();
    setupEvents();
    await loadLocalData();
    renderAll();
    await syncPending({ silent: true });

    if (navigator.storage?.persist) {
      navigator.storage.persist().catch(() => false);
    }

    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch((error) => {
        console.warn("서비스워커를 등록하지 못했습니다.", error);
      });
    }

    setInterval(() => {
      if (document.visibilityState === "visible") void syncPending({ silent: true });
    }, POLL_INTERVAL);
  }

  document.addEventListener("DOMContentLoaded", () => {
    initialize().catch((error) => {
      console.error("앱을 시작하지 못했습니다.", error);
      showToast("앱을 시작하는 중 문제가 발생했습니다.", "error");
    });
  });
})();
