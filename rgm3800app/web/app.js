"use strict";

const $ = (id) => document.getElementById(id);
const portSelect = $("port-select");
const btnRefresh = $("btn-refresh");
const btnConnect = $("btn-connect");
const connStatus = $("conn-status");
const connText = $("conn-text");
const progressWrap = $("progress-wrap");
const progressBar = $("progress-bar");
const progressLabel = $("progress-label");
const btnAll = $("btn-all");
const btnNone = $("btn-none");
const checkHeader = $("check-header");
const selCount = $("sel-count");
const tracksBody = $("tracks-body");
const btnExport = $("btn-export");
const statusbar = $("statusbar");
const toast = $("toast");

let connected = false;
let tracks = []; // [{index,number,date_label,year,num_points,distance_km,start_time,downloaded}]

// ---- helpers --------------------------------------------------------------
function api() { return window.pywebview.api; }
function setStatus(text) { statusbar.textContent = text; }

let toastTimer = null;
function showToast(msg, kind = "") {
  toast.textContent = msg;
  toast.className = "toast show " + kind;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = "toast " + kind;
    setTimeout(() => { toast.hidden = true; }, 250);
  }, 3200);
}

function setConn(state, text) {
  connStatus.className = "status status-" + state;
  connText.textContent = text;
}

function showProgress(label, pct) {
  progressWrap.hidden = false;
  progressBar.style.width = (pct == null ? 0 : pct) + "%";
  progressLabel.textContent = label;
}
function hideProgress() { progressWrap.hidden = true; }

function fmtDist(km) {
  if (km == null) return "—";
  return km >= 0.05 ? km.toFixed(1).replace(".", ",") + " km" : "—";
}

// ---- ports / connection ---------------------------------------------------
async function refreshPorts() {
  setStatus("Suche Anschlüsse …");
  const res = await api().list_ports();
  portSelect.innerHTML = "";
  if (!res.ok) { showToast(res.error, "err"); return; }
  if (res.ports.length === 0) {
    const o = document.createElement("option");
    o.textContent = "Kein Anschluss gefunden"; o.disabled = true;
    portSelect.appendChild(o);
    setStatus("Kein serieller Anschluss gefunden");
    return;
  }
  for (const p of res.ports) {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = p.device + (p.is_rgm ? "  ·  RGM-3800" : "");
    if (p.is_rgm) o.selected = true;
    portSelect.appendChild(o);
  }
  setStatus(`${res.ports.length} Anschluss/Anschlüsse gefunden`);
}

async function toggleConnect() {
  if (connected) {
    await api().disconnect();
    connected = false;
    btnConnect.textContent = "Verbinden";
    btnConnect.classList.remove("connected");
    setConn("idle", "Nicht verbunden");
    setTrackRows([]);
    tracksBody.innerHTML = '<tr class="empty"><td colspan="5">Nicht verbunden.</td></tr>';
    setStatus("Verbindung getrennt");
    return;
  }
  const port = portSelect.value;
  if (!port) { showToast("Bitte einen Anschluss wählen.", "err"); return; }
  setConn("busy", "Verbinde …");
  btnConnect.disabled = true;
  const res = await api().connect(port);
  btnConnect.disabled = false;
  if (!res.ok) {
    setConn("err", "Verbindung fehlgeschlagen");
    showToast(res.error, "err");
    setStatus("Fehler beim Verbinden");
    return;
  }
  connected = true;
  btnConnect.textContent = "Trennen";
  btnConnect.classList.add("connected");
  setConn("ok", `Verbunden mit RGM-3800 (${res.status.num_tracks} Tracks)`);
  setStatus(`Verbunden · ${res.status.port} · lese Track-Liste …`);
  // Immediately fetch the track headers (fast, no point data yet).
  loadTrackList();
}

// ---- track list (headers only) -------------------------------------------
async function loadTrackList() {
  showProgress("Lese Track-Liste …", 0);
  tracksBody.innerHTML = '<tr class="empty"><td colspan="5">Lese Track-Liste …</td></tr>';
  const res = await api().list_tracks();
  if (!res.ok) { hideProgress(); showToast(res.error, "err"); setStatus(res.error); }
}

window.onListProgress = function (p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  showProgress(`Lese Track-Liste … ${p.done} / ${p.total}`, pct);
};

window.onTrackList = function (res) {
  hideProgress();
  if (!res.ok) {
    showToast(res.error, "err");
    setStatus(res.error);
    tracksBody.innerHTML = `<tr class="empty"><td colspan="5">${res.error}</td></tr>`;
    return;
  }
  tracks = res.tracks;
  setTrackRows(tracks);
  setStatus(`Bereit · ${tracks.length} Tracks auf dem Gerät · Tracks auswählen und exportieren`);
};

// ---- tracks table ---------------------------------------------------------
function setTrackRows(rows) {
  tracksBody.innerHTML = "";
  const enable = rows.length > 0;
  btnAll.disabled = !enable;
  btnNone.disabled = !enable;
  checkHeader.disabled = !enable;
  checkHeader.checked = false;
  if (!enable) {
    tracksBody.innerHTML = '<tr class="empty"><td colspan="5">Keine Tracks.</td></tr>';
    updateSelection();
    return;
  }
  for (const t of rows) {
    const tr = document.createElement("tr");
    tr.dataset.index = t.index;
    tr.innerHTML = `
      <td class="col-check"><input type="checkbox" class="row-check" data-index="${t.index}"></td>
      <td class="col-date">${t.date_label}</td>
      <td class="col-time" data-field="time">${t.start_time || "—"}</td>
      <td class="col-num" data-field="num">${t.num_points.toLocaleString("de-DE")}</td>
      <td class="col-dist" data-field="dist">${fmtDist(t.distance_km)}</td>`;
    tracksBody.appendChild(tr);
  }
  tracksBody.querySelectorAll(".row-check").forEach((cb) => {
    cb.addEventListener("change", onRowToggle);
  });
  tracksBody.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return;
      const cb = tr.querySelector(".row-check");
      cb.checked = !cb.checked;
      onRowToggle();
    });
  });
  updateSelection();
}

function onRowToggle() {
  tracksBody.querySelectorAll(".row-check").forEach((cb) => {
    cb.closest("tr").classList.toggle("selected", cb.checked);
  });
  const total = tracksBody.querySelectorAll(".row-check").length;
  const sel = selectedIndices().length;
  checkHeader.checked = sel === total && total > 0;
  checkHeader.indeterminate = sel > 0 && sel < total;
  updateSelection();
}

function selectedIndices() {
  return [...tracksBody.querySelectorAll(".row-check:checked")]
    .map((cb) => parseInt(cb.dataset.index, 10));
}

function updateSelection() {
  const n = selectedIndices().length;
  selCount.textContent = n ? `${n} ausgewählt` : "";
  btnExport.disabled = n === 0;
}

function selectAll(on) {
  tracksBody.querySelectorAll(".row-check").forEach((cb) => { cb.checked = on; });
  onRowToggle();
}

// ---- export (download selected + write) -----------------------------------
async function doExport() {
  const indices = selectedIndices();
  if (indices.length === 0) {
    showToast("Bitte zuerst mindestens einen Track auswählen.", "err");
    return;
  }
  const fmt = document.querySelector('input[name="fmt"]:checked').value;
  btnExport.disabled = true;
  setStatus(`Lade ${indices.length} ausgewählte(n) Track(s) …`);
  const res = await api().export(indices, fmt);
  if (res.cancelled) { btnExport.disabled = false; setStatus("Export abgebrochen"); return; }
  if (!res.ok) {
    btnExport.disabled = false;
    showToast(res.error, "err");
    setStatus("Export fehlgeschlagen");
    return;
  }
  showProgress("Starte …", 0);
}

window.onProgress = function (p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  showProgress(`Lade Track ${p.done} / ${p.total} · ${pct} %`, pct);
};

window.onTracksUpdated = function (msg) {
  for (const u of msg.updates) {
    const row = tracksBody.querySelector(`tr[data-index="${u.index}"]`);
    if (!row) continue;
    row.querySelector('[data-field="time"]').textContent = u.start_time || "—";
    row.querySelector('[data-field="num"]').textContent = u.num_points.toLocaleString("de-DE");
    row.querySelector('[data-field="dist"]').textContent = fmtDist(u.distance_km);
    const t = tracks.find((x) => x.index === u.index);
    if (t) Object.assign(t, u);
  }
};

window.onExportDone = function (res) {
  btnExport.disabled = false;
  if (!res.ok) {
    hideProgress();
    showToast(res.error, "err");
    setStatus(res.error);
    return;
  }
  showProgress("Fertig", 100);
  setTimeout(hideProgress, 700);
  const r = res.result;
  showToast(`${r.tracks} Track(s) → ${r.format.toUpperCase()} gespeichert`, "ok");
  setStatus(`Gespeichert: ${r.path} (${r.points.toLocaleString("de-DE")} Punkte)`);
};

// ---- wire up --------------------------------------------------------------
btnRefresh.addEventListener("click", refreshPorts);
btnConnect.addEventListener("click", toggleConnect);
btnAll.addEventListener("click", () => selectAll(true));
btnNone.addEventListener("click", () => selectAll(false));
checkHeader.addEventListener("change", (e) => selectAll(e.target.checked));
btnExport.addEventListener("click", doExport);

window.addEventListener("pywebviewready", refreshPorts);
if (window.pywebview && window.pywebview.api) refreshPorts();
