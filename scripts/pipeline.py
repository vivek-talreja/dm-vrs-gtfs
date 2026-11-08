"""End-to-end pipeline: raw GTFS folder -> clean tables + fact_stop_service.

  python scripts/pipeline.py --folder data/raw/vrs_gtfs/2026-11-07 --agency 6
  python scripts/pipeline.py --folder data/raw/vrs_gtfs/2026-11-07 --agency 8 --out-dir outputs/rsvg --clean-dir data/clean/rsvg
Nothing is hardcoded: operator, typical days and paths are parameters.
"""
import argparse, hashlib, os, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleaning_functions as cf

TYPICAL_DAYS = {"weekday": "2025-02-12", "saturday": "2025-02-15", "sunday": "2025-02-16"}


def run_pipeline(feed, agency_id=None, typical_days=None, source_file="", feed_version="", load_date="1970-01-01", log=None):
    """Pure function: same input -> same output. Returns (clean_feed, fact, removed)."""
    typical_days = typical_days or TYPICAL_DAYS
    if agency_id is not None:
        feed = cf.filter_agency(feed, agency_id, log)
    feed, removed = cf.drop_orphans(feed, log)
    feed["stops"] = cf.normalize_whitespace(feed["stops"], ["stop_name"])
    feed["routes"] = cf.normalize_whitespace(feed["routes"], [c for c in ("route_short_name", "route_desc") if c in feed["routes"].columns])
    feed, dropped = cf.drop_empty_columns(feed, log)
    feed = cf.parse_gtfs_dates(feed)
    feed = cf.drop_shape_dist(feed)
    st = cf.normalize_gtfs_time(feed["stop_times"])
    st = cf.add_time_band(cf.flag_zero_hop(st))
    feed["stop_times"] = st
    feed["routes"] = cf.add_line_key(feed["routes"])
    fact = cf.service_features(feed, typical_days)
    fact = cf.add_lineage(fact, source_file, feed_version, load_date)
    return feed, fact, removed


def fingerprint(df):
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", required=True)
    ap.add_argument("--agency", default=None)
    ap.add_argument("--out-dir", default="outputs")
    ap.add_argument("--clean-dir", default="data/clean")
    ap.add_argument("--weekday", default=TYPICAL_DAYS["weekday"]); ap.add_argument("--saturday", default=TYPICAL_DAYS["saturday"])
    ap.add_argument("--sunday", default=TYPICAL_DAYS["sunday"])
    a = ap.parse_args()
    days = {"weekday": a.weekday, "saturday": a.saturday, "sunday": a.sunday}
    os.makedirs(a.out_dir, exist_ok=True); os.makedirs(a.clean_dir, exist_ok=True)

    feed = cf.load_feed(a.folder)
    ver = feed["feed_info"].feed_version.iloc[0] if "feed_info" in feed and len(feed["feed_info"]) else ""
    log = []
    clean, fact, removed = run_pipeline(feed, a.agency, days, os.path.basename(os.path.normpath(a.folder)), ver,
                                        pd.Timestamp.today().date().isoformat(), log)

    # CL07 sanity check: are the typical days representative? Compare with the same weekday one week before/after.
    print("\nTrips running per typical day (date -7 / date / date +7):")
    for k, d in days.items():
        c = [int(clean["trips"].service_id.isin(cf.running_services(clean, pd.Timestamp(d) + pd.Timedelta(days=o))).sum()) for o in (-7, 0, 7)]
        print(f"  {k:9s} {d}: {c}")

    pd.DataFrame(log).to_csv(f"{a.out_dir}/cleaning_log.csv", index=False)
    fact.to_csv(f"{a.out_dir}/fact_stop_service.csv", index=False)
    clean["stops"].to_csv(f"{a.out_dir}/dim_stop.csv", index=False)
    clean["routes"].to_csv(f"{a.out_dir}/dim_route.csv", index=False)
    for t in ("stop_times", "trips"):
        clean[t].to_csv(f"{a.clean_dir}/{t}.csv", index=False)
    print("\nremoved orphans:", {k: len(v) for k, v in removed.items()})
    print("fact rows:", len(fact), "| fingerprint:", fingerprint(fact.drop(columns="load_date")))
    print(pd.DataFrame(log)[["step", "table", "rows_before", "rows_after"]].to_string(index=False))


if __name__ == "__main__":
    main()
