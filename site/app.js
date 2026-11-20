/* EU Agri Tracker: static front end. Reads the JSON files in data/ produced by the daily run. */
"use strict";

const TYPE = {
  COD: "Legislativo (codecisión)", CNS: "Consulta", APP: "Aprobación", NLE: "No legislativo",
  DEA: "Acto delegado", RPS: "Reglamentación con control", INI: "Informe de propia iniciativa",
  INL: "Iniciativa legislativa", BUD: "Presupuesto", RSP: "Resolución",
};
const ROLE = { lead: "responsable", joint: "conjunta", associated: "asociada", opinion: "opinión", budget: "evaluación presupuestaria", other: "otra" };
const PERSON_ROLE = {
  rapporteur: "Ponente", shadow: "Ponente alternativo", opinion_rapporteur: "Ponente de opinión",
  opinion_shadow: "Ponente alternativo de opinión",
};
const KIND = {
  new: ["🆕", "Nuevo expediente"], stage: ["🔀", "Cambio de fase"], status: ["📌", "Cambio de estado"],
  event: ["🗓️", "Nuevo paso"], doc: ["📄", "Nuevo documento"], people: ["👤", "Ponentes"],
  scheduled: ["⏭️", "Previsto"], council_doc: ["🏛️", "Documento del Consejo"],
};
const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const WEEKDAYS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];

const store = { meta: null, index: null, calendar: null, changes: null, people: null, council: null, files: {} };
const app = document.getElementById("app");

/* ---------- helpers ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const todayStr = () => new Date().toISOString().slice(0, 10);
function fmtDate(d) {
  if (!d) return "";
  const [y, m, day] = d.split("-").map(Number);
  return `${day} ${MONTHS[m - 1]} ${y}`;
}
function fmtDay(d) {
  const dt = new Date(d + "T12:00:00");
  return `${WEEKDAYS[dt.getDay()]} ${fmtDate(d)}`;
}
function addDays(d, n) {
  const dt = new Date(d + "T12:00:00"); dt.setDate(dt.getDate() + n); return dt.toISOString().slice(0, 10);
}
async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}
async function need(...names) {
  await Promise.all(names.map(async (n) => {
    if (store[n] === null) store[n] = await getJSON(`data/${n}.json`);
  }));
}
async function fileRecord(id) {
  if (!store.files[id]) store.files[id] = await getJSON(`data/files/${id}.json`);
  return store.files[id];
}
function parseHash() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  const params = new URLSearchParams(query || "");
  return { parts: path.split("/").filter(Boolean).map(decodeURIComponent), params };
}
function setQuery(params) {
  const { parts } = parseHash();
  const q = params.toString();
  history.replaceState(null, "", `#/${parts.map(encodeURIComponent).join("/")}${q ? "?" + q : ""}`);
}
function fileLink(f) {
  return `#/expediente/${encodeURIComponent(f.id)}`;
}
function byRef(ref) {
  return (store.index || []).find((f) => f.ref === ref);
}
function followUrl(f) {
  const repo = store.meta && store.meta.repo;
  if (!repo) return "";
  const title = `Seguir: ${f.ref} — ${(f.title || "").slice(0, 90)}`;
  const body = `Quiero recibir avisos de los cambios en ${f.ref}.\n\n` +
    `(Los avisos llegarán como comentarios en este issue y GitHub te los enviará por correo. ` +
    `Ciérralo para dejar de seguir el expediente.)`;
  return `https://github.com/${repo}/issues/new?labels=follow&title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
}
function committeesText(f) {
  return (f.committees || []).map((c) =>
    `<span class="pill ${c.role === "opinion" ? "lilac" : ""}" title="${esc(ROLE[c.role] || c.role)}">${esc(c.code)}${c.role !== "lead" ? " · " + esc(ROLE[c.role] || c.role) : ""}</span>`).join(" ");
}
function statusPill(f) {
  return f.ongoing === false ? `<span class="pill done">concluido</span>` : `<span class="pill">en curso</span>`;
}
function refRefs(refs) {
  return (refs || []).map((r) => {
    const f = byRef(r);
    return f ? `<a class="ref" href="${fileLink(f)}" title="${esc(f.title)}">${esc(r)}</a>` : `<span class="ref">${esc(r)}</span>`;
  }).join(" ");
}

/* ---------- views ---------- */
async function viewHome() {
  await need("meta", "index", "calendar", "changes", "council");
  const t = todayStr();
  const followed = store.index.filter((f) => f.followed);
  const recent = store.changes.filter((c) => c.date >= addDays(t, -7));
  const upcoming = store.calendar.filter((e) => e.date >= t && e.date <= addDays(t, 21));
  const ongoing = store.index.filter((f) => f.ongoing !== false).length;

  const groups = {};
  recent.forEach((c) => { (groups[c.ref] = groups[c.ref] || []).push(c); });
  const recentHtml = Object.keys(groups).length ? `<ul class="list">${Object.entries(groups)
    .sort((a, b) => (b[1][0].date || "").localeCompare(a[1][0].date || "")).slice(0, 25).map(([ref, cs]) => {
      const f = byRef(ref);
      return `<li><div class="item-head">${f ? `<a class="item-title" href="${fileLink(f)}">${esc(f.title || ref)}</a>` : esc(ref)}
        <span class="ref">${esc(ref)}</span>${f && f.followed ? ` <span class="followed">★ siguiendo</span>` : ""}</div>
        <ul class="list" style="margin-top:4px">${cs.slice(0, 6).map((c) => `<li style="padding:3px 0;border:0">
          <span class="kind">${(KIND[c.kind] || ["•"])[0]}</span> ${esc(c.text)}
          ${c.url ? ` <a href="${esc(c.url)}" target="_blank" rel="noopener">enlace</a>` : ""}</li>`).join("")}
          ${cs.length > 6 ? `<li class="item-sub" style="border:0">y ${cs.length - 6} más…</li>` : ""}</ul></li>`;
    }).join("")}</ul>` : `<p class="empty">Sin cambios en los últimos 7 días.</p>`;

  const upcomingHtml = upcoming.length ? `<ul class="list">${upcoming.slice(0, 30).map((e) => `<li>
      <div class="item-head"><span class="date">${esc(fmtDate(e.date))}</span>
      <span class="${e.source === "council" ? "src-council" : "src-ep"}"><b>${esc(e.body)}</b></span></div>
      <div class="item-sub">${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div>
      ${e.refs && e.refs.length ? `<div class="item-sub">Expedientes: ${refRefs(e.refs)}</div>` : ""}</li>`).join("")}</ul>`
    : `<p class="empty">No hay reuniones registradas en las próximas tres semanas.</p>`;

  const followedHtml = followed.length ? `<ul class="list">${followed.map((f) => `<li>
      <div class="item-head"><a class="item-title" href="${fileLink(f)}">${esc(f.title || f.ref)}</a></div>
      <div class="item-sub"><span class="ref">${esc(f.ref)}</span> · ${esc(f.stage || "")}
      ${f.next ? ` · próximo: ${esc(f.next.body)}, ${esc(fmtDate(f.next.date))}` : ""}</div></li>`).join("")}</ul>`
    : `<p class="empty">Aún no sigues ningún expediente. Abre uno y pulsa «Seguir» para recibir avisos por correo.</p>`;

  const press = (store.council.press || []).slice(0, 8);
  const pressHtml = press.length ? `<ul class="list">${press.map((p) => `<li><div class="item-head">
      <span class="date">${esc(fmtDate(p.date))}</span>${p.step ? `<span class="pill lilac">${esc(p.step)}</span>` : ""}</div>
      <a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></li>`).join("")}</ul>`
    : `<p class="empty">Sin notas de prensa recientes sobre estos temas.</p>`;

  app.innerHTML = `
    <h1>Legislación de la UE en agricultura, pesca y desarrollo regional</h1>
    <p class="lede">Expedientes en los que intervienen las comisiones ${esc((store.meta.committees || []).join(", "))} del Parlamento Europeo, con sus pasos en el Consejo. Se actualiza solo cada día.</p>
    <div class="stats">
      <div class="stat"><b>${store.index.length}</b><span>expedientes</span></div>
      <div class="stat"><b>${ongoing}</b><span>en curso</span></div>
      <div class="stat"><b>${followed.length}</b><span>que sigues</span></div>
      <div class="stat"><b>${recent.length}</b><span>cambios en 7 días</span></div>
    </div>
    <div class="grid grid-3">
      <section class="card"><h2>Novedades de la semana <a href="#/cambios">todas</a></h2>${recentHtml}</section>
      <div class="grid">
        <section class="card"><h2>Expedientes que sigues</h2>${followedHtml}</section>
        <section class="card"><h2>Próximas tres semanas <a href="#/calendario">calendario</a></h2>${upcomingHtml}</section>
        <section class="card"><h2>Consejo: notas de prensa</h2>${pressHtml}</section>
      </div>
    </div>`;
}

async function viewFiles() {
  await need("meta", "index");
  const { params } = parseHash();
  const committees = store.meta.committees || ["AGRI", "PECH", "REGI"];
  const state = {
    q: params.get("q") || "",
    c: (params.get("c") || "").split(",").filter(Boolean),
    role: params.get("role") || "",
    type: params.get("type") || "",
    st: params.get("st") || "ongoing",
    sort: params.get("sort") || "last",
  };
  const types = [...new Set(store.index.map((f) => f.type))].filter(Boolean).sort();

  app.innerHTML = `
    <h1>Expedientes</h1>
    <div class="filters" role="search">
      <input type="search" id="f-q" placeholder="Buscar por título, número o ponente…" value="${esc(state.q)}" aria-label="Buscar">
      <div class="chips" aria-label="Comisión">${committees.map((c) =>
        `<button class="chip" data-c="${esc(c)}" aria-pressed="${state.c.includes(c)}">${esc(c)}</button>`).join("")}</div>
      <label>Papel <select id="f-role"><option value="">todos</option>
        ${Object.entries(ROLE).map(([k, v]) => `<option value="${k}" ${state.role === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label>Tipo <select id="f-type"><option value="">todos</option>
        ${types.map((t) => `<option value="${t}" ${state.type === t ? "selected" : ""}>${esc(TYPE[t] || t)}</option>`).join("")}</select></label>
      <label>Estado <select id="f-st">
        <option value="ongoing" ${state.st === "ongoing" ? "selected" : ""}>en curso</option>
        <option value="done" ${state.st === "done" ? "selected" : ""}>concluidos</option>
        <option value="followed" ${state.st === "followed" ? "selected" : ""}>que sigo</option>
        <option value="" ${state.st === "" ? "selected" : ""}>todos</option></select></label>
    </div>
    <p class="count" id="f-count"></p>
    <div class="table-wrap"><table>
      <thead><tr>
        <th data-sort="ref">Número</th><th data-sort="title">Expediente</th><th>Comisiones</th>
        <th data-sort="stage">Fase</th><th data-sort="last">Última actividad</th></tr></thead>
      <tbody id="f-body"></tbody></table></div>`;

  function apply() {
    const q = state.q.trim().toLowerCase();
    let rows = store.index.filter((f) => {
      if (state.st === "ongoing" && f.ongoing === false) return false;
      if (state.st === "done" && f.ongoing !== false) return false;
      if (state.st === "followed" && !f.followed) return false;
      if (state.type && f.type !== state.type) return false;
      if (state.c.length || state.role) {
        const ok = (f.committees || []).some((c) =>
          (!state.c.length || state.c.includes(c.code)) && (!state.role || c.role === state.role));
        if (!ok) return false;
      }
      if (q) {
        const hay = [f.ref, f.title, f.title_en, f.stage, ...(f.rapporteurs || [])].join(" ").toLowerCase();
        if (!q.split(/\s+/).every((w) => hay.includes(w))) return false;
      }
      return true;
    });
    const key = state.sort;
    rows.sort((a, b) => key === "last" ? (b.last || "").localeCompare(a.last || "")
      : String(a[key] || "").localeCompare(String(b[key] || ""), "es"));
    document.getElementById("f-count").textContent = `${rows.length} expedientes`;
    document.getElementById("f-body").innerHTML = rows.slice(0, 600).map((f) => `<tr>
      <td><span class="ref">${esc(f.ref)}</span><div class="item-sub">${esc(TYPE[f.type] || f.type)}</div></td>
      <td class="title"><a href="${fileLink(f)}">${esc(f.title || f.ref)}</a>
        ${f.followed ? ` <span class="followed">★</span>` : ""}
        ${(f.rapporteurs || []).length ? `<div class="item-sub">Ponente: ${esc(f.rapporteurs.join(", "))}</div>` : ""}</td>
      <td>${committeesText(f)}</td>
      <td>${statusPill(f)}<div class="item-sub">${esc(f.stage || "")}</div>
        ${f.next ? `<div class="item-sub">⏭️ ${esc(f.next.body)}, ${esc(fmtDate(f.next.date))}</div>` : ""}</td>
      <td><span class="date">${esc(fmtDate(f.last))}</span></td></tr>`).join("") ||
      `<tr><td colspan="5" class="empty">Ningún expediente coincide con los filtros.</td></tr>`;
    const p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.c.length) p.set("c", state.c.join(","));
    if (state.role) p.set("role", state.role);
    if (state.type) p.set("type", state.type);
    if (state.st !== "ongoing") p.set("st", state.st);
    if (state.sort !== "last") p.set("sort", state.sort);
    setQuery(p);
  }
  document.getElementById("f-q").addEventListener("input", (e) => { state.q = e.target.value; apply(); });
  app.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    const c = b.dataset.c;
    state.c = state.c.includes(c) ? state.c.filter((x) => x !== c) : [...state.c, c];
    b.setAttribute("aria-pressed", state.c.includes(c)); apply();
  }));
  document.getElementById("f-role").addEventListener("change", (e) => { state.role = e.target.value; apply(); });
  document.getElementById("f-type").addEventListener("change", (e) => { state.type = e.target.value; apply(); });
  document.getElementById("f-st").addEventListener("change", (e) => { state.st = e.target.value; apply(); });
  app.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
    state.sort = th.dataset.sort; apply();
  }));
  apply();
}

async function viewFile(id) {
  await need("meta", "index");
  let f;
  try { f = await fileRecord(id); } catch (e) {
    app.innerHTML = `<h1>Expediente no encontrado</h1><p class="muted">${esc(id)} no está en los datos del tracker.</p>`;
    return;
  }
  const t = todayStr();
  const summary = store.index.find((x) => x.id === id) || {};
  const people = f.people || [];
  const byCommittee = {};
  (f.committees || []).forEach((c) => { byCommittee[c.code] = { ...c, people: [] }; });
  const roleOrder = { rapporteur: 0, opinion_rapporteur: 1, shadow: 2, opinion_shadow: 3 };
  people.slice().sort((a, b) => (roleOrder[a.role] ?? 9) - (roleOrder[b.role] ?? 9)).forEach((p) => {
    const k = p.committee || "—";
    (byCommittee[k] = byCommittee[k] || { code: k, role: "", people: [] }).people.push(p);
  });
  const comHtml = Object.values(byCommittee).map((c) => `<li style="margin-bottom:10px">
      <b>${esc(c.code)}</b> ${c.role ? `<span class="pill ${c.role === "opinion" ? "lilac" : ""}">${esc(ROLE[c.role] || c.role)}</span>` : ""}
      ${c.people.length ? `<ul class="people">${c.people.map((p) => `<li>${esc(PERSON_ROLE[p.role] || p.role)}:
        <a href="#/persona/${encodeURIComponent(p.id || p.name)}">${esc(p.name)}</a>
        <span class="group">${esc([p.group, p.country].filter(Boolean).join(", "))}</span></li>`).join("")}</ul>`
        : `<div class="item-sub">Sin ponentes registrados</div>`}</li>`).join("");

  const past = (f.events || []).map((e) => ({ ...e, _future: false }));
  const agenda = (f.agenda || []).map((e) => ({ date: e.date, title: `${e.body}: ${e.title}`, url: e.url,
    _future: e.date >= t, _agenda: true }));
  const all = [...past, ...agenda.filter((a) => a._future || !past.some((p) => p.date === a.date))]
    .filter((e) => e.date).sort((a, b) => b.date.localeCompare(a.date));
  let todayPlaced = false;
  const tl = all.map((e) => {
    let mark = "";
    if (!todayPlaced && e.date < t) { todayPlaced = true; mark = `<li class="today-mark">Hoy, ${esc(fmtDate(t))}</li>`; }
    const docs = (e.docs || []).filter((d) => d.url);
    return `${mark}<li class="${e._future ? "future" : ""}"><span class="date">${esc(fmtDate(e.date))}</span>
      ${e._future ? ` <span class="pill lilac">previsto</span>` : ""}
      <div>${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.type)}</a>` : esc(e.title || e.type)}</div>
      ${docs.length ? `<div class="docs">${docs.map((d) => `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title || d.id || "documento")}</a>`).join(" · ")}</div>` : ""}</li>`;
  }).join("");

  const allDocs = (f.docs || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  const AMEND = { committee_amendments: "Enmiendas en comisión", plenary_amendments: "Enmiendas al Pleno" };
  const docs = allDocs.filter((d) => !AMEND[d.kind]);
  const amendRows = Object.entries(AMEND).map(([kind, label]) => {
    const list = allDocs.filter((d) => d.kind === kind);
    if (!list.length) return "";
    const n = list.reduce((s, d) => s + (d.amendments || 0), 0);
    return `<li><div class="item-head"><b>${label}</b><span class="item-sub">${list.length} documento(s)${n ? ` · ${n} enmiendas` : ""}</span></div>
      <div class="docs item-sub">${list.map((d) => `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.id)}</a>`).join(" · ")}</div></li>`;
  }).join("");
  const council = (f.council_docs || []).slice().sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  const links = f.links || {};
  const linkItems = [
    ["oeil", "Observatorio Legislativo (OEIL)"], ["law_tracker", "EU Law Tracker"], ["eurlex", "EUR-Lex"],
    ["train", "Legislative Train"], ["ep_data", "Datos abiertos del PE"],
  ].filter(([k]) => links[k]).map(([k, label]) => `<a href="${esc(links[k])}" target="_blank" rel="noopener">${label} ↗</a>`).join("");
  const follow = summary.follow_url || f.follow_url;
  const followBtn = summary.followed && follow
    ? `<a class="btn ghost" href="${esc(follow)}" target="_blank" rel="noopener">★ Siguiendo (ver avisos)</a>`
    : `<a class="btn" href="${esc(followUrl(f))}" target="_blank" rel="noopener">☆ Seguir este expediente</a>`;

  app.innerHTML = `
    <p class="item-sub"><a href="#/expedientes">← Expedientes</a></p>
    <div class="file-head">
      <div>
        <div class="meta"><span class="ref">${esc(f.ref)}</span><span class="pill grey">${esc(TYPE[f.type] || f.type)}</span>
          ${statusPill(summary.ongoing === undefined ? f : summary)}</div>
        <h1>${esc(f.title || f.ref)}</h1>
        ${f.title_en && f.title_en !== f.title ? `<p class="item-sub">${esc(f.title_en)}</p>` : ""}
      </div>
      <div>${followBtn}</div>
    </div>
    <div class="grid grid-3">
      <div class="grid">
        <section class="card"><h2>Calendario del expediente</h2>
          ${tl ? `<ul class="timeline">${tl}</ul>` : `<p class="empty">Sin pasos registrados todavía.</p>`}</section>
        <section class="card"><h2>Documentos</h2>
          ${docs.length || amendRows ? `<ul class="list">${docs.map((d) => `<li><div class="item-head"><span class="date">${esc(fmtDate(d.date))}</span>
            ${d.url ? `<a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.title || d.id)}</a>` : esc(d.title || d.id)}</div></li>`).join("")}${amendRows}</ul>`
            : `<p class="empty">Sin documentos registrados.</p>`}
        </section>
      </div>
      <div class="grid">
        <section class="card"><h2>Situación</h2>
          <dl class="kv">
            <dt>Fase</dt><dd>${esc(f.stage || "—")}</dd>
            <dt>Inicio</dt><dd>${esc(fmtDate(f.started) || "—")}</dd>
            <dt>Última actividad</dt><dd>${esc(fmtDate(f.last_activity) || "—")}</dd>
            <dt>Enmiendas</dt><dd>${f.amendments && (f.amendments.plenary || f.amendments.committee_documents)
              ? esc([f.amendments.plenary ? `${f.amendments.plenary} en el Pleno` : "", f.amendments.committee_documents ? `${f.amendments.committee_documents} documento(s) de enmiendas en comisión` : ""].filter(Boolean).join(" · "))
              : "—"}</dd>
            <dt>En el tracker por</dt><dd>${esc((f.scope || []).join(", ") || "—")}</dd>
          </dl></section>
        <section class="card"><h2>Comisiones y ponentes</h2>
          ${comHtml ? `<ul class="list">${comHtml}</ul>` : `<p class="empty">Sin datos de comisiones.</p>`}</section>
        <section class="card"><h2>Consejo</h2>
          ${council.length ? `<ul class="list">${council.map((d) => `<li><div class="item-head"><span class="date">${esc(fmtDate(d.date))}</span>
            ${d.step ? `<span class="pill lilac">${esc(d.step)}</span>` : ""}
            ${d.kind === "agenda" ? `<span class="pill amber">orden del día${d.meeting_date ? " · " + esc(fmtDate(d.meeting_date)) : ""}</span>` : ""}</div>
            <div><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.kind === "agenda" ? (d.body || "") : (d.title || d.id))}</a></div>
            <div class="item-sub">${esc(d.code || d.id)}${d.kind === "agenda" && d.title ? " · " + esc(d.title) : ""}</div></li>`).join("")}</ul>`
            : `<p class="empty">Ningún documento del Consejo cita este expediente en las últimas semanas.</p>`}</section>
        <section class="card"><h2>Fuentes oficiales</h2><div class="links">${linkItems}</div>
          ${!links.train ? `<p class="item-sub" style="margin-top:8px">Legislative Train: añade el enlace a mano en config.yml (su web no permite lectura automática).</p>` : ""}</section>
      </div>
    </div>`;
}

async function viewCalendar() {
  await need("meta", "index", "calendar");
  const { params } = parseHash();
  const t = todayStr();
  const state = { src: params.get("src") || "", when: params.get("when") || "next", mine: params.get("mine") === "1" };
  app.innerHTML = `
    <h1>Calendario</h1>
    <div class="filters">
      <label>Institución <select id="c-src"><option value="">todas</option>
        <option value="ep" ${state.src === "ep" ? "selected" : ""}>Parlamento Europeo</option>
        <option value="council" ${state.src === "council" ? "selected" : ""}>Consejo</option></select></label>
      <label>Periodo <select id="c-when">
        <option value="next" ${state.when === "next" ? "selected" : ""}>próximas 8 semanas</option>
        <option value="all" ${state.when === "all" ? "selected" : ""}>todo lo previsto</option>
        <option value="past" ${state.when === "past" ? "selected" : ""}>últimas 8 semanas</option></select></label>
      <label><input type="checkbox" id="c-mine" ${state.mine ? "checked" : ""}> solo con expedientes que sigo</label>
    </div>
    <div id="c-body"></div>`;
  function apply() {
    const followedRefs = new Set(store.index.filter((f) => f.followed).map((f) => f.ref));
    let rows = store.calendar.filter((e) => {
      if (state.src && e.source !== state.src) return false;
      if (state.when === "next" && (e.date < t || e.date > addDays(t, 56))) return false;
      if (state.when === "all" && e.date < t) return false;
      if (state.when === "past" && (e.date >= t || e.date < addDays(t, -56))) return false;
      if (state.mine && !(e.refs || []).some((r) => followedRefs.has(r))) return false;
      return true;
    });
    rows.sort((a, b) => state.when === "past" ? b.date.localeCompare(a.date) : a.date.localeCompare(b.date));
    const days = {};
    rows.forEach((e) => { (days[e.date] = days[e.date] || []).push(e); });
    document.getElementById("c-body").innerHTML = Object.keys(days).length ? Object.entries(days).map(([d, es]) =>
      `<section class="day ${d === t ? "is-today" : ""}"><h3>${esc(fmtDay(d))}${d === t ? " · hoy" : ""}</h3>
      <ul class="list">${es.map((e) => `<li><div class="item-head">
        <b class="${e.source === "council" ? "src-council" : "src-ep"}">${esc(e.body)}</b>
        ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title)}</a>` : esc(e.title)}</div>
        ${e.description ? `<div class="item-sub">${esc(e.description)}</div>` : ""}
        ${e.refs && e.refs.length ? `<div class="item-sub">Expedientes: ${refRefs(e.refs)}</div>` : ""}</li>`).join("")}</ul></section>`).join("")
      : `<p class="empty">No hay reuniones con estos filtros.</p>`;
    const p = new URLSearchParams();
    if (state.src) p.set("src", state.src);
    if (state.when !== "next") p.set("when", state.when);
    if (state.mine) p.set("mine", "1");
    setQuery(p);
  }
  document.getElementById("c-src").addEventListener("change", (e) => { state.src = e.target.value; apply(); });
  document.getElementById("c-when").addEventListener("change", (e) => { state.when = e.target.value; apply(); });
  document.getElementById("c-mine").addEventListener("change", (e) => { state.mine = e.target.checked; apply(); });
  apply();
}

async function viewChanges() {
  await need("meta", "index", "changes");
  const { params } = parseHash();
  const state = { kind: params.get("kind") || "", mine: params.get("mine") === "1" };
  app.innerHTML = `
    <h1>Cambios detectados</h1>
    <p class="lede">Lo que ha cambiado en cada actualización diaria (últimos 90 días).</p>
    <div class="filters">
      <label>Tipo <select id="x-kind"><option value="">todos</option>
        ${Object.entries(KIND).map(([k, [i, l]]) => `<option value="${k}" ${state.kind === k ? "selected" : ""}>${i} ${l}</option>`).join("")}</select></label>
      <label><input type="checkbox" id="x-mine" ${state.mine ? "checked" : ""}> solo expedientes que sigo</label>
    </div>
    <div id="x-body"></div>`;
  function apply() {
    const followedRefs = new Set(store.index.filter((f) => f.followed).map((f) => f.ref));
    const rows = store.changes.filter((c) => (!state.kind || c.kind === state.kind) &&
      (!state.mine || followedRefs.has(c.ref)));
    const byDay = {};
    rows.forEach((c) => { (byDay[c.seen || c.date] = byDay[c.seen || c.date] || []).push(c); });
    document.getElementById("x-body").innerHTML = Object.keys(byDay).length ? Object.entries(byDay)
      .sort((a, b) => b[0].localeCompare(a[0])).map(([d, cs]) => `<section class="day"><h3>Detectado el ${esc(fmtDay(d))}</h3>
        <ul class="list">${cs.map((c) => {
          const f = byRef(c.ref);
          return `<li><div class="item-head"><span class="kind">${(KIND[c.kind] || ["•"])[0]}</span>
            ${f ? `<a class="item-title" href="${fileLink(f)}">${esc(f.title || c.ref)}</a>` : esc(c.ref)}
            <span class="ref">${esc(c.ref)}</span></div>
            <div class="item-sub">${esc(c.text)} ${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">enlace</a>` : ""}</div></li>`;
        }).join("")}</ul></section>`).join("") : `<p class="empty">Sin cambios con estos filtros.</p>`;
    const p = new URLSearchParams();
    if (state.kind) p.set("kind", state.kind);
    if (state.mine) p.set("mine", "1");
    setQuery(p);
  }
  document.getElementById("x-kind").addEventListener("change", (e) => { state.kind = e.target.value; apply(); });
  document.getElementById("x-mine").addEventListener("change", (e) => { state.mine = e.target.checked; apply(); });
  apply();
}

async function viewPerson(pid) {
  await need("meta", "index", "people");
  const p = store.people[pid] || Object.values(store.people).find((x) => x.name === pid);
  if (!p) { app.innerHTML = `<h1>Persona no encontrada</h1>`; return; }
  const rows = (p.files || []).map((x) => ({ ...x, f: byRef(x.ref) })).filter((x) => x.f)
    .sort((a, b) => (b.f.last || "").localeCompare(a.f.last || ""));
  app.innerHTML = `
    <p class="item-sub"><a href="#/expedientes">← Expedientes</a></p>
    <h1>${esc(p.name)}</h1>
    <p class="lede">${esc([p.group, p.country].filter(Boolean).join(" · "))}
      ${p.id && /^\d+$/.test(p.id) ? ` · <a href="https://www.europarl.europa.eu/meps/es/${esc(p.id)}" target="_blank" rel="noopener">ficha en el PE ↗</a>` : ""}</p>
    <section class="card"><h2>Expedientes en el tracker</h2>
      <ul class="list">${rows.map((x) => `<li><div class="item-head"><a class="item-title" href="${fileLink(x.f)}">${esc(x.f.title || x.ref)}</a>
        <span class="ref">${esc(x.ref)}</span></div>
        <div class="item-sub">${esc(PERSON_ROLE[x.role] || x.role)}${x.committee ? " · " + esc(x.committee) : ""} · ${esc(x.f.stage || "")}</div></li>`).join("")}</ul>
    </section>`;
}

/* ---------- router ---------- */
async function route() {
  const { parts } = parseHash();
  const r = parts[0] || "";
  const navKey = { "": "home", expedientes: "files", expediente: "files", calendario: "calendar", cambios: "changes", persona: "files" }[r] || "home";
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === navKey));
  try {
    if (r === "expedientes") await viewFiles();
    else if (r === "expediente" && parts[1]) await viewFile(parts[1]);
    else if (r === "calendario") await viewCalendar();
    else if (r === "cambios") await viewChanges();
    else if (r === "persona" && parts[1]) await viewPerson(parts[1]);
    else await viewHome();
  } catch (e) {
    console.error(e);
    app.innerHTML = `<div class="notice">No se han podido cargar los datos (${esc(e.message)}). Si es la primera vez, espera a que termine la primera actualización.</div>`;
  }
  if (store.meta) {
    document.getElementById("foot-meta").textContent =
      `Última actualización: ${new Date(store.meta.updated).toLocaleString("es-ES")}.`;
  }
}
let lastRoute = "";
window.addEventListener("hashchange", () => {
  const base = location.hash.split("?")[0];
  if (base !== lastRoute) { lastRoute = base; route(); window.scrollTo(0, 0); }
});
lastRoute = location.hash.split("?")[0];
route();
