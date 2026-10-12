/* Deterministic, lossless USB reconciliation. Numeric UI IDs are device-local;
   _sync IDs and ancestry identify the same record across disconnected devices. */
(() => {
  "use strict";
  const kinds = ["equipment", "renters", "rentals", "overrides"];
  const uuid = () => crypto.randomUUID();
  const keyFor = (kind, item) => `${kind}:${item._sync.id}`;
  const fail = (message, conflict = false) => { const error = new Error(message); error.conflict = conflict; throw error; };
  const validId = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
  function validateMetadata(s) {
    if (!s || !validId(s.id) || !validId(s.rev) || !Array.isArray(s.parents) || s.parents.some(p => !validId(p))) fail("동기화 식별자가 손상된 기록입니다.");
  }
  const revision = previous => ({ id: previous.id, rev: uuid(), parents: [...new Set([...previous.parents, previous.rev])] });
  const metadata = id => ({ id: id || uuid(), rev: uuid(), parents: [] });

  function prepare(data) {
    data.sync_equipment_ids ||= {};
    data.sync_renter_ids ||= {};
    data.sync_tombstones ||= [];
    for (const kind of kinds) for (const item of data[kind]) {
      const catalog = kind === "equipment" ? data.sync_equipment_ids : kind === "renters" ? data.sync_renter_ids : null;
      item._sync ||= metadata(catalog?.[item.id]);
      const s = item._sync;
      validateMetadata(s);
      if (catalog) catalog[item.id] = s.id;
    }
    for (const rental of data.rentals) {
      rental._equipment_uid ||= data.sync_equipment_ids[rental.equipment_id] ||= uuid();
      rental._renter_uid ||= data.sync_renter_ids[rental.renter_id] ||= uuid();
      if (!validId(rental._equipment_uid) || !validId(rental._renter_uid)) fail("대여 기록의 동기화 연결이 손상되었습니다.");
    }
    for (const item of data.overrides) {
      item._equipment_uid ||= data.sync_equipment_ids[item.equipment_id] ||= uuid();
      if (!validId(item._equipment_uid)) fail("통계 기록의 동기화 연결이 손상되었습니다.");
    }
    return data;
  }

  function records(data) {
    const map = new Map();
    for (const kind of kinds) for (const item of data[kind]) {
      const key = keyFor(kind, item);
      if (map.has(key)) fail("동기화 식별자가 중복되었습니다.");
      map.set(key, { kind, item, deleted: false });
    }
    for (const record of data.sync_tombstones || []) {
      if (!kinds.includes(record.kind) || !record.item?._sync) fail("삭제 기록의 동기화 형식이 올바르지 않습니다.");
      validateMetadata(record.item._sync);
      const key = keyFor(record.kind, record.item);
      if (map.has(key)) fail("같은 항목에 활성 기록과 삭제 기록이 함께 있습니다.");
      map.set(key, { ...record, deleted: true });
    }
    return map;
  }

  function content(record) {
    const item = { ...record.item };
    delete item._sync;
    delete item.id;
    if (record.kind === "rentals") { delete item.equipment_id; delete item.renter_id; }
    if (record.kind === "overrides") delete item.equipment_id;
    return JSON.stringify([record.deleted, Object.keys(item).sort().map(key => [key, item[key]])]);
  }

  function track(before, after) {
    prepare(after);
    const previous = records(before), current = records(after);
    for (const [key, record] of current) {
      const old = previous.get(key);
      if (old && content(old) !== content(record)) {
        const parents = [...new Set([...old.item._sync.parents, old.item._sync.rev,
          ...record.item._sync.parents, record.item._sync.rev])];
        record.item._sync = { id: old.item._sync.id, rev: uuid(), parents };
      }
    }
    for (const [key, record] of previous) {
      if (current.has(key)) continue;
      if (record.deleted) { after.sync_tombstones.push(record); continue; }
      const item = structuredClone(record.item);
      item._sync = revision(item._sync);
      if (record.kind === "equipment") item.image_url = "";
      after.sync_tombstones.push({ kind: record.kind, item, deleted: true });
    }
  }

  const label = record => record.item.name || record.item.equipment_name || record.item.user_id || "기록";
  function choose(left, right) {
    const a = left.item._sync, b = right.item._sync;
    if (a.rev === b.rev) {
      if (content(left) !== content(right)) fail(`동기화 기록이 손상되었습니다: ${label(left)}`);
      return left;
    }
    if (a.parents.includes(b.rev)) return left;
    if (b.parents.includes(a.rev)) return right;
    if (content(left) === content(right)) {
      // Equal concurrent edits converge on a deterministic revision.
      const chosen = structuredClone(a.rev < b.rev ? left : right);
      chosen.item._sync.parents = [...new Set([...a.parents, ...b.parents, a.rev, b.rev])].filter(p => p !== chosen.item._sync.rev);
      return chosen;
    }
    if (left.kind === "rentals" && !left.deleted && !right.deleted) {
      const withoutReturn = record => content({ ...record, item: { ...record.item, return_date: null } });
      if (withoutReturn(left) === withoutReturn(right) && left.item.return_date && right.item.return_date) {
        const chosen = structuredClone(left.item.return_date < right.item.return_date ? left : right);
        chosen.item._sync.parents = [...new Set([...a.parents, ...b.parents, a.rev, b.rev])].filter(p => p !== chosen.item._sync.rev);
        return chosen;
      }
    }
    fail(`양쪽에서 같은 기록을 다르게 변경했습니다: ${label(left)}. 백업 후 USB 화면의 충돌 해결을 사용하세요.`, true);
  }

  function merge(first, second, preference = "") {
    const left = prepare(structuredClone(first)), right = prepare(structuredClone(second));
    const a = records(left), b = records(right), union = new Map();
    const conflicts = [];
    for (const key of [...new Set([...a.keys(), ...b.keys()])].sort()) {
      if (!a.has(key)) union.set(key, b.get(key));
      else if (!b.has(key)) union.set(key, a.get(key));
      else {
        try { union.set(key, choose(a.get(key), b.get(key))); }
        catch (error) {
          if (!preference || !error.conflict) { conflicts.push(error.message); continue; }
          const chosen = structuredClone(preference === "pc" ? a.get(key) : b.get(key));
          const s1 = a.get(key).item._sync, s2 = b.get(key).item._sync;
          // Both clients receive the same resolution preference. Keep the selected
          // revision and acknowledge the rejected revision as an ancestor.
          chosen.item._sync.parents = [...new Set([...s1.parents, ...s2.parents, s1.rev, s2.rev])].filter(p => p !== chosen.item._sync.rev);
          union.set(key, chosen);
        }
      }
    }
    if (conflicts.length) fail(conflicts.join("\n"));
    const result = { schema: 1, database_id: [left.database_id, right.database_id].sort()[0], version: 0,
      equipment: [], renters: [], rentals: [], overrides: [], sync_tombstones: [], sync_equipment_ids: {}, sync_renter_ids: {}, sequences: {} };
    const equipment = new Set(), renters = new Set();
    for (const { kind, item } of union.values()) {
      if (kind === "equipment") equipment.add(item._sync.id);
      if (kind === "renters") renters.add(item._sync.id);
      if (item._equipment_uid) equipment.add(item._equipment_uid);
      if (item._renter_uid) renters.add(item._renter_uid);
    }
    const equipmentIds = new Map([...equipment].sort().map((id, index) => [id, index + 1]));
    const renterIds = new Map([...renters].sort().map((id, index) => [id, index + 1]));
    for (const [id, number] of equipmentIds) result.sync_equipment_ids[number] = id;
    for (const [id, number] of renterIds) result.sync_renter_ids[number] = id;
    let rentalId = 0;
    for (const record of union.values()) {
      const { kind, deleted } = record, item = structuredClone(record.item);
      if (kind === "equipment") item.id = equipmentIds.get(item._sync.id);
      if (kind === "renters") item.id = renterIds.get(item._sync.id);
      if (kind === "rentals") { item.id = ++rentalId; item.renter_id = renterIds.get(item._renter_uid); }
      if (kind === "rentals" || kind === "overrides") item.equipment_id = equipmentIds.get(item._equipment_uid);
      if (deleted) result.sync_tombstones.push({ kind, item, deleted: true });
      else result[kind].push(item);
    }
    result.sequences = { equipment: equipmentIds.size, renters: renterIds.size, rentals: rentalId };
    const users = new Set();
    for (const user of result.renters) {
      if (users.has(user.user_id)) fail(`양쪽에서 별도로 등록한 대여자 아이디가 겹칩니다: ${user.user_id}. 한쪽의 중복 대여자를 정리한 뒤 다시 시도하세요.`);
      users.add(user.user_id);
    }
    for (const rental of result.rentals.filter(r => !r.return_date)) {
      if (!result.equipment.some(e => e.id === rental.equipment_id) || !result.renters.some(r => r.id === rental.renter_id)) {
        fail(`삭제와 대여가 충돌합니다: ${rental.equipment_name} / ${rental.renter_name}. 대여한 기기에서 기록을 확인하고 반납 후 다시 시도하세요.`);
      }
    }
    for (const item of result.equipment) {
      const count = result.rentals.filter(r => !r.return_date && r.equipment_id === item.id).length;
      if (count > item.quantity) fail(`수량 충돌: ${item.name} 보유 ${item.quantity}개, 양쪽 대여 합계 ${count}개. 잘못 입력한 대여를 해당 기기에서 반납한 뒤 다시 시도하세요.`);
    }
    const months = new Set();
    for (const item of result.overrides) {
      const key = `${item.equipment_id}:${item.stat_month}`;
      if (months.has(key)) fail(`같은 통계 월을 양쪽에서 새로 조정했습니다: ${item.equipment_name} ${item.stat_month}. 한쪽에서 통계 조정을 초기화한 뒤 다시 시도하세요.`);
      months.add(key);
    }
    return result;
  }
  window.ERMUsbMerge = { prepare, track, merge };
})();
