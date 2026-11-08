import os, sys
import pandas as pd
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import cleaning_functions as cf
from pipeline import run_pipeline, fingerprint


def mini_feed():
    """Tiny made-up feed: 2 operators, orphans, an after-midnight trip, a zero-minute hop."""
    return {
        "agency": pd.DataFrame({"agency_id": ["1", "2"], "agency_name": ["A", "B"]}),
        "routes": pd.DataFrame({"route_id": ["r1", "r2"], "agency_id": ["1", "2"],
                                "route_short_name": ["10", "20"], "route_desc": ["x  y ", "z"], "route_url": [None, None]}),
        "trips": pd.DataFrame({"trip_id": ["t1", "t2", "t3", "t4", "t5"], "route_id": ["r1", "r1", "r1", "r1", "r2"],
                               "service_id": ["WK", "WK", "WK", "WK", "WK"]}),
        "stops": pd.DataFrame({"stop_id": ["s1", "s2", "s3", "s4", "s5"],
                               "stop_name": ["Alpha", "Beta  ", "Gamma", "Delta", "Eps"], "stop_code": [None] * 5}),
        "stop_times": pd.DataFrame({
            "trip_id": ["t1", "t1", "t3", "t4", "t4", "t5", "t5"],
            "stop_id": ["s1", "s2", "s4", "s1", "s2", "s5", "s5"],
            "stop_sequence": ["1", "2", "1", "1", "2", "1", "2"],
            "arrival_time": ["19:10:00", "19:10:00", "08:00:00", "25:10:00", "25:20:00", "07:00:00", "07:05:00"],
            "departure_time": ["19:10:00", "19:12:00", "08:00:00", "25:10:00", "25:20:00", "07:00:00", "07:05:00"],
            "pickup_type": ["0"] * 7, "shape_dist_traveled": ["0", "1.5", "0", "0", "2", "0", "1"]}),
        "calendar": pd.DataFrame({"service_id": ["WK"], "monday": ["1"], "tuesday": ["1"], "wednesday": ["1"], "thursday": ["1"],
                                  "friday": ["1"], "saturday": ["0"], "sunday": ["0"], "start_date": ["20250101"], "end_date": ["20250331"]}),
        "calendar_dates": pd.DataFrame({"service_id": ["WK"], "date": ["20250219"], "exception_type": ["2"]}),
        "feed_info": pd.DataFrame({"feed_version": ["V1"]}),
    }
DAYS = {"weekday": "2025-02-12"}


def test_drop_orphans_removes_exactly_the_orphans():
    feed, removed = cf.drop_orphans(mini_feed())
    assert removed["trips"] == ["t2", "t3"]            # t2 has no stop_times, t3 only one
    assert set(feed["trips"].trip_id) == {"t1", "t4", "t5"}
    assert "s3" not in set(feed["stops"].stop_id) and "s4" not in set(feed["stops"].stop_id)   # unused / only served by t3
    assert set(feed["stop_times"].trip_id) == {"t1", "t4", "t5"}


def test_drop_orphans_leaves_clean_data_alone():
    feed, _ = cf.drop_orphans(mini_feed())
    again, removed = cf.drop_orphans(feed)
    assert removed == {"trips": [], "stops": []}
    assert all(len(again[t]) == len(feed[t]) for t in ("trips", "stops", "stop_times"))


def test_filter_agency_keeps_only_that_operator():
    f = cf.filter_agency(mini_feed(), 2)
    assert set(f["routes"].route_id) == {"r2"} and set(f["trips"].trip_id) == {"t5"} and set(f["stops"].stop_id) == {"s5"}


def test_drop_empty_columns_keeps_protected():
    feed, dropped = cf.drop_empty_columns(mini_feed())
    assert ("stops", "stop_code") in dropped and ("routes", "route_url") in dropped
    assert "stop_name" in feed["stops"].columns


def test_normalize_time_after_midnight():
    st = cf.normalize_gtfs_time(mini_feed()["stop_times"])
    row = st[(st.trip_id == "t4") & (st.stop_sequence == "1")].iloc[0]
    assert row.dep_seconds == 25 * 3600 + 600 and row.day_offset == 1 and row.departure_clock == "01:10:00"
    assert row.departure_time == "25:10:00"            # original kept
    assert st[st.trip_id == "t3"].iloc[0].day_offset == 0


def test_parse_dates_become_real_dates():
    f = cf.parse_gtfs_dates(mini_feed())
    assert pd.api.types.is_datetime64_any_dtype(f["calendar"].start_date) and f["calendar_dates"].date.iloc[0] == pd.Timestamp("2025-02-19")


def test_running_services_pattern_minus_removals_plus_additions():
    f = mini_feed()
    assert cf.running_services(f, "2025-02-12") == {"WK"}      # Wednesday
    assert cf.running_services(f, "2025-02-19") == set()       # removed that day
    assert cf.running_services(f, "2025-02-16") == set()       # Sunday, not in pattern
    f["calendar_dates"] = pd.concat([f["calendar_dates"], pd.DataFrame({"service_id": ["EXTRA"], "date": ["20250216"], "exception_type": ["1"]})])
    assert cf.running_services(f, "2025-02-16") == {"EXTRA"}   # added day
    assert cf.running_services(cf.parse_gtfs_dates(f), "2025-02-12") == {"WK"}   # also works on parsed dates


def test_flag_zero_hop_flags_but_never_deletes():
    st = cf.flag_zero_hop(cf.normalize_gtfs_time(mini_feed()["stop_times"]))
    assert len(st) == 7
    flagged = st[st.is_zero_hop]
    assert list(flagged.trip_id) == ["t1"]             # t1: arrives 19:10 at stop 2, previous stop departed 19:10


def test_time_band_boundaries():
    st = pd.DataFrame({"dep_seconds": [0, 5.99 * 3600, 6 * 3600, 18.99 * 3600, 19 * 3600, 22 * 3600, 25 * 3600]})
    assert list(cf.add_time_band(st).time_band) == ["00-06", "00-06", "06-09", "16-19", "19-22", "22+", "22+"]


def test_service_features_longest_gap_and_zero_stops():
    feed, _ = cf.drop_orphans(mini_feed())
    st = cf.add_time_band(cf.flag_zero_hop(cf.normalize_gtfs_time(feed["stop_times"])))
    feed["stop_times"] = st
    fact = cf.service_features(feed, DAYS)
    ev = fact[(fact.stop_id == "s1") & (fact.time_band == "19-22")].iloc[0]
    assert ev.departures == 1 and ev.longest_gap_min == pytest.approx(170)     # 19:00 -> 19:10 = 10, 19:10 -> 22:00 = 170
    none = fact[(fact.stop_id == "s5") & (fact.time_band == "19-22")].iloc[0]
    assert none.departures == 0 and none.longest_gap_min == 180               # no service at all in the band
    assert len(fact) == 3 * 1 * 6                                             # 3 stops (s1,s2,s5) x 1 day type x 6 bands
    late = fact[(fact.stop_id == "s1") & (fact.time_band == "22+")].iloc[0]
    assert late.departures == 1                                               # the 25:10 departure belongs to the late-night band


def test_pipeline_is_reproducible_and_order_independent():
    f1 = mini_feed()
    f2 = {k: v.sample(frac=1, random_state=3).reset_index(drop=True) for k, v in mini_feed().items()}
    _, fact1, _ = run_pipeline(f1, None, DAYS, "x", "V1")
    _, fact2, _ = run_pipeline(f2, None, DAYS, "x", "V1")
    _, fact3, _ = run_pipeline(mini_feed(), None, DAYS, "x", "V1")
    assert fingerprint(fact1) == fingerprint(fact2) == fingerprint(fact3)


def test_pipeline_runs_for_a_second_operator():
    _, fact, _ = run_pipeline(mini_feed(), 2, DAYS, "x", "V1")
    assert set(fact.stop_id) == {"s5"}                       # only operator 2's stop
    assert fact.departures.sum() == 2                        # t5 departs 07:00 and 07:05
    assert fact[fact.time_band == "06-09"].departures.iloc[0] == 2
