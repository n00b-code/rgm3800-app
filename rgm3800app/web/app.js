"use strict";

// ---- element refs ---------------------------------------------------------
const $ = (id) => document.getElementById(id);
const portSelect = $("port-select");
const btnRefresh = $("btn-refresh");
const btnConnect = $("btn-connect");
const connStatus = $("conn-status");
const connText = $("conn-text");
const btnDownload = $("btn-download");
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
let tracks = []; // [{index,number,date,start_time,num_points,distance_km}]

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

function fmtDate(iso, startTime) {
  // iso "2025-05-12" -> "12.05." plus time
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.` + (startTime ? ` ${startTime}` : "");
}
function fmtDist(km) {
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
    o.textContent = "Kein Anschluss gefunden";
    o.disabled = true;
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
    btnDownload.disabled = true;
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
  btnDownload.disabled = false;
  setStatus(`Verbunden · ${res.status.num_tracks} Tracks auf dem Gerät · ${res.status.port}`);
}

// ---- download -------------------------------------------------------------
async function startDownload() {
  btnDownload.disabled = true;
  progressWrap.hidden = false;
  progressBar.style.width = "0%";
  progressLabel.textContent = "Starte …";
  setStatus("Lade Tracklogs …");
  setTrackRows([]); // clear
  const res = await api().download();
  if (!res.ok) {
    showToast(res.error, "err");
    progressWrap.hidden = true;
    btnDownload.disabled = false;
    setStatus("Download fehlgeschlagen");
  }
}

window.onProgress = function (p) {
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  progressBar.style.width = pct + "%";
  progressLabel.textContent = `${p.done} / ${p.total} Tracks · ${pct} %`;
};

window.onDownloadDone = function (res) {
  btnDownload.disabled = false;
  if (!res.ok) {
    progressWrap.hidden = true;
    showToast(res.error, "err");
    setStatus(res.error);
    return;
  }
  tracks = res.tracks;
  setTrackRows(tracks);
  progressBar.style.width = "100%";
  progressLabel.textContent = `${tracks.length} / ${tracks.length} Tracks · 100 %`;
  setTimeout(() => { progressWrap.hidden = true; }, 700);
  showToast(`${tracks.length} Tracks geladen`, "ok");
  setStatus(`Bereit · ${tracks.length} Tracks geladen`);
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
    tracksBody.innerHTML = '<tr class="empty"><td colspan="4">Noch keine Tracks geladen.</td></tr>';
    updateSelection();
    return;
  }
  for (const t of rows) {
    const tr = document.createElement("tr");
    tr.dataset.index = t.index;
    tr.innerHTML = `
      <td class="col-check"><input type="checkbox" class="row-check" data-index="${t.index}"></td>
      <td class="col-date">${fmtDate(t.date, t.start_time)}</td>
      <td class="col-num">${t.num_points.toLocaleString("de-DE")}</td>
      <td class="col-dist">${fmtDist(t.distance_km)}</td>`;
    tracksBody.appendChild(tr);
  }
  tracksBody.querySelectorAll(".row-check").forEach((cb) => {
    cb.addEventListener("change", onRowToggle);
  });
  // clicking a row toggles its checkbox
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

// ---- export ---------------------------------------------------------------
async function doExport() {
  const indices = selectedIndices();
  if (indices.length === 0) {
    showToast("Bitte zuerst mindestens einen Track auswählen.", "err");
    return;
  }
  const fmt = document.querySelector('input[name="fmt"]:checked').value;
  setStatus(`Exportiere ${indices.length} Track(s) als ${fmt.toUpperCase()} …`);
  btnExport.disabled = true;
  const res = await api().export(indices, fmt);
  btnExport.disabled = false;
  if (res.cancelled) { setStatus("Export abgebrochen"); return; }
  if (!res.ok) { showToast(res.error, "err"); setStatus("Export fehlgeschlagen"); return; }
  const r = res.result;
  showToast(`${r.tracks} Track(s) → ${fmt.toUpperCase()} gespeichert`, "ok");
  setStatus(`Gespeichert: ${r.path} (${r.points.toLocaleString("de-DE")} Punkte)`);
}

// ---- wire up --------------------------------------------------------------
btnRefresh.addEventListener("click", refreshPorts);
btnConnect.addEventListener("click", toggleConnect);
btnDownload.addEventListener("click", startDownload);
btnAll.addEventListener("click", () => selectAll(true));
btnNone.addEventListener("click", () => selectAll(false));
checkHeader.addEventListener("change", (e) => selectAll(e.target.checked));
btnExport.addEventListener("click", doExport);

window.addEventListener("pywebviewready", refreshPorts);
// Fallback if the event already fired before listener attached.
if (window.pywebview && window.pywebview.api) refreshPorts();
