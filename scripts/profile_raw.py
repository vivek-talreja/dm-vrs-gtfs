"""Profile a GTFS feed: column profile + 25 quality rules + KPIs per dimension.

Usage:
  python scripts/profile_raw.py --folder data/raw/vrs_gtfs/2026-11-07 --label before --as-of 2026-11-07
  python scripts/profile_raw.py --folder data/raw/vrs_gtfs/2026-11-07 --label before_swb --as-of 2026-11-07 --agency 6
Run the same command on the cleaned folder with --label after to compare.
"""
import argparse, os
import numpy as np
import pandas as pd

ap = argparse.ArgumentParser()
ap.add_argument("--folder", required=True)
ap.add_argument("--label", default="before")
ap.add_argument("--as-of", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
ap.add_argument("--agency", default=None, help="optional agency_id to restrict to")
ap.add_argument("--out", default="outputs")
a = ap.parse_args()
os.makedirs(a.out, exist_ok=True)
AS_OF = pd.Timestamp(a.as_of)

# ---------- load ----------
T = {}
for f in sorted(os.listdir(a.folder)):
    if f.endswith(".txt"):
        p = os.path.join(a.folder, f)
        try:
            T[f[:-4]] = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
        except Exception:
            T[f[:-4]] = pd.read_csv(p, dtype=str, encoding="utf-8-sig", engine="python", on_bad_lines="skip")
print("loaded:", {k: len(v) for k, v in T.items()})

if a.agency is not None:
    r = T["routes"][T["routes"].agency_id == str(a.agency)]
    tr = T["trips"][T["trips"].route_id.isin(r.route_id)]
    s = T["stop_times"][T["stop_times"].trip_id.isin(tr.trip_id)]
    T["routes"], T["trips"], T["stop_times"] = r, tr, s
    T["stops"] = T["stops"][T["stops"].stop_id.isin(s.stop_id)]
    if "agency" in T: T["agency"] = T["agency"][T["agency"].agency_id == str(a.agency)]
    print("restricted to agency", a.agency, {k: len(T[k]) for k in ("routes", "trips", "stop_times", "stops")})

has = lambda *names: all(n in T and len(T[n]) > 0 for n in names)
col = lambda t, c: t in T and c in T[t].columns

# ---------- column profile ----------
rows = []
for t, df in T.items():
    for c in df.columns:
        s = df[c]
        filled = s.notna() & (s.str.strip() != "")
        v = s[filled]
        rows.append(dict(table=t, column=c, rows=len(df), filled_pct=round(100 * filled.mean(), 1) if len(df) else None,
                         distinct=v.nunique(), min=v.min() if len(v) else None, max=v.max() if len(v) else None,
                         top_value=v.mode().iloc[0] if len(v) else None))
prof = pd.DataFrame(rows)
prof.to_csv(f"{a.out}/column_profile_{a.label}.csv", index=False)

# ---------- rules ----------
R = []
def rule(rid, dim, desc, sev, checked, failed):
    R.append(dict(id=rid, dimension=dim, rule=desc, severity=sev, checked=int(checked), failed=int(failed),
                  pass_rate=round(1 - failed / checked, 4) if checked else None,
                  status="PASS" if failed == 0 else "FAIL"))

def unique_rule(rid, t, cols):
    if has(t) and all(col(t, c) for c in cols):
        rule(rid, "Uniqueness", f"{t}.{'+'.join(cols)} is unique", "error", len(T[t]), T[t].duplicated(cols).sum())

def link_rule(rid, t, c, ref, rc, extra=None):
    if has(t, ref) and col(t, c) and col(ref, rc):
        ok = set(T[ref][rc])
        if extra is not None: ok |= extra
        rule(rid, "Integrity", f"{t}.{c} exists in {ref}.{rc}", "error", len(T[t]), (~T[t][c].isin(ok)).sum())

unique_rule("U1", "stops", ["stop_id"]); unique_rule("U2", "trips", ["trip_id"]); unique_rule("U3", "routes", ["route_id"])
unique_rule("U4", "stop_times", ["trip_id", "stop_sequence"]); unique_rule("U5", "calendar_dates", ["service_id", "date"])

svc = set(T["calendar"].service_id) if has("calendar") else set()
if has("calendar_dates"): svc |= set(T["calendar_dates"].service_id)
link_rule("I1", "trips", "route_id", "routes", "route_id")
if has("trips"): rule("I2", "Integrity", "trips.service_id exists in calendar/calendar_dates", "error", len(T["trips"]), (~T["trips"].service_id.isin(svc)).sum())
link_rule("I3", "stop_times", "trip_id", "trips", "trip_id")
link_rule("I4", "stop_times", "stop_id", "stops", "stop_id")
link_rule("I5", "routes", "agency_id", "agency", "agency_id")
if has("trips", "stop_times"):
    n = T["stop_times"].trip_id.value_counts()
    cnt = T["trips"].trip_id.map(n).fillna(0)
    rule("I6", "Integrity", "trip has at least 1 stop_time", "warning", len(cnt), (cnt < 1).sum())
    rule("I7", "Integrity", "trip has at least 2 stops", "warning", len(cnt), (cnt < 2).sum())
if has("stops", "stop_times"):
    rule("I8", "Integrity", "stop is served by at least one trip", "warning", len(T["stops"]), (~T["stops"].stop_id.isin(T["stop_times"].stop_id)).sum())

TIME = r"^\d{1,2}:\d{2}:\d{2}$"
if has("stop_times"):
    st = T["stop_times"]
    for c in ("arrival_time", "departure_time"):
        if c in st.columns:
            rule(f"V1{c[0]}", "Validity", f"{c} has format H:MM:SS", "error", len(st), (~st[c].fillna("").str.match(TIME)).sum())
    if "departure_time" in st.columns:
        hh = pd.to_numeric(st.departure_time.str.split(":").str[0], errors="coerce")
        rule("V2", "Validity", "departure_time below 24:00:00", "warning", len(st), (hh >= 24).sum())
def date_rule(rid, t, c):
    if has(t) and col(t, c):
        d = pd.to_datetime(T[t][c], format="%Y%m%d", errors="coerce")
        rule(rid, "Validity", f"{t}.{c} is a real date (YYYYMMDD)", "error", len(d), d.isna().sum())
date_rule("V3a", "calendar_dates", "date"); date_rule("V3b", "calendar", "start_date"); date_rule("V3c", "calendar", "end_date")
if has("stops"):
    la, lo = pd.to_numeric(T["stops"].stop_lat, errors="coerce"), pd.to_numeric(T["stops"].stop_lon, errors="coerce")
    rule("V4", "Validity", "stop coordinates are valid numbers", "error", len(la), (la.isna() | lo.isna() | la.abs().gt(90) | lo.abs().gt(180)).sum())
    rule("V5", "Validity", "stop lies inside Germany (lat 47-56, lon 5-16)", "warning", len(la), (~(la.between(47, 56) & lo.between(5, 16))).sum())
if has("routes"):
    rule("V6", "Validity", "route_type is a standard GTFS code", "error", len(T["routes"]), (~T["routes"].route_type.isin(list("0123456789") + ["11", "12"])).sum())

def filled(t, c):
    s = T[t][c]; return s.notna() & (s.str.strip() != "")
for rid, t, c, sev in [("C1", "routes", "route_short_name", "warning"), ("C2", "routes", "route_long_name", "warning"),
                       ("C3", "stops", "stop_name", "error"), ("C4", "agency", "agency_phone", "info")]:
    if has(t) and col(t, c): rule(rid, "Completeness", f"{t}.{c} is filled", sev, len(T[t]), (~filled(t, c)).sum())
empty = prof[(prof.filled_pct == 0) & (prof.rows > 0)]
rule("C5", "Completeness", "no column is completely empty", "warning", len(prof), len(empty))

if has("stop_times") and "arrival_time" in T["stop_times"].columns:
    st = T["stop_times"].copy()
    def secs(s):
        p = s.str.extract(r"^(\d+):(\d{2}):(\d{2})$").astype(float)
        return p[0] * 3600 + p[1] * 60 + p[2]
    st["arr"], st["dep"] = secs(st.arrival_time), secs(st.departure_time)
    st["seq"] = pd.to_numeric(st.stop_sequence, errors="coerce")
    st = st.sort_values(["trip_id", "seq"])
    rule("K1", "Consistency", "departure not before arrival", "error", len(st), (st.dep < st.arr).sum())
    same = st.trip_id.eq(st.trip_id.shift())
    back = same & (st.arr < st.dep.shift())
    rule("K2", "Consistency", "time never goes backwards within a trip", "error", same.sum(), back.sum())
    zero = same & (st.arr - st.dep.shift() == 0)
    rule("K3", "Consistency", "travel time to next stop is greater than 0 min", "warning", same.sum(), zero.sum())
    if "shape_dist_traveled" in st.columns:
        d = pd.to_numeric(st.shape_dist_traveled, errors="coerce")
        later = same & d.notna()
        rule("K4", "Consistency", "shape_dist_traveled is greater than 0 after the first stop", "warning", later.sum(), (later & (d == 0)).sum())
if has("routes") and col("routes", "route_short_name"):
    r = T["routes"].dropna(subset=["route_short_name"])
    d = r.groupby(["agency_id", "route_short_name"]).route_id.nunique()
    rule("K5", "Consistency", "route_short_name maps to one route_id per operator", "warning", len(d), (d > 1).sum())
for t, c in [("stops", "stop_name"), ("routes", "route_desc"), ("agency", "agency_name")]:
    if has(t) and col(t, c):
        s = T[t][c].dropna()
        rule(f"K6{t[0]}", "Consistency", f"{t}.{c} has no double or trailing spaces", "warning", len(s), (s != s.str.strip().str.replace(r"\s+", " ", regex=True)).sum())

ends = []
if has("calendar"): ends.append(pd.to_datetime(T["calendar"].end_date, format="%Y%m%d", errors="coerce"))
if has("calendar_dates"): ends.append(pd.to_datetime(T["calendar_dates"].date, format="%Y%m%d", errors="coerce"))
if "feed_info" in T and col("feed_info", "feed_end_date"):
    fe = pd.to_datetime(T["feed_info"].feed_end_date, format="%Y%m%d", errors="coerce").max()
    rule("T1", "Timeliness", f"feed is valid on {a.as_of}", "error", 1, int(fe < AS_OF))
if has("calendar"):
    e = pd.to_datetime(T["calendar"].end_date, format="%Y%m%d", errors="coerce")
    rule("T2", "Timeliness", f"service still running on {a.as_of}", "error", len(e), (e < AS_OF).sum())

rules = pd.DataFrame(R)
rules.to_csv(f"{a.out}/validator_rules_{a.label}.csv", index=False)
dims = (rules.groupby("dimension").agg(score_pct=("pass_rate", lambda s: round(100 * s.mean(), 1)),
        rules_passed=("status", lambda s: (s == "PASS").sum()), rules=("id", "count")).reset_index())
overall = dict(dimension="OVERALL", score_pct=round(dims.score_pct.mean(), 1), rules_passed=int(dims.rules_passed.sum()), rules=int(dims.rules.sum()))
dims = pd.concat([dims, pd.DataFrame([overall])], ignore_index=True)
dims.to_csv(f"{a.out}/quality_kpis_{a.label}.csv", index=False)

print("\n=== KPIs ===\n", dims.to_string(index=False))
print("\n=== Failed rules ===\n", rules[rules.status == "FAIL"][["id", "dimension", "rule", "severity", "checked", "failed"]].to_string(index=False))
print("\n=== Completely empty columns ===\n", empty[["table", "column"]].to_string(index=False))
print("\nWritten to", a.out)
