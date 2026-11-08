"""Cleaning functions for a GTFS feed (one function per step of docs/cleaning_spec.csv).

A feed is a dict {table_name: DataFrame}, every column read as text (see load_feed).
Functions never modify their input; they return new objects. Pass a list as `log`
to collect rows before/after for the cleaning log.
"""
import os
import numpy as np
import pandas as pd

# Time bands on the SERVICE day: hours can run past 24 (trips after midnight).
# (label, start_hour, end_hour). The last band's end is an assumption (04:00 next day).
BANDS = [("00-06", 0, 6), ("06-09", 6, 9), ("09-16", 9, 16),
         ("16-19", 16, 19), ("19-22", 19, 22), ("22+", 22, 28)]
EVENING_BANDS = ["19-22", "22+"]
WEEKDAY_COLUMNS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
# Columns later steps need: never dropped even if empty.
PROTECTED = {"stops": ["stop_id", "stop_name"], "stop_times": ["trip_id", "stop_id", "stop_sequence",
             "arrival_time", "departure_time"], "trips": ["trip_id", "route_id", "service_id"],
             "routes": ["route_id", "agency_id"]}


def _log(log, step, table, before, after, note=""):
    if log is not None:
        log.append(dict(step=step, table=table, rows_before=before, rows_after=after, note=note))


def _to_date(s):
    """Accept YYYYMMDD text or an already parsed datetime column."""
    return s if pd.api.types.is_datetime64_any_dtype(s) else pd.to_datetime(s, format="%Y%m%d", errors="coerce")


# CL01 -----------------------------------------------------------------------
def load_feed(folder):
    feed = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".txt"):
            p = os.path.join(folder, f)
            try:
                feed[f[:-4]] = pd.read_csv(p, dtype=str, encoding="utf-8-sig")
            except Exception:
                feed[f[:-4]] = pd.read_csv(p, dtype=str, encoding="utf-8-sig", engine="python", on_bad_lines="skip")
    return feed


# CL02 -----------------------------------------------------------------------
def filter_agency(feed, agency_id, log=None):
    """Keep one operator: its routes, trips, stop_times, the stops they serve, and their services."""
    agency_id = str(agency_id)
    out = dict(feed)
    routes = feed["routes"][feed["routes"].agency_id == agency_id]
    trips = feed["trips"][feed["trips"].route_id.isin(routes.route_id)]
    st = feed["stop_times"][feed["stop_times"].trip_id.isin(trips.trip_id)]
    out.update(routes=routes, trips=trips, stop_times=st,
               stops=feed["stops"][feed["stops"].stop_id.isin(st.stop_id)])
    if "agency" in feed:
        out["agency"] = feed["agency"][feed["agency"].agency_id == agency_id]
    for t in ("calendar", "calendar_dates"):
        if t in feed:
            out[t] = feed[t][feed[t].service_id.isin(trips.service_id)]
    for t in ("routes", "trips", "stop_times", "stops"):
        _log(log, "CL02 filter_agency", t, len(feed[t]), len(out[t]), f"agency_id={agency_id}")
    return out


# CL03 -----------------------------------------------------------------------
def drop_empty_columns(feed, log=None):
    """Drop columns that are 100% empty (except those later steps need). Returns (feed, dropped)."""
    out, dropped = {}, []
    for t, df in feed.items():
        if len(df) == 0:
            out[t] = df
            continue
        empty = [c for c in df.columns if (df[c].isna() | (df[c].astype(str).str.strip() == "")).all()
                 and c not in PROTECTED.get(t, [])]
        out[t] = df.drop(columns=empty)
        dropped += [(t, c) for c in empty]
    _log(log, "CL03 drop_empty_columns", "all", None, None, f"{len(dropped)} columns dropped: " + "; ".join(f"{t}.{c}" for t, c in dropped))
    return out, dropped


# CL04 -----------------------------------------------------------------------
def drop_orphans(feed, log=None):
    """Remove trips with fewer than 2 stop_times, their stop_times, then stops no trip serves.
    Returns (feed, removed) where removed lists the IDs so the log can show what went."""
    out = dict(feed)
    n = feed["stop_times"].trip_id.value_counts()
    cnt = feed["trips"].trip_id.map(n).fillna(0)
    bad_trips = feed["trips"].loc[cnt < 2, "trip_id"]
    out["trips"] = feed["trips"][~feed["trips"].trip_id.isin(bad_trips)]
    out["stop_times"] = feed["stop_times"][~feed["stop_times"].trip_id.isin(bad_trips)]
    unused = feed["stops"].loc[~feed["stops"].stop_id.isin(out["stop_times"].stop_id), "stop_id"]
    out["stops"] = feed["stops"][~feed["stops"].stop_id.isin(unused)]
    removed = {"trips": sorted(bad_trips), "stops": sorted(unused)}
    for t in ("trips", "stop_times", "stops"):
        _log(log, "CL04 drop_orphans", t, len(feed[t]), len(out[t]),
             f"removed ids: {removed['trips'] if t != 'stops' else removed['stops']}"[:300])
    return out, removed


# CL05 -----------------------------------------------------------------------
def parse_gtfs_dates(feed):
    out = dict(feed)
    if "calendar" in feed:
        c = feed["calendar"].copy()
        c["start_date"], c["end_date"] = _to_date(c.start_date), _to_date(c.end_date)
        out["calendar"] = c
    if "calendar_dates" in feed:
        d = feed["calendar_dates"].copy()
        d["date"] = _to_date(d["date"])
        out["calendar_dates"] = d
    return out


# CL06 -----------------------------------------------------------------------
def _seconds(s):
    p = s.astype("string").str.extract(r"^\s*(\d+):(\d{2}):(\d{2})\s*$").astype(float)
    return p[0] * 3600 + p[1] * 60 + p[2]


def normalize_gtfs_time(st):
    """Add arr_seconds, dep_seconds (seconds since the service day began, may exceed 86400),
    day_offset (0 same day, 1 after midnight) and departure_clock (HH:MM:SS below 24h).
    The original strings stay untouched."""
    st = st.copy()
    st["arr_seconds"], st["dep_seconds"] = _seconds(st["arrival_time"]), _seconds(st["departure_time"])
    st["day_offset"] = (st["dep_seconds"] // 86400).astype("Int64")
    c = st["dep_seconds"] % 86400
    z = lambda x: x.fillna(0).astype(int).astype(str).str.zfill(2)
    clock = z(c // 3600) + ":" + z((c % 3600) // 60) + ":" + z(c % 60)
    st["departure_clock"] = clock.where(st["dep_seconds"].notna())
    return st


# CL07 -----------------------------------------------------------------------
def running_services(feed, date):
    """service_ids running on `date`: weekly pattern inside its validity, minus removals (type 2), plus additions (type 1)."""
    d = pd.Timestamp(date)
    cal = feed["calendar"]
    ok = (cal[d.day_name().lower()] == "1") & (_to_date(cal.start_date) <= d) & (_to_date(cal.end_date) >= d)
    running = set(cal.loc[ok, "service_id"])
    if "calendar_dates" in feed:
        cd = feed["calendar_dates"]
        on = cd[_to_date(cd["date"]) == d]
        running -= set(on.loc[on.exception_type == "2", "service_id"])
        running |= set(on.loc[on.exception_type == "1", "service_id"])
    return running


# CL08 -----------------------------------------------------------------------
def flag_zero_hop(st):
    """Flag (never delete) stops reached in 0 minutes after the previous stop of the same trip.
    Needs arr_seconds/dep_seconds from normalize_gtfs_time. Returns rows sorted by trip and sequence."""
    st = st.copy()
    st["_seq"] = pd.to_numeric(st["stop_sequence"], errors="coerce")
    st = st.sort_values(["trip_id", "_seq"], kind="mergesort").reset_index(drop=True)
    same = st.trip_id.eq(st.trip_id.shift())
    st["is_zero_hop"] = same & ((st.arr_seconds - st.dep_seconds.shift()) == 0)
    return st.drop(columns="_seq")


# CL09 / CL10 / CL11 ---------------------------------------------------------
def add_line_key(routes):
    """One line = one key, even if VRS split it over several route_ids."""
    r = routes.copy()
    name = r["route_short_name"].where(r["route_short_name"].notna() & (r["route_short_name"].str.strip() != ""), r["route_id"])
    r["line_key"] = r["agency_id"] + "_" + name
    return r


def normalize_whitespace(df, columns=None):
    df = df.copy()
    for c in (columns or [c for c in df.columns if df[c].dtype == object]):
        df[c] = df[c].str.strip().str.replace(r"\s+", " ", regex=True)
    return df


def drop_shape_dist(feed):
    out = dict(feed)
    for t in ("stop_times", "shapes"):
        if t in out and "shape_dist_traveled" in out[t].columns:
            out[t] = out[t].drop(columns="shape_dist_traveled")
    return out


# CL12 -----------------------------------------------------------------------
def add_time_band(st):
    edges = [b[1] for b in BANDS] + [np.inf]
    st = st.copy()
    st["time_band"] = pd.cut(st["dep_seconds"] / 3600, bins=edges, right=False, labels=[b[0] for b in BANDS]).astype(object)
    return st


def service_features(feed, typical_days):
    """One row per stop x day type x time band. `typical_days` = {"weekday": "2025-02-12", ...}.
    Expects stop_times with dep_seconds and time_band. Counts boardings only (pickup_type != 1).
    longest_gap_min = longest stretch inside the band without a departure (includes the time from the
    band start to the first departure and from the last departure to the band end)."""
    frames = []
    for day_type, date in typical_days.items():
        run = running_services(feed, date)
        trips = feed["trips"].loc[feed["trips"].service_id.isin(run), ["trip_id"]]
        ev = feed["stop_times"].merge(trips, on="trip_id")
        if "pickup_type" in ev.columns:
            ev = ev[ev["pickup_type"].fillna("0") != "1"]
        ev = ev[["stop_id", "dep_seconds", "time_band"]].dropna().copy()
        ev["day_type"] = day_type
        frames.append(ev)
    ev = pd.concat(frames, ignore_index=True)
    info = {b[0]: (b[1] * 3600, b[2] * 3600) for b in BANDS}
    keys = ["stop_id", "day_type", "time_band"]
    ev = ev.sort_values(keys + ["dep_seconds"], kind="mergesort")
    g = ev.groupby(keys)
    start = ev.time_band.map(lambda b: info[b][0])
    end = ev.time_band.map(lambda b: info[b][1])
    gap_before = (ev.dep_seconds - g.dep_seconds.shift().fillna(start)).where(g.cumcount() > 0, ev.dep_seconds - start)
    agg = pd.DataFrame({"departures": g.size(), "_maxgap": gap_before.groupby([ev[k] for k in keys]).max(),
                        "_last": g.dep_seconds.max()}).reset_index()
    agg["_end"] = agg.time_band.map(lambda b: info[b][1])
    agg["longest_gap_min"] = np.maximum(agg["_maxgap"], (agg["_end"] - agg["_last"]).clip(lower=0)) / 60
    grid = pd.MultiIndex.from_product([feed["stops"]["stop_id"], list(typical_days), [b[0] for b in BANDS]], names=keys).to_frame(index=False)
    fact = grid.merge(agg[keys + ["departures", "longest_gap_min"]], on=keys, how="left")
    fact["band_hours"] = fact.time_band.map({b[0]: b[2] - b[1] for b in BANDS})
    fact["departures"] = fact["departures"].fillna(0).astype(int)
    fact["longest_gap_min"] = fact["longest_gap_min"].fillna(fact["band_hours"] * 60)
    fact["departures_per_hour"] = fact["departures"] / fact["band_hours"]
    fact["avg_headway_min"] = (fact["band_hours"] * 60 / fact["departures"]).where(fact["departures"] > 0)
    fact["is_evening"] = fact.time_band.isin(EVENING_BANDS)
    fact["service_date"] = fact.day_type.map({k: pd.Timestamp(v).date().isoformat() for k, v in typical_days.items()})
    fact = fact.merge(feed["stops"][["stop_id", "stop_name"]], on="stop_id", how="left")
    cols = ["stop_id", "stop_name", "day_type", "service_date", "time_band", "band_hours", "is_evening",
            "departures", "departures_per_hour", "avg_headway_min", "longest_gap_min"]
    return fact[cols].sort_values(["stop_id", "day_type", "time_band"]).reset_index(drop=True)


# CL13 -----------------------------------------------------------------------
def add_lineage(df, source_file, feed_version, load_date=None):
    df = df.copy()
    df["source_file"], df["feed_version"] = source_file, feed_version
    df["load_date"] = load_date or pd.Timestamp.today().date().isoformat()
    return df
