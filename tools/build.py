# /// script
# requires-python = ">=3.11"
# dependencies = ["pyyaml>=6"]
# ///
"""Validate content/ and write the published JSON under docs/v1/.

Run:    uv run tools/build.py [--report]
Output:
  docs/v1/packs/<id>.json  one pack each (shape mirrored by ancestor-app's :content PackModels.kt)
  docs/v1/manifest.json    schema version, content version, sha256 per pack

--report lists every DRAFT item and what it still needs before it can be VERIFIED.
"""

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
OUT = ROOT / "docs" / "v1"
SCHEMA_VERSION = 1

STATUSES = {"DRAFT", "VERIFIED"}
MISSION_TYPES = {"own", "collect", "zoom", "level_subagent", "spot_glitch", "open_capsules", "automate"}
FRAGMENT_KINDS = {"name", "today", "food", "worry", "pleasure", "weather"}
RARITIES = {"common", "rare", "legendary"}
EFFECTS = {"automate", "multiply", "bonus_chance"}


def load_yaml(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def big(value) -> str:
    """A number as the string BigNum parses: plain integers below 1e15, else '<mantissa>e<exponent>'."""
    v = float(value)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    mantissa, exponent = f"{v:.15e}".split("e")
    mantissa = mantissa.rstrip("0").rstrip(".")
    return f"{mantissa}e{int(exponent)}"


class Checker:
    def __init__(self, sources: dict):
        self.sources = sources
        self.errors: list[str] = []
        self.drafts: list[str] = []
        self.cited: set[str] = set()

    def err(self, where: str, msg: str):
        self.errors.append(f"{where}: {msg}")

    def status(self, where: str, item: dict) -> str:
        s = item.get("status", "DRAFT")
        if s not in STATUSES:
            self.err(where, f"status must be DRAFT or VERIFIED, not {s!r}")
        return s

    def sourced(self, where: str, item: dict, exempt: bool = False) -> list[dict]:
        """Checks an item's sources and records drafts. Returns the cleaned source refs."""
        refs = []
        for ref in item.get("sources") or []:
            sid = ref.get("source")
            if sid not in self.sources:
                self.err(where, f"cites unknown source {sid!r}")
                continue
            self.cited.add(sid)
            refs.append({"source": sid, "locator": str(ref.get("locator", ""))})
        status = self.status(where, item)
        if status == "VERIFIED":
            if not refs and not exempt:
                self.err(where, "VERIFIED but has no source")
            if any("[VERIFY]" in r["locator"] for r in refs):
                self.err(where, "VERIFIED but a locator still says [VERIFY]")
        else:
            need = "needs a source" if not refs and not exempt else "needs checking against its source"
            self.drafts.append(f"{where}: {need}")
        return refs

    def naming_source(self, where: str, raw) -> dict | None:
        if not raw:
            return None
        if raw.get("source") not in self.sources:
            self.err(where, f"naming_source cites unknown source {raw.get('source')!r}")
            return None
        self.cited.add(raw["source"])
        return {"source": raw["source"], "locator": str(raw.get("locator", ""))}


def build_generators(c: Checker, pid: str, raw: list, occupations: set[str]) -> list[dict]:
    gens = []
    ids = set()
    for g in raw:
        where = f"{pid} generator {g.get('id')}"
        if g["id"] in ids:
            c.err(where, "duplicate id")
        ids.add(g["id"])
        if float(g["cost_growth"]) <= 1:
            c.err(where, "cost_growth must be above 1")
        if float(g["cycle_seconds"]) <= 0:
            c.err(where, "cycle_seconds must be positive")
        if g["tier"] > 1 and "unlock_at" not in g:
            c.err(where, "tiers above 1 need unlock_at")
        for p in g.get("people", []):
            if p not in occupations:
                c.err(where, f"people lists unknown occupation {p!r}")
        gens.append({
            "id": g["id"],
            "tier": g["tier"],
            "name": g["name"],
            "baseCost": big(g["base_cost"]),
            "attentionCost": big(g["attention_cost"]),
            "costGrowth": float(g["cost_growth"]),
            "cycleSeconds": float(g["cycle_seconds"]),
            "outputPerUnit": big(g["output_per_unit"]),
            "unlockAt": big(g["unlock_at"]) if "unlock_at" in g else None,
            "people": g.get("people", []),
        })
    tiers = sorted(g["tier"] for g in gens)
    if tiers != list(range(1, len(gens) + 1)):
        c.err(pid, f"generator tiers must run 1..{len(gens)}, got {tiers}")
    return sorted(gens, key=lambda g: g["tier"])


def build_start(c: Checker, pid: str, raw: dict, currency_id: str, gen_ids: set[str]) -> dict:
    for gid in raw.get("generators", {}):
        if gid not in gen_ids:
            c.err(pid, f"start lists unknown generator {gid!r}")
    return {
        "currency": big(raw.get(currency_id, 0)),
        "attention": big(raw.get("attention", 0)),
        "generators": {k: big(v) for k, v in raw.get("generators", {}).items()},
    }


def build_ranks(c: Checker, pid: str, raw: list, gen_ids: set[str], subagent_ids: set[str],
                skip_slack: bool) -> list[dict]:
    ranks = []
    mission_ids = set()
    for i, r in enumerate(raw, start=1):
        where = f"{pid} rank {r.get('rank')}"
        if r["rank"] != i:
            c.err(where, f"ranks must be numbered 1..n in order (expected {i})")
        missions = []
        for m in r["missions"]:
            mw = f"{where} mission {m.get('id')}"
            if m["id"] in mission_ids:
                c.err(mw, "duplicate id")
            mission_ids.add(m["id"])
            if m["type"] not in MISSION_TYPES:
                c.err(mw, f"unknown type {m['type']!r}")
            if m["type"] in ("own", "automate") and m.get("generator") not in gen_ids:
                c.err(mw, f"unknown generator {m.get('generator')!r}")
            if m["type"] == "level_subagent" and m.get("subagent") != "any" and m.get("subagent") not in subagent_ids:
                c.err(mw, f"unknown subagent {m.get('subagent')!r}")
            if m["type"] != "automate" and float(m.get("amount", 0)) <= 0:
                c.err(mw, "amount must be positive")
            missions.append({
                "id": m["id"],
                "type": m["type"],
                "generator": m.get("generator"),
                "subagent": m.get("subagent"),
                "amount": big(m.get("amount", 1)),
            })
        available = len(missions)
        required = r["required"]
        if skip_slack:
            expected = required if r["rank"] <= 3 else required + 3
            if available != expected:
                c.err(where, f"has {available} missions; rank {r['rank']} with {required} required needs {expected}")
        elif required > available:
            c.err(where, f"requires {required} of only {available} missions")
        for s in r.get("guarantees", []):
            if s not in subagent_ids:
                c.err(where, f"guarantees unknown subagent {s!r}")
        ranks.append({
            "rank": r["rank"],
            "required": required,
            "guarantees": r.get("guarantees", []),
            "story": str(r["story"]).strip(),
            "missions": missions,
        })
    return ranks


def build_subagent(c: Checker, pid: str, s: dict, gen_ids: set[str]) -> dict:
    where = f"{pid} subagent {s.get('id')}"
    if s["rarity"] not in RARITIES:
        c.err(where, f"unknown rarity {s['rarity']!r}")
    effect = dict(s["effect"])
    if effect.get("type") not in EFFECTS:
        c.err(where, f"unknown effect {effect.get('type')!r}")
    if effect.get("type") == "automate" and effect.get("generator") not in gen_ids:
        c.err(where, f"automates unknown generator {effect.get('generator')!r}")
    effect = {
        "type": effect["type"],
        "generator": effect.get("generator"),
        "scope": effect.get("scope"),
        "perLevel": float(effect.get("per_level", effect.get("speed_per_level", 0))),
    }
    pages = []
    for i, p in enumerate(s.get("pages", []), start=1):
        pw = f"{where} page L{p.get('level')}"
        if p["level"] != i:
            c.err(pw, f"pages must run level 1..n in order (expected {i})")
        pages.append({
            "level": p["level"],
            "title": p["title"],
            "text": str(p["text"]).strip(),
            "status": p.get("status", "DRAFT"),
            "sources": c.sourced(pw, p),
        })
    return {"id": s["id"], "name": s["name"], "rarity": s["rarity"], "effect": effect, "pages": pages}


def cited_sources(c: Checker, before: set[str]) -> dict:
    """The sources this pack cites, so each pack stands alone."""
    ids = c.cited - before
    def fields(src: dict) -> dict:
        out = {k: src.get(k) for k in ("title", "author", "url", "note")}
        out["year"] = str(src["year"]) if src.get("year") is not None else None
        return out
    return {sid: fields(c.sources[sid]) for sid in sorted(ids)}


def build_era(c: Checker, folder: Path, raw: dict) -> dict:
    pid = raw["id"]
    before = set(c.cited)
    people = load_yaml(folder / "people.yaml") or {}
    subs = load_yaml(folder / "subagents.yaml") or {}
    occupations = people.get("occupations", [])
    occ_ids = {o["id"] for o in occupations}
    naming = c.naming_source(pid, raw.get("naming_source"))

    generators = build_generators(c, pid, raw["generators"], occ_ids)
    gen_ids = {g["id"] for g in generators}
    subagent_list = [build_subagent(c, pid, s, gen_ids) for s in subs.get("subagents", [])]
    subagent_ids = {s["id"] for s in subagent_list}

    fragments = []
    frag_ids = set()
    for f in people.get("fragments", []):
        where = f"{pid} fragment {f.get('id')}"
        if f["id"] in frag_ids:
            c.err(where, "duplicate id")
        frag_ids.add(f["id"])
        if f["kind"] not in FRAGMENT_KINDS:
            c.err(where, f"unknown kind {f['kind']!r}")
        for o in f.get("applies_to", []):
            if o not in occ_ids:
                c.err(where, f"applies_to unknown occupation {o!r}")
        fragments.append({
            "id": f["id"],
            "kind": f["kind"],
            "text": str(f["text"]).strip(),
            "appliesTo": f.get("applies_to", []),
            "status": f.get("status", "DRAFT"),
            "sources": c.sourced(where, f, exempt=f["kind"] == "name" and naming is not None),
        })
    for o in occupations:
        if not any(f["kind"] == "today" and o["id"] in f["appliesTo"] for f in fragments):
            c.err(f"{pid} occupation {o['id']}", "has no 'today' fragment, so zoom can't resolve anyone")

    look = " ".join(str(raw.get("look", "")).split())
    for token in re.findall(r"\{([^}]*)\}", look):
        parts = token.split(":")
        if parts[0] not in gen_ids:
            c.err(f"{pid} look", f"{{{token}}} doesn't start with a generator id")
        if len(parts) not in (1, 3):
            c.err(f"{pid} look", f"{{{token}}} must be {{id}} or {{id:singular:plural}}")

    lc = subs.get("level_cost", {})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": pid,
        "kind": "era",
        "title": raw["title"],
        "subtitle": raw.get("subtitle", ""),
        "status": raw.get("status", "DRAFT"),
        "namingSource": naming,
        "currency": {"id": raw["currency"]["id"], "name": raw["currency"]["name"]},
        "attentionPerSecond": float(raw["attention_per_second"]),
        "look": look,
        "start": build_start(c, pid, raw.get("start", {}), raw["currency"]["id"], gen_ids),
        "generators": generators,
        "maxActiveMissions": raw.get("max_active_missions", 3),
        "ranks": build_ranks(c, pid, raw["ranks"], gen_ids, subagent_ids, skip_slack=True),
        "levelCost": {
            "clarityBase": big(lc.get("clarity_base", 40)),
            "clarityGrowth": float(lc.get("clarity_growth", 1.8)),
            "cardsBase": int(lc.get("cards_base", 1)),
        },
        "subagents": subagent_list,
        "occupations": [{"id": o["id"], "name": o["name"], "age": o["age"]} for o in occupations],
        "fragments": fragments,
        "sources": cited_sources(c, before),
        "migrations": raw.get("migrations", []),
    }


def build_event(c: Checker, raw: dict, era_ids: set[str]) -> dict:
    pid = raw["id"]
    before = set(c.cited)
    if raw.get("era") not in era_ids:
        c.err(pid, f"era {raw.get('era')!r} is not an era pack")
    naming = c.naming_source(pid, raw.get("naming_source"))
    generators = build_generators(c, pid, raw["generators"], set())
    gen_ids = {g["id"] for g in generators}
    reward = build_subagent(c, pid, raw["reward"]["subagent"], gen_ids)
    if reward["rarity"] != "legendary":
        c.err(pid, "the event reward must be a legendary subagent")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": pid,
        "kind": "event",
        "title": raw["title"],
        "subtitle": raw.get("subtitle", ""),
        "status": raw.get("status", "DRAFT"),
        "era": raw["era"],
        "durationDays": raw.get("duration_days", 5),
        "namingSource": naming,
        "currency": {"id": raw["currency"]["id"], "name": raw["currency"]["name"]},
        "attentionPerSecond": float(raw["attention_per_second"]),
        "start": build_start(c, pid, raw.get("start", {}), raw["currency"]["id"], gen_ids),
        "generators": generators,
        "ranks": build_ranks(c, pid, raw["ranks"], gen_ids, {reward["id"]}, skip_slack=False),
        "reward": reward,
        "sources": cited_sources(c, before),
        "migrations": raw.get("migrations", []),
    }


def build_core(c: Checker, raw: dict, era_ids: set[str]) -> dict:
    pid = raw["id"]
    before = set(c.cited)
    glitches = []
    for g in raw.get("glitches", []):
        where = f"{pid} {g['id']}"
        for e in g.get("eras", []):
            if e not in era_ids:
                c.err(where, f"unknown era {e!r}")
        glitches.append({
            "id": g["id"],
            "object": g["object"],
            "why": str(g["why"]).strip(),
            "eras": g.get("eras", []),
            "status": g.get("status", "DRAFT"),
            "sources": c.sourced(where, g),
        })
    lo, hi = raw.get("glitch_interval_seconds", [180, 480])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": pid,
        "kind": "core",
        "title": raw.get("title", "Core"),
        "status": raw.get("status", "DRAFT"),
        "glitchIntervalSeconds": [lo, hi],
        "glitches": glitches,
        "hints": [{"id": h["id"], "text": h["text"]} for h in raw.get("hints", [])],
        "sources": cited_sources(c, before),
    }


def count_status(obj) -> dict:
    counts = {"DRAFT": 0, "VERIFIED": 0}

    def walk(o):
        if isinstance(o, dict):
            if o.get("status") in counts and ("text" in o or "why" in o):
                counts[o["status"]] += 1
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return {"draft": counts["DRAFT"], "verified": counts["VERIFIED"]}


def build(report: bool) -> int:
    sources = load_yaml(CONTENT / "sources.yaml") or {}
    c = Checker(sources)
    raws = {}
    for folder in sorted(p for p in CONTENT.iterdir() if p.is_dir()):
        raw = load_yaml(folder / "pack.yaml")
        if raw is None:
            c.err(folder.name, "missing pack.yaml")
            continue
        if raw.get("id") != folder.name:
            c.err(folder.name, f"pack id {raw.get('id')!r} must match its folder name")
        raws[folder.name] = (folder, raw)

    era_ids = {pid for pid, (_, raw) in raws.items() if raw.get("kind") == "era"}
    packs = []
    for pid, (folder, raw) in raws.items():
        kind = raw.get("kind")
        if kind == "era":
            packs.append(build_era(c, folder, raw))
        elif kind == "event":
            packs.append(build_event(c, raw, era_ids))
        elif kind == "core":
            packs.append(build_core(c, raw, era_ids))
        else:
            c.err(pid, f"unknown kind {kind!r}")

    all_subagents = [s["id"] for p in packs for s in p.get("subagents", [])] + \
                    [p["reward"]["id"] for p in packs if p["kind"] == "event"]
    for sid in {s for s in all_subagents if all_subagents.count(s) > 1}:
        c.err("subagents", f"id {sid!r} is used by more than one pack")

    if report:
        print(f"{len(c.drafts)} draft items:", *c.drafts, sep="\n  ")
    if c.errors:
        print("content errors:", *c.errors, sep="\n  ")
        return 1

    (OUT / "packs").mkdir(parents=True, exist_ok=True)
    entries = []
    for p in sorted(packs, key=lambda p: p["id"]):
        body = json.dumps(p, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        (OUT / "packs" / f"{p['id']}.json").write_bytes(body)
        entries.append({
            "id": p["id"],
            "kind": p["kind"],
            "title": p["title"],
            "file": f"packs/{p['id']}.json",
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
            **count_status(p),
        })
    for stale in (OUT / "packs").glob("*.json"):
        if stale.stem not in {p["id"] for p in packs}:
            stale.unlink()

    digest = hashlib.sha256("".join(e["sha256"] for e in entries).encode()).hexdigest()
    manifest_path = OUT / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    version = previous.get("contentVersion", 0)
    if previous.get("sha256") != digest:
        version += 1
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "contentVersion": version,
        "sha256": digest,
        "builtAt": previous.get("builtAt") if previous.get("sha256") == digest
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "packs": entries,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    drafts = sum(e["draft"] for e in entries)
    print(f"content v{version}: {len(entries)} packs, {drafts} draft items, "
          f"{sum(e['bytes'] for e in entries):,} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(build("--report" in sys.argv[1:]))
