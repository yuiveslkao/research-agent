"""arXiv lookup (search and id verification) with an on-disk cache and rate limiting.

Results are untrusted data: only short structured fields are returned, control characters are stripped and
lengths are capped. Used for (a) literature search and (b) verifying that a cited arXiv id exists.
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

API = "http://export.arxiv.org/api/query"
ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
SEARCH_TTL_S = 24 * 3600


class ArxivError(RuntimeError):
    pass


def _clean(s: str | None, n: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


class ArxivClient:
    def __init__(self, cache_dir: str | Path, fetch=None, min_interval: float = 3.0):
        self.cache = Path(cache_dir)
        self._fetch = fetch or self._http
        self.min_interval = min_interval
        self._last = 0.0

    @staticmethod
    def _http(url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "research-agent/0.1 (mailto-free; academic use)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def _path(self, url: str) -> Path:
        return self.cache / (hashlib.sha256(url.encode()).hexdigest() + ".xml")

    def _get(self, url: str, ttl_s: float | None) -> bytes:
        f = self._path(url)
        if f.exists() and (ttl_s is None or time.time() - f.stat().st_mtime < ttl_s):
            return f.read_bytes()
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            data = self._fetch(url)
        except Exception as e:  # noqa: BLE001 - network problems are reported, not raised raw
            raise ArxivError(f"cannot reach arXiv ({type(e).__name__}: {e})") from e
        finally:
            self._last = time.monotonic()
        self.cache.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        return data

    @staticmethod
    def _parse(data: bytes) -> list[dict]:
        data = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b" ", data)     # illegal XML control characters
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise ArxivError(f"unreadable arXiv response: {e}") from e
        out = []
        for e in root.findall("a:entry", NS):
            url = (e.findtext("a:id", "", NS) or "").strip()
            if "/api/errors" in url:
                continue
            aid = re.sub(r"^https?://arxiv\.org/abs/", "", url)
            authors = [_clean(a.findtext("a:name", "", NS), 60) for a in e.findall("a:author", NS)]
            cat = e.find("x:primary_category", NS)
            out.append({
                "id": _clean(aid, 40), "title": _clean(e.findtext("a:title", "", NS), 200),
                "authors": authors[:3] + (["et al."] if len(authors) > 3 else []),
                "year": (e.findtext("a:published", "", NS) or "")[:4],
                "category": cat.get("term") if cat is not None else None,
                "abstract": _clean(e.findtext("a:summary", "", NS), 350)})
        return out

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        q = _clean(query, 200)
        if not q:
            raise ArxivError("empty query")
        url = f"{API}?search_query={urllib.parse.quote_plus('all:' + q)}&start=0&max_results={max(1, min(max_results, 8))}"
        return self._parse(self._get(url, SEARCH_TTL_S))

    def get(self, arxiv_id: str) -> dict | None:
        if not ARXIV_ID.match(arxiv_id or ""):
            raise ArxivError(f"{arxiv_id!r} is not a valid arXiv id (e.g. 2305.05127 or math/0501001)")
        url = f"{API}?id_list={urllib.parse.quote(arxiv_id)}&max_results=1"
        found = self._parse(self._get(url, None))
        base = re.sub(r"v\d+$", "", arxiv_id)
        hit = next((r for r in found if re.sub(r"v\d+$", "", r["id"]) == base), None)
        if hit is None:
            self._path(url).unlink(missing_ok=True)      # do not cache "not found" forever
        return hit
