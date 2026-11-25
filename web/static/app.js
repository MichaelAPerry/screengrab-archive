"use strict";
const $ = (s, el = document) => el.querySelector(s);
const form = $("#search"), q = $("#q"), results = $("#results"), statusEl = $("#status"), more = $("#more");
const sel = { speaker: $("#speaker"), season: $("#season"), episode: $("#episode") };
let facets = { episodes: [], speakers: [] }, offset = 0, total = 0, lastKey = "";
const PAGE = 24;

/* Two backends with the same shapes: the local FastAPI server, or the static public
 * build (window.ARCHIVE_CONFIG = {mode: "static", dataBase, imageBase}) searched in-browser. */
const CONFIG = Object.assign({ mode: "server", imageBase: "/images/", dataBase: "data/" }, window.ARCHIVE_CONFIG || {});
const imgUrl = (rel) => CONFIG.imageBase + rel;
const shareKey = (image) => {
  const m = /(S\d{2}E\d{2})_(\d+)\.\w+$/.exec(image || "");
  return m ? `${m[1]}-${Number(m[2])}` : null;
};
/* Permalink whose link preview shows the screengrab (see archive/share.py). */
function shareUrl(image) {
  const key = shareKey(image);
  if (!key) return null;
  if (CONFIG.mode !== "static") return `${location.origin}/q/${key}.html`;
  if (CONFIG.shareBase) return `${CONFIG.shareBase}${key}.html`;
  return `${CONFIG.siteUrl || location.origin + location.pathname}?id=${key}`;
}
const serverBackend = {
  lineIdForKey: async (key) => { const r = await fetch(`/api/key/${encodeURIComponent(key)}`); return r.ok ? (await r.json()).id : null; },
  facets: async () => (await fetch("/api/facets")).json(),
  search: async (p) => (await fetch(`/api/search?${p}`)).json(),
  dialogue: async (id) => (await fetch(`/api/dialogue/${id}`)).json(),
};
let backend = serverBackend;
async function initBackend() {
  if (CONFIG.mode !== "static") return;
  statusEl.textContent = "Loading dialogue…";
  const engine = await new window.ArchiveEngine.StaticEngine(CONFIG.dataBase).load();
  statusEl.textContent = "";
  backend = {
    facets: async () => engine.facets(),
    search: async (p) => engine.search(p.get("q") || "", { speaker: p.get("speaker"), season: p.get("season"),
      episode: p.get("episode"), limit: Number(p.get("limit")), offset: Number(p.get("offset")) }),
    dialogue: async (id) => engine.dialogue(id),
    lineIdForKey: async (key) => engine.lineIdForKey(key),
  };
}

const escapeHtml = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function highlight(text, query) {
  const terms = [...query.matchAll(/"([^"]+)"|(?:^|\s)(?!\w+:)([^\s"]+)/g)]
    .map((m) => (m[1] || m[2] || "").trim()).filter((t) => t.length > 1)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "[\\s\\W]+"));
  let html = escapeHtml(text);
  if (!terms.length) return html;
  return html.replace(new RegExp(`(${terms.join("|")})`, "gi"), "<mark>$1</mark>");
}

function params() {
  const p = new URLSearchParams();
  if (q.value.trim()) p.set("q", q.value.trim());
  for (const [k, el] of Object.entries(sel)) if (el.value) p.set(k, el.value);
  return p;
}

function fillEpisodes() {
  const season = sel.season.value, current = sel.episode.value;
  sel.episode.innerHTML = '<option value="">Any episode</option>' + facets.episodes
    .filter((e) => !season || String(e.season) === season)
    .map((e) => `<option value="${e.episode_code}">${e.episode_code} — ${escapeHtml(e.title || "")}</option>`).join("");
  sel.episode.value = [...sel.episode.options].some((o) => o.value === current) ? current : "";
}

async function loadFacets() {
  facets = await backend.facets();
  if (facets.series && facets.series !== "Local media") {
    const title = `${facets.series} · Screengrab Archive`;
    document.title = title;
    $("h1 a").textContent = title;
  }
  const seasons = [...new Set(facets.episodes.map((e) => e.season))];
  sel.season.innerHTML += seasons.map((s) => `<option value="${s}">Season ${s}</option>`).join("");
  sel.speaker.hidden = !facets.speakers.length;  // no speaker data: don't show an empty filter
  document.body.classList.toggle("no-speakers", !facets.speakers.length);
  sel.speaker.innerHTML += facets.speakers.map((s) => `<option value="${escapeHtml(s.speaker)}">${escapeHtml(s.speaker)}</option>`).join("");
  fillEpisodes();
}

function card(r, query) {
  const node = $("#card").content.firstElementChild.cloneNode(true);
  const img = r.image ? imgUrl(r.image) : null;
  if (img) {
    $("img", node).src = img;
    $("img", node).alt = `${r.speaker || ""} — ${r.text}`;
    $(".open", node).href = img;
    $(".download", node).href = img;
    $(".download", node).download = r.image.split("/").pop();
  } else node.classList.add("noimage");
  $(".speaker", node).textContent = r.speaker || "";
  $(".text", node).innerHTML = highlight(r.text, query);
  $(".code", node).textContent = r.episode_code;
  $(".title", node).textContent = r.title || "";
  $(".when", node).innerHTML = r.timestamp
    ? `${r.timestamp}${r.timing_source === "aligned" ? ` <span class="badge" title="timing matched from subtitles, confidence ${r.timing_confidence}">aligned</span>` : ""}`
    : '<span class="badge">no timing</span>';
  const url = shareUrl(r.image);
  if (url) $(".share", node).addEventListener("click", () => share(url, r));
  else $(".share", node).hidden = true;
  const open = () => showDetail(r.id);
  $(".frame", node).addEventListener("click", open);
  $(".ctx", node).addEventListener("click", open);
  return node;
}

async function run(append = false) {
  const p = params(), key = p.toString();
  if (!append) { offset = 0; history.replaceState(null, "", key ? `?${key}` : location.pathname); }
  if (!key) { results.innerHTML = ""; statusEl.textContent = ""; more.hidden = true; return; }
  lastKey = key;
  p.set("limit", PAGE); p.set("offset", offset);
  const data = await backend.search(p);
  if (key !== lastKey) return; // a newer query won
  if (!append) results.innerHTML = "";
  total = data.total;
  data.results.forEach((r) => results.appendChild(card(r, q.value)));
  offset += data.results.length;
  statusEl.textContent = total === 0 ? "No matches."
    : `${total.toLocaleString()} match${total === 1 ? "" : "es"}${data.mode === "any" ? " (no line had every word; showing lines with any of them)" : ""}`;
  more.hidden = offset >= total || data.mode === "any" && offset >= 500;
}

async function showDetail(id) {
  const d = await backend.dialogue(id);
  const L = d.line, dlg = $("#detail"), body = $("#detail-body");
  const main = d.images[0];
  body.innerHTML = `
    ${main ? `<img class="detail-img" src="${imgUrl(main.filename)}" alt="">` : ""}
    ${d.images.length > 1 ? `<div class="alts">${d.images.map((i, n) => `<img src="${imgUrl(i.filename)}" data-f="${i.filename}" class="${n ? "" : "on"}" title="${i.timestamp} · quality ${i.quality_score ?? "?"}">`).join("")}</div>` : ""}
    <h2 style="margin:12px 0 2px;font-size:.9rem;letter-spacing:.06em">${escapeHtml(L.speaker || "")}</h2>
    <p style="font-size:1.25rem;margin:0">“${escapeHtml(L.text)}”</p>
    <p style="color:var(--muted);margin:6px 0">${L.episode_code} — ${escapeHtml(L.title || "")}${L.timestamp ? ` · ${L.timestamp}` : ""}
      ${L.timestamp && d.video_url ? ` · <a href="${d.video_url}#t=${(L.start_ms / 1000).toFixed(1)}" target="_blank">Open video at ${L.timestamp}</a>` : ""}
      ${main ? ` · <a href="${imgUrl(main.filename)}" target="_blank">Open image</a> · <a href="${imgUrl(main.filename)}" download="${main.filename.split("/").pop()}">Download</a>` : ""}</p>
    ${main ? `<div class="actions"><button type="button" class="d-share">Share link</button><button type="button" class="d-meme">Make meme</button></div>` : ""}
    <ol class="ctx-lines">${d.context.map((c) => `
      <li class="${c.selected ? "sel" : ""}"><span class="t">${c.timestamp || ""}</span><span class="s">${escapeHtml(c.speaker || "")}</span><span>${escapeHtml(c.text)}</span></li>`).join("")}
    </ol>`;
  body.querySelectorAll(".alts img").forEach((im) => im.addEventListener("click", () => {
    $(".detail-img", body).src = imgUrl(im.dataset.f);
    body.querySelectorAll(".alts img").forEach((x) => x.classList.toggle("on", x === im));
  }));
  const current = () => $(".detail-img", body)?.getAttribute("src");
  $(".d-share", body)?.addEventListener("click", () => share(shareUrl(main.filename), L));
  $(".d-meme", body)?.addEventListener("click", () => makeMeme(current(), L));
  dlg.showModal();
  $(".sel", body)?.scrollIntoView({ block: "center" });
}

// Cross-origin images (e.g. a separate image bucket) ignore <a download>; fetch as a blob instead.
// Needs CORS on the image host; if that fails, just open the image.
document.addEventListener("click", async (e) => {
  const a = e.target.closest("a[download]");
  if (!a || new URL(a.href, location.href).origin === location.origin) return;
  e.preventDefault();
  try {
    const blob = await (await fetch(a.href, { mode: "cors" })).blob();
    const u = URL.createObjectURL(blob), tmp = Object.assign(document.createElement("a"), { href: u, download: a.download || a.href.split("/").pop() });
    tmp.click(); setTimeout(() => URL.revokeObjectURL(u), 1000);
  } catch { window.open(a.href, "_blank", "noopener"); }
});

function toast(msg) {
  const t = Object.assign(document.createElement("div"), { className: "toast", textContent: msg });
  ($("dialog[open]") || document.body).appendChild(t);  // an open modal sits above everything else
  setTimeout(() => t.remove(), 2200);
}

async function share(url, line) {
  const text = `\u201c${line.text}\u201d \u2014 ${line.episode_code}`;
  if (navigator.share && matchMedia("(pointer: coarse)").matches) {
    try { await navigator.share({ url, text }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  try { await navigator.clipboard.writeText(url); toast("Link copied"); }
  catch { prompt("Copy this link:", url); }
}

/* Draw the quote onto the frame (subtitle style) and save/share it. Nothing is uploaded or stored.
 * Cross-origin frames need CORS on the image host (see docs/PUBLISHING.md). */
async function makeMeme(src, line) {
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = src;
  try { await img.decode(); } catch { toast("Couldn't load the image"); return; }
  const c = document.createElement("canvas");
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  const g = c.getContext("2d");
  g.drawImage(img, 0, 0);
  const size = Math.round(c.width / 18), maxW = c.width * 0.9;
  g.font = `bold ${size}px Impact, "Arial Black", "Helvetica Neue", sans-serif`;
  g.textAlign = "center"; g.textBaseline = "bottom"; g.lineJoin = "round";
  const words = line.text.split(/\s+/), rows = [];
  let cur = "";
  for (const w of words) {
    const test = cur ? `${cur} ${w}` : w;
    if (g.measureText(test).width > maxW && cur) { rows.push(cur); cur = w; } else cur = test;
  }
  if (cur) rows.push(cur);
  let y = c.height - size * 0.5;
  for (const row of rows.reverse()) {
    g.lineWidth = size / 6; g.strokeStyle = "#000"; g.strokeText(row, c.width / 2, y);
    g.fillStyle = "#fff"; g.fillText(row, c.width / 2, y);
    y -= size * 1.15;
  }
  let blob;
  try { blob = await new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej()), "image/jpeg", 0.9)); }
  catch { toast("Image host doesn't allow this (CORS)"); return; }
  const name = `${line.episode_code}-${(line.text.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40)).replace(/-+$/, "")}.jpg`;
  const file = new File([blob], name, { type: "image/jpeg" });
  if (navigator.canShare?.({ files: [file] }) && matchMedia("(pointer: coarse)").matches) {
    try { await navigator.share({ files: [file] }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  const u = URL.createObjectURL(blob);
  Object.assign(document.createElement("a"), { href: u, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(u), 1000);
}

$("#detail .close").addEventListener("click", () => $("#detail").close());
$("#detail").addEventListener("click", (e) => { if (e.target.id === "detail") e.target.close(); });
let timer;
q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => run(), 250); });
form.addEventListener("submit", (e) => { e.preventDefault(); run(); });
sel.season.addEventListener("change", () => { fillEpisodes(); run(); });
sel.speaker.addEventListener("change", () => run());
sel.episode.addEventListener("change", () => run());
more.addEventListener("click", () => run(true));

(async () => {
  await initBackend();
  await loadFacets();
  const init = new URLSearchParams(location.search);
  q.value = init.get("q") || "";
  for (const [k, el] of Object.entries(sel)) if (init.get(k)) { el.value = init.get(k); if (k === "season") fillEpisodes(); }
  const key = init.get("id");
  if (key) {  // shared permalink: show that quote, with its episode's lines behind it
    const id = await backend.lineIdForKey(key);
    if (id != null) {
      const d = await backend.dialogue(id);
      if (!q.value && !sel.episode.value) { sel.season.value = String(d.line.season); fillEpisodes(); sel.episode.value = d.line.episode_code; }
      await run();
      showDetail(id);
      return;
    }
  }
  run();
})();
