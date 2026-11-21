"""Notifications through GitHub issues.

Following a file = an open issue with the label "follow" whose title contains the file
number, e.g. "Seguir: 2024/0319(COD)". Each run posts the changes as a comment on that
issue, and GitHub e-mails the comment. Closing the issue stops the notifications.
"""

from __future__ import annotations

import os

import requests

from .common import days_ago, log, normalize_ref, refs_in, today

API = "https://api.github.com"
LABEL = "follow"
DIGEST_LABEL = "digest"

KIND_ICON = {"new": "🆕", "stage": "🔀", "status": "📌", "event": "🗓️", "doc": "📄", "people": "👤",
             "scheduled": "⏭️", "council_doc": "🏛️"}


class GitHub:
    def __init__(self) -> None:
        self.token = os.getenv("GITHUB_TOKEN", "")
        self.repo = os.getenv("GITHUB_REPOSITORY", "")
        self.owner = os.getenv("GITHUB_REPOSITORY_OWNER", self.repo.split("/")[0] if self.repo else "")
        self.ok = bool(self.token and self.repo)
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}",
                               "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def call(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{API}/repos/{self.repo}{path}", timeout=30, **kw)
        if r.status_code >= 400:
            log(f"  GitHub {method} {path}: HTTP {r.status_code} {r.text[:200]}")
            return None
        return r.json() if r.content else {}

    def ensure_label(self, name: str, color: str, description: str) -> None:
        if self.call("GET", f"/labels/{name}") is None:
            self.call("POST", "/labels", json={"name": name, "color": color, "description": description})

    def open_issues(self, label: str) -> list[dict]:
        out, page = [], 1
        while True:
            batch = self.call("GET", "/issues", params={"labels": label, "state": "open", "per_page": 100,
                                                         "page": page})
            if not batch:
                break
            out.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
            page += 1
        return out


def _issue_title(ref: str, title: str) -> str:
    short = (title or "").strip()
    if len(short) > 90:
        short = short[:87] + "…"
    return f"Seguir: {ref} — {short}" if short else f"Seguir: {ref}"


def _status_block(rec: dict, site_url: str) -> str:
    lines = [f"**{rec.get('title') or rec['ref']}**", ""]
    pr = rec.get("progress") or {}
    if pr.get("stages"):
        marks = {"done": "✅", "implied": "☑️", "partial": "◐", "current": "📍", "pending": "○"}
        lines.append("- Procedimiento: " + " → ".join(
            f"{marks.get(st['state'], '○')} {st['label']}" for st in pr["stages"]))
        if pr.get("current_label"):
            lines.append(f"- Ahora: **{pr['current_label']}**" +
                         (f" · después: {pr['next_label']}" if pr.get("next_label") else ""))
    if rec.get("stage"):
        lines.append(f"- Último paso en el PE: {rec['stage']}")
    lead = [c["code"] for c in rec.get("committees", []) if c.get("role") in ("lead", "joint")]
    if lead:
        lines.append(f"- Comisión responsable: {', '.join(lead)}")
    rapp = [p["name"] for p in rec.get("people", []) if p.get("role") == "rapporteur"]
    if rapp:
        lines.append(f"- Ponente: {', '.join(rapp)}")
    nxt = sorted((s for s in rec.get("scheduled", []) if s.get("date")), key=lambda s: s["date"])
    if nxt:
        lines.append(f"- Próximo: {nxt[0]['body']}, {nxt[0]['date']}")
    links = rec.get("links", {})
    link_parts = []
    if site_url:
        link_parts.append(f"[Ficha en el tracker]({site_url}#/expediente/{rec.get('id')})")
    if links.get("oeil"):
        link_parts.append(f"[OEIL]({links['oeil']})")
    if links.get("law_tracker"):
        link_parts.append(f"[EU Law Tracker]({links['law_tracker']})")
    if links.get("eurlex"):
        link_parts.append(f"[EUR-Lex]({links['eurlex']})")
    if links.get("train"):
        link_parts.append(f"[Legislative Train]({links['train']})")
    if link_parts:
        lines.append("- " + " · ".join(link_parts))
    return "\n".join(lines)


def _line(c: dict) -> str:
    icon = KIND_ICON.get(c["kind"], "•")
    return f"- {icon} {c['text']}" + (f" ([enlace]({c['url']}))" if c.get("url") else "")


def _changes_block(changes: list[dict]) -> str:
    """Important items first, then normal ones; minor items folded into one line."""
    high = [c for c in changes if c.get("importance") == "high"]
    normal = [c for c in changes if c.get("importance", "normal") == "normal"]
    low = [c for c in changes if c.get("importance") == "low"]
    parts = []
    if high:
        parts.append("**Lo importante**\n" + "\n".join(_line(c) for c in high))
    if normal:
        parts.append(("**Otros movimientos**\n" if high else "") + "\n".join(_line(c) for c in normal))
    if low:
        parts.append(f"<details><summary>{len(low)} movimiento(s) menor(es): documentos y trámites</summary>\n\n" +
                     "\n".join(_line(c) for c in low) + "\n</details>")
    return "\n\n".join(parts)


def _pending(changes_log: list[dict], ref: str, sent: set[str]) -> list[dict]:
    return [c for c in changes_log if c.get("ref") == ref and c["id"] not in sent
            and (c.get("seen") or "") >= days_ago(30)]


def _due(pending: list[dict]) -> bool:
    """Post now if something matters; minor-only items wait for a weekly round-up."""
    if any(c.get("importance", "normal") in ("high", "normal") for c in pending):
        return True
    return any((c.get("seen") or today()) <= days_ago(7) for c in pending)


def run(records: dict, changes_log: list[dict], cfg: dict, state: dict, site_url: str) -> tuple[dict, dict]:
    """Post notifications. Returns (new notify state, {ref: issue url} for followed files)."""
    gh = GitHub()
    followed_urls: dict[str, str] = {}
    sent: dict[str, list[str]] = {k: list(v) for k, v in state.get("sent", {}).items()}
    welcomed: list[int] = list(state.get("welcomed", []))
    if not gh.ok:
        log("  notifications skipped (not running on GitHub)")
        return state, followed_urls

    gh.ensure_label(LABEL, "2e7d32", "Seguir este expediente: los cambios se publican aquí")

    issues = gh.open_issues(LABEL)
    by_ref: dict[str, dict] = {}
    for issue in issues:
        refs = refs_in(f"{issue.get('title', '')}\n{issue.get('body') or ''}")
        for ref in refs[:1]:
            by_ref.setdefault(ref, issue)

    # Files listed under "follow" in config.yml get an issue automatically
    for ref in cfg.get("follow", []):
        ref = normalize_ref(ref)
        if ref and ref not in by_ref:
            rec = records.get(ref, {"ref": ref, "title": ""})
            body = (f"@{gh.owner} este expediente está en la lista `follow` de config.yml.\n\n"
                    "Los cambios se publicarán aquí como comentarios. Cierra el issue para dejar de seguirlo.")
            issue = gh.call("POST", "/issues", json={"title": _issue_title(ref, rec.get("title", "")),
                                                     "body": body, "labels": [LABEL]})
            if issue:
                by_ref[ref] = issue
                log(f"  created follow issue for {ref}")

    for ref, issue in by_ref.items():
        number = issue["number"]
        followed_urls[ref] = issue.get("html_url", "")
        rec = records.get(ref)
        already = set(sent.get(ref, []))
        pending = _pending(changes_log, ref, already)
        if number not in welcomed:
            if rec:
                body = ("👋 Siguiendo este expediente. Situación actual:\n\n" + _status_block(rec, site_url) +
                        "\n\nRecibirás un comentario aquí (y un correo de GitHub) cuando haya novedades. "
                        "Los pasos importantes se avisan enseguida; los trámites menores, en un resumen semanal.")
            else:
                body = (f"👋 Siguiendo {ref}. Todavía no está en los datos del tracker: si es un expediente del "
                        "Parlamento, aparecerá en la próxima actualización.")
            if gh.call("POST", f"/issues/{number}/comments", json={"body": body}) is not None:
                welcomed.append(number)
                sent.setdefault(ref, []).extend(c["id"] for c in pending)
            continue
        if not pending or not _due(pending):
            continue
        title = rec.get("title") if rec else ref
        where = ""
        if rec and rec.get("progress", {}).get("current_label"):
            pr = rec["progress"]
            where = f"\n\n📍 Ahora: **{pr['current_label']}**" + (f" · después: {pr['next_label']}" if pr.get("next_label") else "")
            if pr.get("next_date"):
                where += f" · próxima fecha: {pr['next_date']['body']}, {pr['next_date']['date']}"
        body = (f"**Novedades en {ref}** — {title}\n\n" + _changes_block(pending) + where +
                (f"\n\n[Ver la ficha]({site_url}#/expediente/{rec.get('id')})" if site_url and rec else ""))
        if gh.call("POST", f"/issues/{number}/comments", json={"body": body}) is not None:
            sent.setdefault(ref, []).extend(c["id"] for c in pending)
            log(f"  notified {len(pending)} change(s) on {ref} (issue #{number})")

    # Optional daily digest of all changes (important and normal ones; minor ones only counted)
    if cfg["notifications"].get("daily_digest"):
        fresh = [c for c in changes_log if c["id"] not in set(sent.get("_digest", []))
                 and (c.get("seen") or "") >= days_ago(3)]
        if fresh:
            gh.ensure_label(DIGEST_LABEL, "8e7cc3", "Resumen diario de cambios")
            digest = gh.open_issues(DIGEST_LABEL)
            if digest:
                number = digest[0]["number"]
            else:
                created = gh.call("POST", "/issues", json={
                    "title": "Cambios diarios", "labels": [DIGEST_LABEL],
                    "body": f"@{gh.owner} aquí se publica cada día el resumen de cambios en todos los expedientes."})
                number = created["number"] if created else None
            lines = []
            for ref in sorted({c["ref"] for c in fresh}):
                rec = records.get(ref, {})
                lines.append(f"**{ref}** — {rec.get('title', '')}")
                lines.append(_changes_block([c for c in fresh if c["ref"] == ref]))
                lines.append("")
            body = "\n".join(lines)
            if len(body) > 60000:
                body = body[:60000] + "\n\n… (lista recortada; el resto está en la web)"
            if number and gh.call("POST", f"/issues/{number}/comments", json={"body": body}) is not None:
                sent.setdefault("_digest", []).extend(c["id"] for c in fresh)

    # Keep the memory of sent changes small
    sent = {k: v[-800:] for k, v in sent.items()}
    return {"sent": sent, "welcomed": welcomed[-1000:]}, followed_urls
