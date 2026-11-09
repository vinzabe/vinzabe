#!/usr/bin/env python3
"""Regenerate the custom profile README for github.com/vinzabe.

Writes the animated header, the stats strip and one light + one dark card per
project into assets/, then rewrites the blocks between the BEGIN/END markers in
README.md. Standard library only, so the workflow needs no dependencies.

    python3 scripts/generate.py                        # anonymous API, 60 req/h
    GITHUB_TOKEN=$(gh auth token) python3 scripts/generate.py

Projects come from the star list named in config.json when the token can read
it (a private list is only visible to its owner's token), otherwise from the
"contributions" array in config.json.
"""

from __future__ import annotations

import base64
import html
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
README_PATH = ROOT / "README.md"
ASSETS = ROOT / "assets"
CARDS_DIR = ASSETS / "cards"
SNAPSHOT_PATH = ROOT / "data" / "contributions.json"

CONFIG = json.loads(CONFIG_PATH.read_text())
LOGIN: str = CONFIG["user"]
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
CACHE = Path(os.environ["PROFILE_CACHE"]) if os.environ.get("PROFILE_CACHE") else None
API = "https://api.github.com"

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"

THEMES = {
    "dark": {
        "bg0": "#0d1117", "bg1": "#101a25", "border": "#2b3440", "rule": "#222c37",
        "text": "#e6edf3", "muted": "#9da7b3", "faint": "#6e7781",
        "accent": "#3fb950", "accent2": "#58a6ff", "merged": "#a371f7",
        "critical": "#ff6b6b", "high": "#ff9b50", "medium": "#e3b341", "low": "#58a6ff",
    },
    "light": {
        "bg0": "#ffffff", "bg1": "#f5f8fb", "border": "#d0d7de", "rule": "#e3e8ed",
        "text": "#1f2328", "muted": "#57606a", "faint": "#8c959f",
        "accent": "#1a7f37", "accent2": "#0969da", "merged": "#8250df",
        "critical": "#cf222e", "high": "#bc4c00", "medium": "#9a6700", "low": "#0969da",
    },
}
SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}
SEVERITY_DOT = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
CREDIT_VERB = {
    "finder": "found", "reporter": "reported", "analyst": "analysed",
    "coordinator": "coordinated", "remediation_developer": "fixed",
    "remediation_reviewer": "reviewed fix", "remediation_verifier": "verified fix",
}
LANG_COLORS = {
    "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "Python": "#3572A5", "Go": "#00ADD8",
    "Rust": "#dea584", "Java": "#b07219", "C": "#555555", "C++": "#f34b7d", "C#": "#178600",
    "PHP": "#4F5D95", "Ruby": "#701516", "Shell": "#89e051", "Vue": "#41b883",
    "Svelte": "#ff3e00", "Kotlin": "#A97BFF", "Swift": "#F05138", "Dart": "#00B4AB",
    "HTML": "#e34c26", "CSS": "#663399", "SCSS": "#c6538c", "Lua": "#000080",
    "Elixir": "#6e4a7e", "Scala": "#c22d40", "Haskell": "#5e5086", "Zig": "#ec915c",
    "Nix": "#7e7eff", "MDX": "#fcb32c", "Jupyter Notebook": "#DA5B0B", "Dockerfile": "#384d54",
    "Perl": "#0298c3", "R": "#198CE7", "Objective-C": "#438eff", "Clojure": "#db5855",
    "Erlang": "#B83998", "OCaml": "#ef7a08", "Solidity": "#AA6746", "Astro": "#ff5a03",
    "HCL": "#844FBA", "PowerShell": "#012456", "Assembly": "#6E4C13", "Julia": "#a270ba",
    "Nim": "#ffc200", "Gleam": "#ffaff3", "Makefile": "#427819", "Groovy": "#4298b8",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------------- HTTP

def fetch(url: str, accept: str = "application/vnd.github+json") -> tuple[bytes, str]:
    """GET a URL, returning (body, Link header). Optionally cached on disk."""
    cached = None
    if CACHE:
        cached = CACHE / re.sub(r"[^A-Za-z0-9._-]+", "_", url)[:200]
        if cached.exists():
            link = Path(f"{cached}.link")
            return cached.read_bytes(), link.read_text() if link.exists() else ""
    headers = {"Accept": accept, "User-Agent": f"{LOGIN}-profile-generator"}
    if url.startswith(API):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if TOKEN:
            headers["Authorization"] = f"Bearer {TOKEN}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as resp:
        body, link = resp.read(), resp.headers.get("Link", "")
    if cached:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(body)
        Path(f"{cached}.link").write_text(link)
    return body, link


def api(path: str, **params):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    return json.loads(fetch(url)[0])


def api_list(path: str, max_pages: int = 10, **params) -> list:
    url = API + path + "?" + urllib.parse.urlencode(params)
    out: list = []
    for _ in range(max_pages):
        body, link = fetch(url)
        page = json.loads(body)
        out.extend(page["items"] if isinstance(page, dict) else page)
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', link)
        if not nxt:
            break
        url = nxt.group(1)
    return out


def graphql(query: str, **variables) -> dict:
    req = urllib.request.Request(
        API + "/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": f"{LOGIN}-profile-generator"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        out = json.load(resp)
    if out.get("errors"):
        raise RuntimeError(out["errors"][0].get("message", "GraphQL error"))
    return out["data"]


# ---------------------------------------------------------------------- data

LIST_QUERY = """
query($login: String!) {
  user(login: $login) {
    lists(first: 100) {
      nodes { slug items(first: 100) { nodes { ... on Repository { nameWithOwner } } } }
    }
  }
}"""


def featured_repos() -> list[str]:
    """Repos from the star list if this token can see it, else config.json."""
    slug, saved = CONFIG.get("star_list"), CONFIG.get("contributions", [])
    if not (slug and TOKEN):
        return saved
    try:
        for lst in graphql(LIST_QUERY, login=LOGIN)["user"]["lists"]["nodes"]:
            if lst["slug"] == slug:
                repos = [n["nameWithOwner"] for n in lst["items"]["nodes"] if n.get("nameWithOwner")]
                if repos:
                    log(f"star list '{slug}': {len(repos)} repos")
                    if sorted(repos) != sorted(saved):  # keep the fallback in sync
                        CONFIG["contributions"] = repos
                        CONFIG_PATH.write_text(json.dumps(CONFIG, indent=2, ensure_ascii=False) + "\n")
                    return repos
        log(f"star list '{slug}' isn't visible to this token; using config.json")
    except Exception as exc:  # noqa: BLE001 - any failure falls back to config.json
        log(f"star list lookup failed ({exc}); using config.json")
    return saved


def my_issues_and_prs() -> dict[str, list[dict]] | None:
    """Issues and PRs this user opened on repos they don't own, keyed by repo (None if search failed)."""
    by_repo: dict[str, list[dict]] = {}
    for kind in ("pr", "issue"):
        try:
            found = api_list("/search/issues", q=f"author:{LOGIN} is:{kind} -user:{LOGIN}", per_page=100)
        except urllib.error.HTTPError as exc:
            log(f"search for {kind}s failed ({exc}); keeping the last snapshot's issues and PRs")
            return None
        for it in found:
            repo = it["repository_url"].split("/repos/", 1)[1].lower()
            by_repo.setdefault(repo, []).append(normalize_issue(it))
    return by_repo


def normalize_issue(it: dict) -> dict:
    pr = it.get("pull_request")
    if pr:
        merged = pr.get("merged_at")
        status = "merged" if merged else it["state"]
        when = merged or it["created_at"]
    else:
        fixed = it["state"] == "closed" and it.get("state_reason") == "completed"
        status = "fixed" if fixed else it["state"]
        when = it.get("closed_at") if fixed else it["created_at"]
    return {
        "kind": "pr" if pr else "issue",
        "id": f"#{it['number']}",
        "title": it["title"].strip(),
        "url": it["html_url"],
        "date": (when or "")[:10],
        "status": status,  # merged | fixed | open | closed (closed = declined, not shown)
    }


def credited_role(adv: dict) -> str | None:
    for c in adv.get("credits") or []:
        if (c.get("login") or "").lower() == LOGIN.lower():
            return c.get("type") or "reporter"
    for c in adv.get("credits_detailed") or []:
        if ((c.get("user") or {}).get("login") or "").lower() == LOGIN.lower():
            return c.get("type") or "reporter"
    return None


def clean_summary(summary: str, repo: dict) -> str:
    """Drop a leading 'Project: ' prefix (the card already names the project) and backticks."""
    names = {repo["name"].lower(), repo["owner"]["login"].lower()}
    m = re.match(r"^\s*([\w@./ -]{1,40}?)\s*:\s+(.+)$", summary)
    if m and m.group(1).lower().replace(" ", "") in names | {n.replace("-", "") for n in names}:
        summary = m.group(2)
    summary = summary.replace("`", "").strip()
    return summary[:1].upper() + summary[1:]


def normalize_advisory(adv: dict, role: str, repo: dict) -> dict:
    severity = (adv.get("severity") or "").lower()
    severity = "medium" if severity == "moderate" else severity
    score = None
    for key in ("cvss_v4", "cvss_v3"):
        score = score or ((adv.get("cvss_severities") or {}).get(key) or {}).get("score")
    score = score or (adv.get("cvss") or {}).get("score")
    return {
        "kind": "advisory",
        "id": adv.get("cve_id") or adv["ghsa_id"],
        "ghsa": adv["ghsa_id"],
        "cve": adv.get("cve_id"),
        "severity": severity if severity in SEVERITY_RANK else "low",
        "score": score,
        "title": clean_summary(adv.get("summary") or "", repo),
        "url": adv.get("html_url") or f"https://github.com/{repo['full_name']}/security/advisories/{adv['ghsa_id']}",
        "date": (adv.get("published_at") or "")[:10],
        "role": role,
    }


def avatar_data_uri(url: str) -> str | None:
    try:
        body, _ = fetch(url + ("&" if "?" in url else "?") + "s=80", accept="image/*")
    except Exception as exc:  # noqa: BLE001
        log(f"avatar fetch failed ({exc})")
        return None
    mime = ("image/png" if body[:4] == b"\x89PNG" else "image/jpeg" if body[:2] == b"\xff\xd8"
            else "image/gif" if body[:4] == b"GIF8" else "image/webp" if body[8:12] == b"WEBP" else None)
    return f"data:{mime};base64,{base64.b64encode(body).decode()}" if mime else None


def collect(full: str, items: list[dict] | None, prev: dict | None) -> dict:
    """Live data for one repo. A failed sub-request keeps the last snapshot's value
    instead of silently dropping a contribution."""
    prev = prev or {}
    repo = api(f"/repos/{full}")
    advisories = []
    try:
        # Only *published* advisories: drafts and reports in triage must never leak here.
        for adv in api_list(f"/repos/{repo['full_name']}/security-advisories", state="published", per_page=100):
            role = credited_role(adv)
            if role:
                advisories.append(normalize_advisory(adv, role, repo))
    except urllib.error.HTTPError as exc:
        advisories = prev.get("advisories", [])
        log(f"{full}: advisories unavailable ({exc.code}); keeping {len(advisories)} from the last snapshot")
    try:
        commits = len(api(f"/repos/{repo['full_name']}/commits", author=LOGIN, per_page=100))
    except urllib.error.HTTPError:
        commits = prev.get("commits", 0)
    if items is None:
        items = prev.get("items", [])
    override = (CONFIG.get("overrides") or {}).get(repo["full_name"]) or {}
    return {
        "repo": repo["full_name"],
        "url": repo["html_url"],
        "description": (repo.get("description") or "").strip(),
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "language": repo.get("language"),
        "avatar_url": repo["owner"]["avatar_url"],
        "advisories": sorted(advisories, key=lambda a: a["date"], reverse=True),
        "items": sorted((i for i in items if i["status"] != "closed"), key=lambda i: i["date"], reverse=True),
        "commits": commits,
        "override": override,
    }


# --------------------------------------------------------------- presentation

def month(iso: str) -> str:
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{months[int(iso[5:7]) - 1]} {iso[:4]}" if len(iso) >= 7 else ""


def short_num(n: int) -> str:
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix if v < 100 else f"{v:.0f}{suffix}"
    return str(n)


def primary(c: dict) -> dict:
    """The one contribution a card leads with, plus how many others there are."""
    o = c.get("override") or {}
    advs = sorted(c["advisories"], key=lambda a: (SEVERITY_RANK[a["severity"]], a["date"]), reverse=True)
    resolved = [i for i in c["items"] if i["status"] in ("merged", "fixed")]
    opened = [i for i in c["items"] if i["status"] == "open"]
    total = len(advs) + len(c["items"])
    if advs:
        a = advs[0]
        badge = a["severity"].upper() + (f" {float(a['score']):.1f}" if a.get("score") else "")
        p = dict(icon="shield", heading=a["id"], badge=badge, color=a["severity"], title=a["title"],
                 url=a["url"], meta=f"{CREDIT_VERB.get(a['role'], 'credited')} {month(a['date'])}")
    elif resolved or opened:
        i = (resolved or opened)[0]
        is_pr = i["kind"] == "pr"
        badge = {"merged": "MERGED", "fixed": "FIXED", "open": "OPEN"}[i["status"]]
        color = {"merged": "merged", "fixed": "accent", "open": "accent2"}[i["status"]]
        verb = {"merged": "merged", "fixed": "fixed", "open": "opened"}[i["status"]]
        p = dict(icon="pr" if is_pr else "issue", heading=f"{'PR' if is_pr else 'Issue'} {i['id']}",
                 badge=badge, color=color, title=i["title"], url=i["url"], meta=f"{verb} {month(i['date'])}")
    elif c["commits"]:
        n = c["commits"]
        p = dict(icon="commit", heading=f"{n}{'+' if n >= 100 else ''} commit{'s' if n != 1 else ''}",
                 badge="CODE", color="accent", title=c["description"], meta="",
                 url=f"{c['url']}/commits?author={LOGIN}")
        total = 1
    else:
        # Nothing public yet: the report went through private vulnerability reporting.
        # Deliberately no IDs, severities or counts until the advisory is published.
        p = dict(icon="lock", heading="Reported privately", badge="", color="faint",
                 title=c["description"], url=c["url"], meta="", private=True)
        total = 1
    for key in ("heading", "badge", "title", "url", "meta"):
        if o.get(key):
            p[key] = o[key]
    if total > 1:
        p["meta"] = (p["meta"] + " · " if p["meta"] else "") + f"+{total - 1} more"
    return p


# Rough advance widths (1/1000 em) of Helvetica/Arial; close enough to SF Pro and
# Segoe UI to keep sans-serif text inside its box.
_ADV: dict[str, int] = {}
for _chars, _w in (("ijl.,:;!|'", 230), ("ftI[]()/\\-\" ", 300), ("r`{}*", 340), ("Jcksvxyz?", 510),
                   ("abdeghnopqu#$_0123456789", 560), ("FTLZ+=<>~^", 600), ("ABEKPSVXY&", 670),
                   ("CDHNRUw", 720), ("GOQ", 780), ("mM", 840), ("W%", 950), ("@…—", 1015)):
    for _c in _chars:
        _ADV[_c] = _w


def text_width(s: str, size: float, mono: bool = False, bold: bool = False) -> float:
    if mono:
        return len(s) * size * 0.61
    return sum(_ADV.get(ch, 600) for ch in s) * size / 1000 * (1.08 if bold else 1.03)


def ellipsize(s: str, width: float, size: float, **kw) -> str:
    if text_width(s, size, **kw) <= width:
        return s
    while s and text_width(s + "…", size, **kw) > width:
        s = s[:-1]
    return s.rstrip(" ,.;:-") + "…"


def wrap(text: str, width: float, size: float, max_lines: int, **kw) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}" if cur else word
        if not cur or text_width(trial, size, **kw) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [ellipsize(" ".join(lines[max_lines - 1:]) + " …", width, size, **kw)]
    return [ellipsize(line, width, size, **kw) for line in lines]


def icon(kind: str, x: float, y: float, color: str) -> str:
    stroke = f'fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    body = {
        "shield": f'<path d="M8 1.2 13.6 3.4v4.3c0 3.3-2.3 6.1-5.6 7.1-3.3-1-5.6-3.8-5.6-7.1V3.4z" {stroke}/>'
                  f'<path d="M5.5 8.1l1.8 1.8 3.3-3.5" {stroke}/>',
        "issue": f'<circle cx="8" cy="8" r="6.2" {stroke}/><circle cx="8" cy="8" r="1.7" fill="{color}"/>',
        "pr": f'<circle cx="4.5" cy="3.4" r="1.9" {stroke}/><circle cx="4.5" cy="12.6" r="1.9" {stroke}/>'
              f'<circle cx="11.5" cy="12.6" r="1.9" {stroke}/><path d="M4.5 5.3v5.4M4.5 5.6c0 3.4 7 2.2 7 5.1" {stroke}/>',
        "commit": f'<circle cx="8" cy="8" r="2.7" {stroke}/><path d="M1.2 8h4.1M10.7 8h4.1" {stroke}/>',
        "lock": f'<rect x="2.8" y="7" width="10.4" height="7.6" rx="1.6" {stroke}/>'
                f'<path d="M5.2 7V5.1a2.8 2.8 0 0 1 5.6 0V7" {stroke}/><circle cx="8" cy="10.8" r="1.1" fill="{color}"/>',
    }[kind]
    return f'<g transform="translate({x} {y})">{body}</g>'


def star(cx: float, cy: float, r: float, fill: str) -> str:
    pts = " ".join(
        f"{cx + (r if k % 2 == 0 else r * 0.48) * math.cos(-math.pi / 2 + k * math.pi / 5):.2f},"
        f"{cy + (r if k % 2 == 0 else r * 0.48) * math.sin(-math.pi / 2 + k * math.pi / 5):.2f}"
        for k in range(10)
    )
    return f'<polygon points="{pts}" fill="{fill}"/>'


def svg_root(w: int, h: int, title: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
            f"<title>{esc(title)}</title>{body}</svg>\n")


CARD_W, CARD_H, COMPACT_H, PAD = 400, 176, 100, 18


def card_svg(c: dict, p: dict, avatar: str | None, theme: str) -> str:
    """Full card for a public finding; compact card (repo + lock line) for private reports."""
    compact = bool(p.get("private"))
    t, W, H = THEMES[theme], CARD_W, (COMPACT_H if compact else CARD_H)
    accent = t.get(p["color"], t["accent"])
    owner, name = c["repo"].split("/", 1)
    out = [
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t["bg0"]}"/>'
        f'<stop offset="1" stop-color="{t["bg1"]}"/></linearGradient>',
        f'<clipPath id="card"><rect width="{W}" height="{H}" rx="10"/></clipPath>',
        f'<clipPath id="av"><rect x="{PAD}" y="{PAD}" width="34" height="34" rx="8"/></clipPath>',
        "</defs>",
        f'<g clip-path="url(#card)"><rect width="{W}" height="{H}" fill="url(#bg)"/>'
        f'<rect x="0" y="0" width="3" height="{H}" fill="{accent}"/></g>',
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="9.5" fill="none" stroke="{t["border"]}"/>',
    ]
    # Avatar (embedded, because SVGs shown as images can't load external URLs).
    if avatar:
        out.append(f'<image x="{PAD}" y="{PAD}" width="34" height="34" href="{avatar}" xlink:href="{avatar}" '
                   f'clip-path="url(#av)" preserveAspectRatio="xMidYMid slice"/>')
    else:
        out.append(f'<rect x="{PAD}" y="{PAD}" width="34" height="34" rx="8" fill="{t["rule"]}"/>'
                   f'<text x="{PAD + 17}" y="{PAD + 22}" text-anchor="middle" font-family="{SANS}" font-size="14" '
                   f'font-weight="700" fill="{t["muted"]}">{esc(owner[:1].upper())}</text>')
    out.append(f'<rect x="{PAD}" y="{PAD}" width="34" height="34" rx="8" fill="none" stroke="{t["border"]}"/>')

    # Stars, right-aligned; the repo name gets whatever room is left.
    stars = short_num(c["stars"])
    sw = text_width(stars, 12, mono=True)
    out.append(star(W - PAD - sw - 9, 28, 6, t["medium"] if theme == "dark" else "#bf8700"))
    out.append(f'<text x="{W - PAD}" y="{32}" text-anchor="end" font-family="{MONO}" font-size="12" '
               f'fill="{t["muted"]}">{stars}</text>')
    name_room = W - PAD - sw - 22 - (PAD + 46)
    owner_txt, name_txt = f"{owner} / ", name
    if text_width(owner_txt + name_txt, 15, bold=True) > name_room:
        owner_txt = ellipsize(owner, max(30, name_room - text_width(name, 15, bold=True) - 20), 15) + " / "
        name_txt = ellipsize(name, name_room - text_width(owner_txt, 15), 15, bold=True)
    out.append(f'<text x="{PAD + 46}" y="32" font-family="{SANS}" font-size="15">'
               f'<tspan fill="{t["muted"]}">{esc(owner_txt)}</tspan>'
               f'<tspan fill="{t["text"]}" font-weight="600">{esc(name_txt)}</tspan></text>')
    if c["description"]:
        desc = ellipsize(c["description"], W - PAD - (PAD + 46), 12)
        out.append(f'<text x="{PAD + 46}" y="49" font-family="{SANS}" font-size="12" '
                   f'fill="{t["muted"]}">{esc(desc)}</text>')
    out.append(f'<line x1="{PAD}" y1="66.5" x2="{W - PAD}" y2="66.5" stroke="{t["rule"]}"/>')

    if compact:
        out.append(icon("lock", PAD, 72, t["muted"]))
        out.append(f'<text x="{PAD + 24}" y="85" font-family="{MONO}" font-size="12" font-weight="700" '
                   f'fill="{t["muted"]}">{esc(p["heading"])}</text>')
        if c["language"]:
            lw = text_width(c["language"], 11.5)
            out.append(f'<circle cx="{W - PAD - lw - 11:.1f}" cy="81" r="5" '
                       f'fill="{LANG_COLORS.get(c["language"], t["faint"])}"/>'
                       f'<text x="{W - PAD}" y="85" text-anchor="end" font-family="{SANS}" font-size="11.5" '
                       f'fill="{t["muted"]}">{esc(c["language"])}</text>')
        return svg_root(W, H, f"{c['repo']}: {p['heading'].lower()}", "".join(out))

    # The contribution itself.
    out.append(icon(p["icon"], PAD, 80, accent))
    out.append(f'<text x="{PAD + 24}" y="93" font-family="{MONO}" font-size="13" font-weight="700" '
               f'fill="{t["text"]}">{esc(p["heading"])}</text>')
    if p["badge"]:
        label = p["badge"]
        pw = text_width(label, 10.5, mono=True) + len(label) * 0.4 + 16
        px = W - PAD - pw
        out.append(f'<rect x="{px:.1f}" y="79.5" width="{pw:.1f}" height="19" rx="9.5" fill="{accent}" '
                   f'fill-opacity=".12" stroke="{accent}" stroke-opacity=".6"/>'
                   f'<text x="{px + pw / 2:.1f}" y="92.6" text-anchor="middle" font-family="{MONO}" font-size="10.5" '
                   f'font-weight="700" letter-spacing=".4" fill="{accent}">{esc(label)}</text>')
    for k, line in enumerate(wrap(p["title"] or c["description"], W - 2 * PAD, 12.5, 2)):
        out.append(f'<text x="{PAD}" y="{116 + k * 18}" font-family="{SANS}" font-size="12.5" '
                   f'fill="{t["text"]}">{esc(line)}</text>')

    # Footer: language on the left, when/what on the right.
    if c["language"]:
        out.append(f'<circle cx="{PAD + 5}" cy="{H - PAD - 4}" r="5" fill="{LANG_COLORS.get(c["language"], t["faint"])}"/>'
                   f'<text x="{PAD + 16}" y="{H - PAD}" font-family="{SANS}" font-size="11.5" '
                   f'fill="{t["muted"]}">{esc(c["language"])}</text>')
    if p["meta"]:
        out.append(f'<text x="{W - PAD}" y="{H - PAD}" text-anchor="end" font-family="{MONO}" font-size="11" '
                   f'fill="{t["faint"]}">{esc(p["meta"])}</text>')
    title = f"{c['repo']}: {p['heading']}" + (f" ({p['badge'].lower()})" if p["badge"] else "")
    return svg_root(W, H, title, "".join(out))


def stats_svg(tiles: list[tuple[str, str]], theme: str) -> str:
    t, W, H = THEMES[theme], 820, 92
    tw = W / len(tiles)
    out = [
        "<defs>",
        f'<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{t["bg0"]}"/>'
        f'<stop offset="1" stop-color="{t["bg1"]}"/></linearGradient>',
        f'<linearGradient id="num" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{t["accent"]}"/>'
        f'<stop offset="1" stop-color="{t["accent2"]}"/></linearGradient>',
        "</defs>",
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="10" fill="url(#bg)" stroke="{t["border"]}"/>',
    ]
    for i, (value, label) in enumerate(tiles):
        cx = tw * i + tw / 2
        if i:
            out.append(f'<line x1="{tw * i:.1f}" y1="22" x2="{tw * i:.1f}" y2="{H - 22}" stroke="{t["rule"]}"/>')
        delay = 0.15 + i * 0.15
        out.append(
            f'<g><animate attributeName="opacity" values="0;0;1" keyTimes="0;{delay / (delay + 0.6):.3f};1" '
            f'dur="{delay + 0.6:.2f}s" fill="freeze"/>'
            f'<text x="{cx:.1f}" y="49" text-anchor="middle" font-family="{SANS}" font-size="30" font-weight="700" '
            f'fill="url(#num)">{esc(value)}</text>'
            f'<text x="{cx:.1f}" y="71" text-anchor="middle" font-family="{MONO}" font-size="10.5" '
            f'letter-spacing="1.2" fill="{t["muted"]}">{esc(label.upper())}</text></g>'
        )
    return svg_root(W, H, " · ".join(f"{v} {l}" for v, l in tiles), "".join(out))


def hidden_until(t: float, attr: str = "opacity", value: str = "0") -> str:
    """SMIL that hides an element until time t. The resting state is the visible,
    finished frame, so renderers that don't animate still show everything."""
    return f'<set attributeName="{attr}" to="{value}" begin="0s" end="{t:.2f}s"/>'


def header_svg(name: str, tagline: str, repo_names: list[str]) -> str:
    """A terminal that types out who you are and which projects you've contributed to."""
    W, size, cw, lh, x0 = 860, 15, 9.0, 27, 26
    green, blue, white, grey, purple = "#3fb950", "#58a6ff", "#e6edf3", "#8b949e", "#d2a8ff"
    prompt = [(f"{LOGIN}@github", green), (":", white), ("~", blue), ("$", white)]
    plen = sum(len(s) for s, _ in prompt)
    cmd_x = x0 + (plen + 1) * cw
    cols = int((W - 150 - 70 - x0) / cw)  # keep text clear of the shield emblem on the right

    focus_rows, row_txt = [], ""
    for word in tagline.split():
        if row_txt and len(row_txt) + 1 + len(word) > cols:
            focus_rows.append(row_txt)
            row_txt = word
        else:
            row_txt = f"{row_txt} {word}".strip()
    if row_txt:
        focus_rows.append(row_txt)

    ls_rows, row, used = [], [], 0
    for n in repo_names:
        if row and used + 2 + len(n) > cols:
            ls_rows.append(row)
            row, used = [], 0
        row.append((used + (2 if row else 0), n))
        used += (2 if len(row) > 1 else 0) + len(n)
    if row:
        ls_rows.append(row)

    script = [
        ("whoami", [[(0, name, white, True), (len(name) + 2, f"@{LOGIN}", grey, False)]]),
        ("cat focus.txt", [[(0, r, purple, False)] for r in focus_rows]),
        ("ls security-contributions/", [[(col, n, blue, True) for col, n in r] for r in ls_rows]),
    ]
    n_lines = sum(1 + len(outs) for _, outs in script) + 1
    H = 36 + 32 + (n_lines - 1) * lh + 24

    defs, body = [], []
    font = f'font-family="{MONO}" font-size="{size}"'

    def prompt_svg(y: float) -> str:
        spans, col = [], 0
        for s, color in prompt:
            spans.append(f'<tspan x="{x0 + col * cw:.1f}" fill="{color}">{esc(s)}</tspan>')
            col += len(s)
        return f'<text y="{y}" {font} font-weight="600">{"".join(spans)}</text>'

    t, line = 0.5, 0
    for i, (cmd, outs) in enumerate(script):
        y = 68 + line * lh
        n = len(cmd)
        per = 0.07
        start, dur = t + 0.25, per * (n + 1)
        end = start + dur
        shown = end + 0.3
        steps = [f"{k * cw:.1f}" for k in range(n + 1)]
        keys = ";".join(f"{k / (n + 1):.4f}" for k in range(n + 1))
        defs.append(f'<clipPath id="type{i}"><rect x="{cmd_x:.1f}" y="{y - 18}" width="{n * cw + 4:.1f}" height="26">'
                    f'{hidden_until(start, "width")}'
                    f'<animate attributeName="width" begin="{start:.2f}s" dur="{dur:.2f}s" fill="freeze" '
                    f'calcMode="discrete" values="{";".join(steps)}" keyTimes="{keys}"/></rect></clipPath>')
        body.append(f"<g>{hidden_until(t)}"
                    f"{prompt_svg(y)}"
                    f'<text x="{cmd_x:.1f}" y="{y}" {font} fill="{white}" clip-path="url(#type{i})" '
                    f'textLength="{n * cw:.1f}" lengthAdjust="spacing">{esc(cmd)}</text></g>')
        cursor_x = ";".join(f"{cmd_x + k * cw:.1f}" for k in range(n + 1))
        body.append(f'<rect x="{cmd_x:.1f}" y="{y - 14}" width="{cw:.0f}" height="18" fill="{green}" visibility="hidden">'
                    f'<set attributeName="visibility" to="visible" begin="{t:.2f}s"/>'
                    f'<animate attributeName="x" begin="{start:.2f}s" dur="{dur:.2f}s" fill="freeze" '
                    f'calcMode="discrete" values="{cursor_x}" keyTimes="{keys}"/>'
                    f'<set attributeName="visibility" to="hidden" begin="{shown:.2f}s"/></rect>')
        line += 1
        for row_spans in outs:
            y = 68 + line * lh
            spans = "".join(
                f'<tspan x="{x0 + col * cw:.1f}" fill="{color}"{" font-weight=" + chr(34) + "700" + chr(34) if bold else ""}>'
                f"{esc(s)}</tspan>"
                for col, s, color, bold in row_spans
            )
            body.append(f'<g>{hidden_until(shown)}<text y="{y}" {font}>{spans}</text></g>')
            line += 1
        t = shown + 0.45

    y = 68 + line * lh
    body.append(f"<g>{hidden_until(t)}{prompt_svg(y)}</g>")
    body.append(f'<rect x="{cmd_x:.1f}" y="{y - 14}" width="{cw:.0f}" height="18" fill="{green}">{hidden_until(t)}'
                f'<animate attributeName="opacity" values="1;0" keyTimes="0;0.5" dur="1.1s" begin="{t:.2f}s" '
                f'repeatCount="indefinite" calcMode="discrete"/></rect>')

    chrome = (
        f'<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0d1117"/>'
        f'<stop offset="1" stop-color="#0a0e14"/></linearGradient>'
        f'<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{blue}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{blue}" stop-opacity=".07"/><stop offset="1" stop-color="{blue}" stop-opacity="0"/>'
        f"</linearGradient>"
        f'<pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse">'
        f'<path d="M22 0H0V22" fill="none" stroke="#ffffff" stroke-opacity=".035"/></pattern>'
        f'<clipPath id="win"><rect width="{W}" height="{H}" rx="12"/></clipPath>'
    )
    frame = (
        f'<g clip-path="url(#win)"><rect width="{W}" height="{H}" fill="url(#bg)"/>'
        f'<rect y="36" width="{W}" height="{H - 36}" fill="url(#grid)"/>'
        f'<rect y="-60" width="{W}" height="60" fill="url(#scan)"><animateTransform attributeName="transform" '
        f'type="translate" from="0 0" to="0 {H + 60}" dur="7s" repeatCount="indefinite"/></rect>'
        f'<rect width="{W}" height="36" fill="#161b22"/><line x1="0" y1="36.5" x2="{W}" y2="36.5" stroke="#30363d"/></g>'
        f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="11.5" fill="none" stroke="#30363d"/>'
        f'<circle cx="20" cy="18" r="6" fill="#ff5f57"/><circle cx="40" cy="18" r="6" fill="#febc2e"/>'
        f'<circle cx="60" cy="18" r="6" fill="#28c840"/>'
        f'<text x="{W / 2}" y="22.5" text-anchor="middle" font-family="{MONO}" font-size="12.5" fill="{grey}">'
        f"{esc(LOGIN)}@github: ~</text>"
    )
    # Shield emblem on the right: the check draws itself once the typing is done,
    # then two rings pulse out from it.
    sx, sy = W - 150, 36 + (H - 36) / 2 + 2
    chrome += (
        f'<linearGradient id="shield" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{green}"/>'
        f'<stop offset="1" stop-color="{blue}"/></linearGradient>'
        f'<radialGradient id="glow"><stop offset="0" stop-color="{green}" stop-opacity=".16"/>'
        f'<stop offset="1" stop-color="{green}" stop-opacity="0"/></radialGradient>'
    )
    rings = "".join(
        f'<circle r="58" fill="none" stroke="{green}" stroke-width="1.5" stroke-opacity="0">'
        f'<animate attributeName="r" values="58;104" dur="3.2s" begin="{t + 0.6 + k * 1.6:.2f}s" repeatCount="indefinite"/>'
        f'<animate attributeName="stroke-opacity" values=".45;0" dur="3.2s" begin="{t + 0.6 + k * 1.6:.2f}s" '
        f'repeatCount="indefinite"/></circle>'
        for k in range(2)
    )
    emblem = (
        f'<g transform="translate({sx} {sy})"><circle r="96" fill="url(#glow)"/>{rings}'
        f'<path d="M0-64 52-43V2c0 33-22 57-52 67C-30 59-52 35-52 2V-43z" fill="{green}" fill-opacity=".06" '
        f'stroke="url(#shield)" stroke-width="2.5" stroke-linejoin="round"/>'
        f'<path d="M0-50 40-34V2c0 25-17 44-40 52C-23 46-40 27-40 2V-34z" fill="none" stroke="url(#shield)" '
        f'stroke-opacity=".35" stroke-width="1"/>'
        f'<path d="M-19 1-5 15 21-13" fill="none" stroke="{green}" stroke-width="6" stroke-linecap="round" '
        f'stroke-linejoin="round" stroke-dasharray="62">'
        f'<animate attributeName="stroke-dashoffset" values="62;62;0" keyTimes="0;{t / (t + 0.6):.3f};1" '
        f'dur="{t + 0.6:.2f}s" fill="freeze"/></path></g>'
    )
    title = f"{LOGIN}@github:~$ whoami: {name}. {tagline}. Security contributions: {', '.join(repo_names)}"
    return svg_root(W, H, title, f"<defs>{chrome}{''.join(defs)}</defs>{frame}{emblem}{''.join(body)}")


# ------------------------------------------------------------------ README

def picture(base: str, alt: str, width: str) -> str:
    return (f'<picture><source media="(prefers-color-scheme: dark)" srcset="{base}-dark.svg">'
            f'<img src="{base}-light.svg" alt="{esc(alt)}" width="{width}"></picture>')


def md_cell(s: str) -> str:
    s = html.escape(s, quote=False)
    return re.sub(r"([\\|\[\]*_`])", r"\\\1", s)


def card_grid(cards: list[dict]) -> list[str]:
    out = ['<p align="center">']
    link_to = CONFIG.get("card_link", "contribution")
    for c in cards:
        p = c["primary"]
        href = p["url"] if link_to == "contribution" else c["url"]
        alt = f"{c['repo']}: {p['heading']}" + (f" ({p['badge'].lower()})" if p["badge"] else "")
        alt += f". {p['title']}" if p["title"] else ""
        out.append(f'<a href="{esc(href)}">{picture(c["card"], alt, str(CARD_W))}</a>')
    return out + ["</p>"]


def contributions_block(cards: list[dict], tiles: list[tuple[str, str]]) -> str:
    out = ['<p align="center">', picture("assets/stats", " · ".join(f"{v} {l}" for v, l in tiles), "100%"), "</p>"]
    public = [c for c in cards if not c["primary"].get("private")]
    private = [c for c in cards if c["primary"].get("private")]
    if public and private:
        out += ["", "### Disclosed", ""]
    out += card_grid(public)
    if private:
        if public:
            out += ["", "### Reported privately", ""]
        out += ["Sent through GitHub's private vulnerability reporting. Details stay private until each "
                "advisory is published, then the card switches to the full write-up.", ""]
        out += card_grid(private)

    rows = []
    for c in cards:
        for a in c["advisories"]:
            ids = " · ".join(x for x in (a["cve"], a["ghsa"]) if x)
            rows.append((a["date"], c, f"[{ids}]({a['url']}) {md_cell(a['title'])}",
                         f"{SEVERITY_DOT[a['severity']]}&nbsp;{a['severity'].capitalize()}"))
        for i in c["items"]:
            label = f"PR&nbsp;{i['id']}" if i["kind"] == "pr" else f"Issue&nbsp;{i['id']}"
            status = {"merged": "🟣&nbsp;Merged", "fixed": "✅&nbsp;Fixed", "open": "⏳&nbsp;Open"}[i["status"]]
            rows.append((i["date"], c, f"[{label}]({i['url']}) {md_cell(i['title'])}", status))
    if rows:
        rows.sort(key=lambda r: r[0], reverse=True)
        out += ["", "<details>", f"<summary><b>📋 Disclosure log</b> ({len(rows)} entr{'y' if len(rows) == 1 else 'ies'})</summary>",
                "", "| Date | Project | Finding | Status |", "| :-- | :-- | :-- | :-- |"]
        # U+2011 (non-breaking hyphen) keeps dates on one line in narrow columns.
        out += [f"| {d.replace('-', chr(0x2011))} | [{c['repo']}]({c['url']}) | {finding} | {status} |"
                for d, c, finding, status in rows]
        out += ["", "</details>"]
    return "\n".join(out)


def replace_block(text: str, name: str, content: str) -> str:
    pattern = re.compile(rf"(<!-- BEGIN:{name}[^>]*-->)(.*?)(<!-- END:{name} -->)", re.S)
    if not pattern.search(text):
        raise SystemExit(f"README.md is missing the <!-- BEGIN:{name} --> / <!-- END:{name} --> markers")
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", text, count=1)


# -------------------------------------------------------------------- main

def main() -> None:
    previous = {}
    if SNAPSHOT_PATH.exists():
        previous = {c["repo"].lower(): c for c in json.loads(SNAPSHOT_PATH.read_text()).get("projects", [])}

    repos = featured_repos()
    if not repos:
        raise SystemExit("No repositories to feature: add some to config.json 'contributions'.")
    items = my_issues_and_prs()

    cards = []
    for full in repos:
        try:
            c = collect(full, None if items is None else items.get(full.lower(), []), previous.get(full.lower()))
        except urllib.error.HTTPError as exc:
            if full.lower() not in previous:
                log(f"{full}: skipped ({exc})")
                continue
            log(f"{full}: {exc}; reusing the last snapshot")
            c = previous[full.lower()]
        cards.append(c)

    for c in cards:
        c["primary"] = primary(c)
    cards.sort(key=lambda c: (not c["primary"].get("private"),
                              max([SEVERITY_RANK[a["severity"]] for a in c["advisories"]] or [0]),
                              sum(i["status"] in ("merged", "fixed") for i in c["items"]) + len(c["advisories"]),
                              c["stars"]), reverse=True)

    # Cards (light + dark), removing ones for projects no longer featured.
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    keep = set()
    for c in cards:
        slug = re.sub(r"[^a-z0-9]+", "-", c["repo"].lower()).strip("-")
        c["card"] = f"assets/cards/{slug}"
        avatar = avatar_data_uri(c["avatar_url"])
        for theme in THEMES:
            path = CARDS_DIR / f"{slug}-{theme}.svg"
            path.write_text(card_svg(c, c["primary"], avatar, theme))
            keep.add(path.name)
    for stale in CARDS_DIR.glob("*.svg"):
        if stale.name not in keep:
            stale.unlink()

    advisories = sum(len(c["advisories"]) for c in cards)
    resolved = advisories + sum(i["status"] in ("merged", "fixed") for c in cards for i in c["items"])
    tiles = [(str(len(cards)), "projects" if len(cards) != 1 else "project")]
    if advisories:
        tiles.append((str(advisories), "published advisories" if advisories != 1 else "published advisory"))
    if resolved > advisories:
        tiles.append((str(resolved), "fixed upstream"))
    tiles.append((short_num(sum(c["stars"] for c in cards)), "stars across projects"))
    for theme in THEMES:
        (ASSETS / f"stats-{theme}.svg").write_text(stats_svg(tiles, theme))

    name = CONFIG.get("name") or api(f"/users/{LOGIN}").get("name") or LOGIN
    short_names = [c["repo"].split("/")[1] for c in cards]
    if len(set(n.lower() for n in short_names)) != len(short_names):
        short_names = [c["repo"].replace("/", "-") for c in cards]
    (ASSETS / "header.svg").write_text(header_svg(name, CONFIG.get("tagline", ""), short_names))

    readme = README_PATH.read_text()
    readme = replace_block(readme, "contributions", contributions_block(cards, tiles))
    README_PATH.write_text(readme)

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    snapshot = [{k: v for k, v in c.items() if k not in ("primary", "card")} for c in cards]
    SNAPSHOT_PATH.write_text(json.dumps({"user": LOGIN, "projects": snapshot}, indent=2, ensure_ascii=False) + "\n")
    log(f"featured {len(cards)} projects: " + ", ".join(c["repo"] for c in cards))


if __name__ == "__main__":
    main()
