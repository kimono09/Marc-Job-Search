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


def run(sources=None):
    if sources is None:
        sources = []
        if os.environ.get("REED_API_KEY"):
            sources.append(reed)
        if os.environ.get("ADZUNA_APP_ID") and os.environ.get("ADZUNA_APP_KEY"):
            sources.append(adzuna)
    if not sources:
        print("No API keys set: only the curated list and careers-page checks will update.")
    jobs, found, ok_all = state.setdefault("jobs", {}), set(), True
    for fn in sources:
        for q in cfg["queries"]:
            try:
                items = list(fn(q))
            except Exception as e:
                ok_all = False
                print("source failed:", fn.__name__, q, e)
                continue
            for it in items:
                if not relevant(it) or below_floor(it):
                    continue
                ind = industry(it)
                if ind != "f" and not any(w in (it["title"] + " " + it["desc"]).lower() for w in cfg["interesting"]):
                    continue
                kk = key(it["emp"], it["title"])
                found.add(kk)
                rec = jobs.get(kk)
                if rec is None:
                    jobs[kk] = rec = dict(first_seen=TODAY, pub=it["posted"], i=ind, jm=None, scored=False)
                rec.update(last_seen=TODAY, misses=0, closed=False, t=it["title"], e=it["emp"], sal=salary_text(it),
                           src=it["url"], sn=it["src"], desc=it["desc"][:1500], i=ind)
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
    out = [j for j in seed["jobs"] if not (j.get("x") and j["x"] < TODAY)]
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
        out.append(dict(id=kk, k="l", i=rec["i"], b="job board, unverified", t=rec["t"], e=rec["e"], sal=rec["sal"],
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
