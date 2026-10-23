#!/usr/bin/env python3
"""Searches Reed, Adzuna and watched careers pages, updates data/state.json, rebuilds docs/index.html."""
import os, re, json, hashlib, datetime as dt, html
import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
P = lambda *a: os.path.join(ROOT, *a)
UA = {"User-Agent": "personal-jobsearch/1.0"}
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date().isoformat()


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


cfg = load(P("config.json"), {})
seed = load(P("data", "seed.json"), {"jobs": [], "emp": [], "sp": []})
state = load(P("data", "state.json"), {"jobs": {}, "watch": {}})


def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def norm(s):
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("&", "and")).split())


def key(emp, title):
    return hashlib.sha1((norm(emp) + "|" + norm(title).replace("london", "").strip()).encode()).hexdigest()[:12]


def money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v >= 1000 else None  # ignore hourly/daily figures


def adzuna(q):
    r = requests.get("https://api.adzuna.com/v1/api/jobs/gb/search/1", headers=UA, timeout=30, params={
        "app_id": os.environ["ADZUNA_APP_ID"], "app_key": os.environ["ADZUNA_APP_KEY"], "what": q,
        "where": "London", "results_per_page": 50, "max_days_old": cfg.get("max_days_old", 14),
        "sort_by": "date", "content-type": "application/json"})
    r.raise_for_status()
    for x in r.json().get("results", []):
        yield dict(title=strip(x.get("title")), emp=(x.get("company") or {}).get("display_name", ""),
                   smin=money(x.get("salary_min")), smax=money(x.get("salary_max")),
                   est=str(x.get("salary_is_predicted")) == "1", posted=(x.get("created") or "")[:10],
                   url=x.get("redirect_url", ""), desc=strip(x.get("description")), src="Adzuna")


def reed(q):
    r = requests.get("https://www.reed.co.uk/api/1.0/search", headers=UA, timeout=30,
                     auth=(os.environ["REED_API_KEY"], ""),
                     params={"keywords": q, "locationName": "London", "distanceFromLocation": 10, "resultsToTake": 50})
    r.raise_for_status()
    for x in r.json().get("results", []):
        d = x.get("date") or ""
        posted = f"{d[6:10]}-{d[3:5]}-{d[0:2]}" if len(d) == 10 else ""
        yield dict(title=strip(x.get("jobTitle")), emp=x.get("employerName", ""),
                   smin=money(x.get("minimumSalary")), smax=money(x.get("maximumSalary")), est=False,
                   posted=posted, url=x.get("jobUrl", ""), desc=strip(x.get("jobDescription")), src="Reed")


def relevant(it):
    t = it["title"].lower()
    return any(w in t for w in cfg["title_include"]) and not any(w in t for w in cfg["title_exclude"])


def industry(it):
    text = (it["title"] + " " + it["emp"] + " " + it["desc"]).lower() + " "
    if any(w in text for w in cfg["food_words"]):
        return "f"
    for code, words in cfg["industries"].items():
        if any(w in text for w in words):
            return code
    return "o"


def salary_text(it):
    lo, hi = it["smin"], it["smax"]
    if not lo and not hi:
        return "Not shown"
    s = f"£{int(lo):,}" if lo and (not hi or lo == hi) else f"£{int(lo or hi):,}–£{int(hi):,}" if lo and hi else f"£{int(hi):,}"
    return s + (" (estimate)" if it["est"] else "")


def below_floor(it):
    top = it["smax"] or it["smin"]
    return bool(top) and not it["est"] and top < cfg["salary_floor"]


def score(it):
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        return None
    prompt = (f"Profile: {cfg['profile']}\n\nJob: {it['title']} at {it['emp']}. Salary: {salary_text(it)}.\n"
              f"Description: {it['desc'][:1500]}\n\nReturn ONLY JSON: "
              '{"jm": integer 0-100 or null, "ev": one sentence on what the advert actually says, '
              '"gap": one sentence on missing requirements or unknowns}. Use only facts in the advert; write unknown if unsure.')
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                          headers={"x-api-key": k, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": cfg["model"], "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        txt = r.json()["content"][0]["text"]
        return json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except Exception as e:
        print("score failed:", e)
        return None


def check_watch():
    alerts, seen = [], state.setdefault("watch", {})
    for w in cfg.get("watch", []):
        try:
            r = requests.get(w["url"], headers=UA, timeout=30)
            r.raise_for_status()
            text = re.sub(r"(?s)<(script|style).*?</\1>", " ", r.text)
            h = hashlib.sha1(norm(strip(text)).encode()).hexdigest()
        except Exception as e:
            print("watch failed:", w["name"], e)
            continue
        if w["url"] in seen and seen[w["url"]] != h:
            alerts.append(dict(id="w" + hashlib.sha1(w["url"].encode()).hexdigest()[:8], k="l", i=w["i"],
                               b="careers page changed", t="Careers page changed: check for new roles", e=w["name"],
                               sal="n/a", pub="Unknown", src=w["url"], sn="Careers page", jm=None,
                               ev="The page content changed since the last check.",
                               gap="The change may be unrelated to social or content roles."))
        seen[w["url"]] = h
    return alerts


def fmt(d):
    try:
        return dt.date.fromisoformat(d).strftime("%-d %b %Y")
    except (TypeError, ValueError):
        return "Unknown"


def clearly_closed(url):
    """Return True only for strong closure evidence; network failures remain unverified."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        r = requests.get(url, headers=UA, timeout=12, allow_redirects=True)
        if r.status_code in (404, 410):
            return True
        if r.status_code >= 500:
            return False
        body = norm(strip(r.text[:500000]))
        signals = [
            "this vacancy is no longer available", "this job is no longer available",
            "this job has expired", "vacancy has expired", "position has been filled",
            "applications are now closed", "this role is now closed", "job not found",
            "vacancy is closed", "no longer accepting applications"
        ]
        return any(signal in body for signal in signals)
    except requests.RequestException:
        return False


def run(sources=None):
    extensive = os.environ.get("SEARCH_MODE", "quick").lower() == "extensive"
    queries = list(dict.fromkeys(cfg["queries"] + (cfg.get("extensive_queries", []) if extensive else [])))
    print(f"Search mode: {'EXTENSIVE' if extensive else 'quick'}; {len(queries)} query phrases")
    if sources is None:
        sources = []
        if os.environ.get("REED_API_KEY"):
            sources.append(reed)
        if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
            sources.append(adzuna)
    if not sources:
        print("No API keys set: only the curated list and careers-page checks will update.")
    jobs, found, ok_all = state.setdefault("jobs", {}), set(), True
    stats = {"queries": len(queries), "sources": [], "raw": 0, "title_rejected": 0,
             "salary_rejected": 0, "relevance_rejected": 0, "accepted": 0}
    if not os.environ.get("REED_API_KEY"):
        print("WARNING: REED_API_KEY is missing; Reed will not be searched.")
    if not (os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY")):
        print("WARNING: ADZUNA_APP_ID and/or ADZUNA_APP_KEY is missing; Adzuna will not be searched.")
    for fn in sources:
        source_raw = 0
        source_accepted = 0
        for q in queries:
            try:
                items = list(fn(q))
            except Exception as e:
                ok_all = False
                print("source failed:", fn.__name__, q, e)
                continue
            for it in items:
                source_raw += 1
                stats["raw"] += 1
                if not relevant(it):
                    stats["title_rejected"] += 1
                    continue
                if below_floor(it):
                    stats["salary_rejected"] += 1
                    continue
                ind = industry(it)
                if ind != "f" and not any(w in (it["title"] + " " + it["desc"]).lower() for w in cfg["interesting"]):
                    stats["relevance_rejected"] += 1
                    continue
                stats["accepted"] += 1
                source_accepted += 1
                kk = key(it["emp"], it["title"])
                found.add(kk)
                rec = jobs.get(kk)
                if rec is None:
                    jobs[kk] = rec = dict(first_seen=TODAY, pub=it["posted"], i=ind, jm=None, scored=False)
                rec.update(last_seen=TODAY, misses=0, closed=False, t=it["title"], e=it["emp"], sal=salary_text(it),
                           src=it["url"], sn=it["src"], desc=it["desc"][:1500], i=ind)
        stats["sources"].append({"name": fn.__name__, "raw": source_raw, "accepted": source_accepted})
        print(f"SOURCE SUMMARY {fn.__name__}: {source_raw} raw listings, {source_accepted} passed filters")
    print("FILTER SUMMARY: " + json.dumps({k: v for k, v in stats.items() if k != "sources"}))
    if sources and ok_all:  # only count misses when every source answered
        for kk, rec in jobs.items():
            if kk not in found and not rec.get("closed"):
                rec["misses"] = rec.get("misses", 0) + 1
                rec["closed"] = rec["misses"] >= 2
    budget = cfg.get("max_scores_per_run", 15)
    for kk, rec in jobs.items():
        if budget and not rec.get("scored") and not rec.get("closed") and os.environ.get("ANTHROPIC_API_KEY"):
            res = score(dict(title=rec["t"], emp=rec["e"], desc=rec.get("desc", ""), smin=None, smax=None, est=False)) or {}
            rec.update(jm=res.get("jm"), ev=res.get("ev"), gap=res.get("gap"), scored=True)
            budget -= 1
    alerts = check_watch()
    seed_keys = {key(j["e"], j["t"]) for j in seed["jobs"]}
    out = []
    for j in seed["jobs"]:
        if j.get("x") and j["x"] < TODAY:
            continue
        if clearly_closed(j.get("src", "")):
            print("Excluded explicitly closed seeded listing:", j.get("t", "(untitled)"))
            continue
        out.append(j)
    cap_default = cfg["max_per_industry"]["default"]
    by_ind = {}
    for kk, rec in sorted(jobs.items(), key=lambda kv: (kv[1]["first_seen"], kv[1].get("jm") or 0), reverse=True):
        if rec.get("closed") or kk in seed_keys:
            continue
        if rec["i"] != "f" and rec.get("jm") is not None and rec["jm"] < cfg["min_score_non_food"]:
            continue
        n = by_ind.get(rec["i"], 0)
        if n >= cfg["max_per_industry"].get(rec["i"], cap_default):
            continue
        by_ind[rec["i"]] = n + 1
        out.append(dict(id=kk, k="l", i=rec["i"], b="recent board listing · not employer-verified", t=rec["t"], e=rec["e"], sal=rec["sal"],
                        pub=fmt(rec.get("pub")), fs=fmt(rec["first_seen"]), src=rec["src"], sn=rec["sn"],
                        ev=rec.get("ev") or "Scored text not available.", gap=rec.get("gap") or "Not yet assessed; check the advert.",
                        jm=rec.get("jm")))
    out += alerts
    data = dict(jobs=out, emp=seed["emp"], sp=seed["sp"])
    page = open(P("template.html"), encoding="utf-8").read()
    page = page.replace("__UPDATED__", NOW.strftime("%-d %b %Y %H:%M UTC"))
    page = page.replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    os.makedirs(P("docs"), exist_ok=True)
    open(P("docs", "index.html"), "w", encoding="utf-8").write(page)
    os.makedirs(P("data"), exist_ok=True)
    json.dump(state, open(P("data", "state.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    new = sum(1 for r in jobs.values() if r["first_seen"] == TODAY and not r.get("closed"))
    print(f"Done: {len(out)} items on page, {new} first seen today, {len(alerts)} careers-page changes.")


if __name__ == "__main__":
    run()
