"""Shared helpers: polite HTTP, file storage, procedure references, dates."""

from __future__ import annotations

import datetime as dt
import email.utils
import io
import json
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
REPO = os.getenv("GITHUB_REPOSITORY", "local/eu-agri-tracker")
USER_AGENT = ("EU-agri-tracker/1.0 (personal, non-commercial monitoring of public EU "
              f"legislative data; https://github.com/{REPO})")

# --------------------------------------------------------------------------- logging

_log: list[str] = []


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    _log.append(line)
    print(line, flush=True)


def log_lines() -> list[str]:
    return list(_log)


# --------------------------------------------------------------------------- HTTP

class Http:
    """requests.Session with a minimum gap per host, retries and a request budget."""

    def __init__(self, min_gap: float = 0.7, budget: int = 6000) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self.min_gap = min_gap
        self.last: dict[str, float] = {}
        self.budget = budget
        self.count = 0
        self.errors = 0

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        gap = time.monotonic() - self.last.get(host, 0)
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self.last[host] = time.monotonic()

    def get(self, url: str, params: dict | None = None, accept: str | None = None,
            timeout: int = 60, tries: int = 3) -> requests.Response | None:
        if self.count >= self.budget:
            raise BudgetExceeded()
        headers = {"Accept": accept} if accept else {}
        for attempt in range(1, tries + 1):
            self._wait(url)
            self.count += 1
            try:
                r = self.s.get(url, params=params, headers=headers, timeout=timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                log(f"  network problem ({attempt}/{tries}) {url}: {type(exc).__name__}")
                time.sleep(3 * attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < tries:
                time.sleep(5 * attempt)
                continue
            if r.status_code >= 400 and r.status_code != 404:
                self.errors += 1
                log(f"  HTTP {r.status_code} {url}")
            return r
        self.errors += 1
        return None

    def json(self, url: str, params: dict | None = None, accept: str = "application/ld+json") -> Any:
        r = self.get(url, params=params, accept=accept)
        if r is None or r.status_code != 200 or not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return None


class BudgetExceeded(Exception):
    """Raised when a run has used its request budget; work resumes next run."""


# --------------------------------------------------------------------------- storage

def path(*parts: str) -> str:
    return os.path.join(DATA, *parts)


def load(name: str, default: Any) -> Any:
    p = path(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except ValueError:
            log(f"  could not read {name}; starting it again")
            return default


def save(name: str, value: Any) -> None:
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, p)


def load_config() -> dict:
    import yaml  # local import: only needed here
    with open(os.path.join(ROOT, "config.yml"), encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("language", "es")
    cfg.setdefault("committees", ["AGRI", "PECH", "REGI"])
    cfg.setdefault("roles", ["lead", "joint", "associated", "opinion"])
    cfg.setdefault("procedure_types", ["COD", "CNS", "APP", "NLE", "DEA", "RPS", "INI", "INL"])
    cfg.setdefault("since_year", 2019)
    cfg["also_track"] = [normalize_ref(r) for r in (cfg.get("also_track") or []) if normalize_ref(r)]
    cfg["follow"] = [normalize_ref(r) for r in (cfg.get("follow") or []) if normalize_ref(r)]
    cfg["legislative_train"] = {normalize_ref(k): v for k, v in (cfg.get("legislative_train") or {}).items()
                                if normalize_ref(k)}
    cfg.setdefault("council", {})
    cfg["council"].setdefault("first_run_days", 45)
    cfg["council"].setdefault("max_pdfs_per_run", 80)
    cfg.setdefault("notifications", {})
    cfg["notifications"].setdefault("daily_digest", False)
    return cfg


# --------------------------------------------------------------------------- references

REF_RE = re.compile(r"\b((?:19|20)\d{2})\s*/\s*(\d{4}[A-Z]?)\s*\(\s*([A-Z]{3})\s*\)")


def normalize_ref(text: str | None) -> str | None:
    """'2024/0319(COD)' in any spacing -> '2024/0319(COD)'."""
    if not text:
        return None
    m = REF_RE.search(str(text))
    return f"{m.group(1)}/{m.group(2)}({m.group(3)})" if m else None


def refs_in(text: str | None) -> list[str]:
    out = []
    for m in REF_RE.finditer(text or ""):
        ref = f"{m.group(1)}/{m.group(2)}({m.group(3)})"
        if ref not in out:
            out.append(ref)
    return out


def ep_id(ref: str) -> str:
    """'2024/0319(COD)' -> '2024-0319' (the Parliament API identifier)."""
    m = REF_RE.search(ref)
    return f"{m.group(1)}-{m.group(2)}" if m else ref


def ref_type(ref: str) -> str:
    m = REF_RE.search(ref)
    return m.group(3) if m else ""


def links_for(ref: str) -> dict[str, str]:
    m = REF_RE.search(ref)
    if not m:
        return {}
    year, num, typ = m.group(1), m.group(2), m.group(3)
    num_digits = re.sub(r"\D", "", num)
    return {
        "oeil": f"https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference={year}/{num}({typ})",
        "eurlex": f"https://eur-lex.europa.eu/procedure/EN/{year}_{int(num_digits)}",
        "law_tracker": f"https://law-tracker.europa.eu/procedure/{year}_{int(num_digits)}?lang=en",
        "ep_data": f"https://data.europarl.europa.eu/api/v2/procedures/{year}-{num}?format=application%2Fld%2Bjson",
    }


# --------------------------------------------------------------------------- dates

def today() -> str:
    return dt.date.today().isoformat()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def parse_date(value: Any) -> str | None:
    """Best effort: ISO, RFC 822, '15 December 2026', '15.12.2026' -> 'YYYY-MM-DD'."""
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("@value") or value.get("value") or next(iter(value.values()), None)
    if isinstance(value, list):
        value = value[0] if value else None
    text = str(value or "").strip()
    if not text:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return m.group(1)
    try:
        return email.utils.parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    m = re.search(r"\b(\d{1,2})\s+([A-Za-z]+)\s+((?:19|20)\d{2})\b", text)
    if m and m.group(2).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})[./](\d{1,2})[./]((?:19|20)\d{2})\b", text)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


def days_ago(n: int) -> str:
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


def days_ahead(n: int) -> str:
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------- PDFs

def pdf_text(content: bytes, pages: int) -> str:
    """Text of the first `pages` pages of a PDF ('' if it cannot be read)."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join((reader.pages[i].extract_text() or "") for i in range(min(pages, len(reader.pages))))
    except Exception as exc:  # noqa: BLE001 - a bad PDF must not stop the run
        log(f"  PDF not readable: {type(exc).__name__}")
        return ""
