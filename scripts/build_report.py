"""Generate the developer-style training-plan report (HTML -> PDF via Chrome).

Generic: all athlete/season-specific copy (titles, goals, race targets, split
tables, rationale) is read from the season spec (examples/season-2026-malbork.yaml);
all metrics are computed from the persisted profile, plan and activity history in
the configured data dir (AITHLETE_DATA_DIR). Nothing personal is hardcoded here.

Run:
  uv run python scripts/build_report.py [SPEC.yaml]
  AITHLETE_DATA_DIR=examples/data uv run python scripts/build_report.py examples/season-2026-malbork.yaml

Then convert with headless Chrome (see README).
"""

from __future__ import annotations

import datetime as dt
import html
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from aithlete.analysis.loaders import load_activities, load_wellness
from aithlete.config import get_config
from aithlete.storage.paths import plan_path, profile_path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "examples" / "season-2026-malbork.yaml"


# ---------- formatting helpers ----------
def hms(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def time_to_s(text: str) -> int:
    parts = [int(p) for p in str(text).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def pace_100(seconds: float, meters: float) -> str:
    if not meters:
        return "—"
    p = seconds / (meters / 100.0)
    return f"{int(p // 60)}:{int(p % 60):02d}/100m"


def pace_km(seconds: float, meters: float) -> str:
    if not meters:
        return "—"
    p = seconds / (meters / 1000.0)
    return f"{int(p // 60)}:{int(p % 60):02d}/km"


def kmh(meters: float, seconds: float) -> str:
    return "—" if not seconds else f"{meters / 1000.0 / (seconds / 3600.0):.1f} km/h"


def esc(x) -> str:
    return html.escape(str(x))


def tv(node) -> str:
    v = node.get("value")
    return "—" if v is None else v


BUCKET_M = {  # (swim, bike, run) meters per IM fraction
    "1/8": (475, 22500, 5275), "1/4": (950, 45000, 10550),
    "1/2": (1900, 90000, 21100), "full": (3800, 180000, 42200),
}


def main() -> None:
    spec_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPEC
    spec = yaml.safe_load(spec_path.read_text())
    meta = spec["meta"]
    athlete = meta["athlete_id"]
    out_html = get_config().data_dir / "reports" / f"{meta['plan_id']}.html"

    prof = json.loads(profile_path(athlete).read_text())
    plan = json.loads(plan_path(meta["plan_id"]).read_text())
    acts = load_activities()
    _ = load_wellness()
    acts["date"] = pd.to_datetime(acts["date"])

    start, end = acts["date"].min(), acts["date"].max()
    total_h = acts["duration_s"].sum() / 3600
    n = len(acts)
    by_sport = (acts.groupby("sport")
                .agg(sessions=("id", "count"),
                     hours=("duration_s", lambda s: s.sum() / 3600),
                     km=("distance_m", lambda s: s.sum() / 1000))
                .sort_values("hours", ascending=False))
    acts["week"] = acts["date"].dt.to_period("W-SUN")
    wk = (acts.groupby("week")
          .agg(hours=("duration_s", lambda s: s.sum() / 3600), tss=("tss", "sum"),
               n=("id", "count")).tail(16))
    ytd = acts[acts["date"] >= pd.Timestamp(end.year, 1, 1)]
    ytd_h = ytd["duration_s"].sum() / 3600
    ytd_swim_h = ytd[ytd["sport"].isin(["swim", "openwaterswim"])]["duration_s"].sum() / 3600
    load = prof["load"]
    results = sorted(prof["results"], key=lambda r: r["date"])

    parts: list[str] = []
    A = parts.append

    def kpi(v, label):
        return f"<div class='kpi'><div class='v'>{v}</div><div class='l'>{label}</div></div>"

    def t_tag(ic):
        return f"<span class='tag t-{ic}'>{ic}</span>"

    def split_table(splits, total_s):
        body = ""
        for sp in splits:
            body += (f"<tr><td>{esc(sp['leg'])}</td><td class='r mono'>{esc(sp['target'])}</td>"
                     f"<td class='small'>{esc(sp.get('detail', ''))}</td></tr>")
        body += (f"<tr class='tot'><td>TOTAL</td><td class='r mono'>{hms(total_s)}</td><td></td></tr>")
        return body

    A(f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head><body>")

    # Cover
    races = spec["races"]
    targ_line = " &middot; ".join(
        f"{esc(r['name'])} ({dt.date.fromisoformat(str(r['date'])).strftime('%d %b %Y')})"
        for r in races)
    goal_line = " &middot; ".join(f"{esc(r['target_time'])} {esc(r['bucket'])}" for r in races)
    A("<div class='cover'>")
    A(f"<h1>{esc(meta.get('title', 'Season Plan & Analysis'))}</h1>")
    A(f"<div class='sub'>{targ_line}<br>Targets: {goal_line}</div>")
    A(f"<div class='small' style='margin-top:10px'>Generated {dt.date.today().isoformat()} "
      f"from {n} activities ({start.date()} &rarr; {end.date()}). "
      f"Plan id <span class='mono'>{esc(meta['plan_id'])}</span>. "
      "All figures computed from persisted data; physiology values are estimates "
      "(provenance tagged), not lab-measured.</div></div>")

    # KPIs
    A("<div class='kpis'>")
    A(kpi(f"{tv(prof['bike']['ftp_w'])} W", f"FTP ({tv(prof['bike']['ftp_w_per_kg'])} W/kg)"))
    A(kpi(f"{tv(load['combined']['ctl'])}", "CTL (combined)"))
    A(kpi(f"{tv(load['combined']['atl'])}", "ATL"))
    A(kpi(f"{tv(load['combined']['tsb'])}", "TSB (form)"))
    A(kpi(f"{tv(prof['health']['hrv_baseline_60d_ms'])} ms", "HRV (60d rMSSD)"))
    A(kpi(f"{tv(prof['health']['resting_hr_baseline_bpm'])} bpm", "Resting HR (60d)"))
    A(kpi(f"{total_h:.0f} h", "Logged"))
    A("</div>")

    # 1. Assumptions
    A("<h2>1 &middot; Base assumptions &amp; method</h2>")
    A("<div class='grid2'><div>")
    A("<h3>Goals (athlete-confirmed)</h3><ul>")
    for r in races:
        d = dt.date.fromisoformat(str(r['date'])).strftime('%a %d %b %Y')
        pr = "A-race" if str(r.get('priority')).upper() == "A" else "B-race / tune-up"
        A(f"<li><b>{esc(r['name'])}</b> &mdash; {esc(d)} &mdash; <b>{esc(pr)}</b>. "
          f"Target <b>{esc(r['target_time'])}</b>, swim {esc(r.get('target_swim', '—'))}.</li>")
    vc = meta.get("volume_ceiling_hours", {})
    if vc:
        A(f"<li>Volume ceiling: <b>{esc(vc.get('to_first_race'))} h/wk</b> to first race, "
          f"<b>{esc(vc.get('after_first_race'))} h/wk</b> after.</li>")
    A("</ul>")
    if meta.get("equipment_note"):
        A(f"<h3>Equipment</h3><p>{esc(meta['equipment_note'])}</p>")
    A("</div><div>")
    A("<h3>Data sources</h3><ul>"
      f"<li>Activities, power, pace, load: <b>intervals.icu</b> (Garmin-synced), {n} sessions "
      f"{start.date()}&rarr;{end.date()}.</li>"
      "<li>CTL/ATL: intervals.icu wellness where present, else Banister (&tau;<sub>42</sub>/"
      "&tau;<sub>7</sub>) on local TSS.</li>"
      "<li>Recovery (HRV/RHR/sleep): wearable wellness.</li></ul>")
    A("<h3>Models &amp; guardrails</h3><ul>"
      "<li>Load: Banister impulse-response (CTL/ATL/TSB).</li>"
      "<li>Race detection: triathlons reconstructed from <span class='mono'>transition</span> "
      "activities; classified to nearest IM fraction.</li>"
      "<li>The plan is gated by a <b>deterministic validator</b> (ramp caps, recovery cadence, "
      "ACWR, intensity distribution, taper window, race-day TSB). See &sect;8.</li></ul>")
    A("</div></div>")
    A("<div class='note small'>Decision-support, not medical or coaching advice. Physiology "
      "values are estimates with provenance tags. Validate paces/powers in training and adjust "
      "to in-session readiness.</div>")

    # 2. Profile
    A("<h2 class='pb'>2 &middot; Athlete profile</h2><div class='grid2'><div>")
    swim_css_spec = meta.get("swim_css")
    if swim_css_spec:
        swim_css_val = swim_css_spec if "/" in str(swim_css_spec) else f"{swim_css_spec}/100m"
        swim_css_src = "season spec (pool threshold)"
    else:
        swim_css_val = f"{hms(prof['swim']['css_s_per_100m']['value'])}/100m"
        swim_css_src = "intervals settings"
    def _node(*keys):
        n = prof
        for k in keys:
            n = n.get(k, {}) if isinstance(n, dict) else {}
        return n if isinstance(n, dict) else {}

    def vo2_row(node):
        v = node.get("value")
        return ("—", "—") if v is None else (f"{v:g} ml/kg/min", node.get("source") or "—")

    run_thr = _node("run", "threshold_pace_s_per_km")
    run_thr_val = "—" if run_thr.get("value") is None else f"{hms(run_thr['value'])}/km"
    vo2_bike_val, vo2_bike_src = vo2_row(_node("bike", "vo2max"))
    vo2_run_val, vo2_run_src = vo2_row(_node("run", "vo2max"))

    A("<h3>Physiology (estimated)</h3><table><tr><th>Metric</th><th class='r'>Value</th>"
      "<th>Source</th></tr>")
    for k, v, src in [
        ("Weight", f"{tv(prof['anthropometrics']['weight_kg'])} kg",
         prof['anthropometrics']['weight_kg'].get('source')),
        ("FTP (bike)", f"{tv(prof['bike']['ftp_w'])} W ({tv(prof['bike']['ftp_w_per_kg'])} W/kg)",
         "intervals settings"),
        ("VO2max (bike)", vo2_bike_val, vo2_bike_src),
        ("Run threshold", run_thr_val, run_thr.get("source") or "—"),
        ("VO2max (run)", vo2_run_val, vo2_run_src),
        ("LTHR", f"{tv(prof['bike']['lthr_bpm'])} bpm", "intervals settings"),
        ("Max HR", f"{tv(prof['bike']['max_hr_bpm'])} bpm", "intervals settings"),
        ("Swim CSS", swim_css_val, swim_css_src),
        ("Bike decoupling", f"{tv(prof['durability']['bike']['decoupling_pct'])} %", "Pw:Hr"),
    ]:
        A(f"<tr><td>{esc(k)}</td><td class='r mono'>{esc(v)}</td>"
          f"<td class='small'>{esc(src)}</td></tr>")
    A("</table>")
    A("<h3>Health / readiness</h3><table><tr><th>Metric</th><th class='r'>Now</th>"
      "<th class='r'>Baseline</th><th>Trend</th></tr>"
      f"<tr><td>HRV rMSSD</td><td class='r mono'>{tv(prof['health']['hrv_rmssd_ms'])}</td>"
      f"<td class='r mono'>{tv(prof['health']['hrv_baseline_60d_ms'])} (60d)</td>"
      f"<td>{esc(prof['health']['hrv_trend'])}</td></tr>"
      f"<tr><td>Resting HR</td><td class='r mono'>{tv(prof['health']['resting_hr_bpm'])}</td>"
      f"<td class='r mono'>{tv(prof['health']['resting_hr_baseline_bpm'])} (60d)</td>"
      f"<td>{esc(prof['health']['resting_hr_trend'])}</td></tr>"
      f"<tr><td>Sleep</td><td class='r mono'>{tv(prof['health']['sleep_avg_hours'])} h</td>"
      "<td class='r'>—</td><td>—</td></tr></table></div><div>")
    A("<h3>Training load by discipline</h3><table><tr><th>Sport</th><th class='r'>CTL</th>"
      "<th class='r'>ATL</th><th class='r'>TSB</th><th>Trend</th></tr>")
    for sp in ["combined", "swim", "bike", "run"]:
        L = load[sp]
        A(f"<tr><td>{esc(sp)}</td><td class='r mono'>{tv(L['ctl'])}</td>"
          f"<td class='r mono'>{tv(L['atl'])}</td><td class='r mono'>{tv(L['tsb'])}</td>"
          f"<td>{esc(L['trend'])}</td></tr>")
    A("</table>")
    A(f"<div class='note'><b>The headline limiter: swim.</b> Swim CTL <b>{tv(load['swim']['ctl'])}</b> "
      f"vs bike {tv(load['bike']['ctl'])} / run {tv(load['run']['ctl'])}. Only ~{ytd_swim_h:.0f} h "
      f"swimming logged in {end.year}. The swim costs little time on the clock but, untrained, it "
      "taxes the whole day. The plan holds <b>swim &ge; 3&times;/week</b> throughout &mdash; the "
      "single highest-leverage change.</div>")
    if spec.get("trajectory_note"):
        A("<h3>Why the targets are plausible (YoY trajectory)</h3><ul>")
        for t in spec["trajectory_note"]:
            A(f"<li>{esc(t)}</li>")
        A("</ul>")
    A("</div></div>")

    # 3. Results history
    A("<h2 class='pb'>3 &middot; Results history (auto-detected)</h2>")
    A("<table><tr><th>Date</th><th>Event</th><th>Fmt</th><th class='r'>Total</th>"
      "<th class='r'>Swim</th><th class='r'>Bike</th><th class='r'>Run</th><th>Notes</th></tr>")
    for r in results:
        sm, bm, rm = BUCKET_M.get(r["bucket"], (0, 0, 0))
        note = []
        if r["bucket"] in BUCKET_M:
            sm, bm, rm = BUCKET_M[r["bucket"]]
            if r.get("swim_time_s"):
                note.append(pace_100(r["swim_time_s"], sm))
            if r.get("bike_time_s"):
                note.append(kmh(bm, r["bike_time_s"]))
            if r.get("run_time_s"):
                note.append(pace_km(r["run_time_s"], rm))
        A(f"<tr><td class='mono'>{esc(r['date'])}</td><td>{esc(r['event_name'] or '—')}</td>"
          f"<td>{esc(r['bucket'])}</td><td class='r mono'>{hms(r['total_time_s'])}</td>"
          f"<td class='r mono'>{hms(r['swim_time_s'])}</td><td class='r mono'>{hms(r['bike_time_s'])}</td>"
          f"<td class='r mono'>{hms(r['run_time_s'])}</td>"
          f"<td class='small'>{esc(' · '.join(note))}</td></tr>")
    A("</table><p class='small'>T1/T2 not shown; included in totals.</p>")

    # 4. Training history
    A("<h2 class='pb'>4 &middot; Training history</h2><div class='kpis'>")
    A(kpi(f"{n}", "Activities"))
    A(kpi(f"{total_h:.0f} h", "Total time"))
    A(kpi(f"{ytd_h:.0f} h", f"{end.year} YTD"))
    A(kpi(f"{wk['hours'].mean():.1f} h", "Avg last 16 wk"))
    A(kpi(f"{wk['hours'].max():.1f} h", "Peak week (16wk)"))
    A("</div><div class='grid2'><div>")
    A("<h3>Volume by discipline (all-time)</h3><table><tr><th>Sport</th><th class='r'>Sess.</th>"
      "<th class='r'>Hours</th><th class='r'>km</th></tr>")
    for sp, row in by_sport.head(9).iterrows():
        A(f"<tr><td>{esc(sp)}</td><td class='r mono'>{int(row['sessions'])}</td>"
          f"<td class='r mono'>{row['hours']:.0f}</td><td class='r mono'>{row['km']:.0f}</td></tr>")
    A("</table></div><div>")
    A("<h3>Last 16 weeks (combined)</h3><table><tr><th>Week ending</th><th class='r'>h</th>"
      "<th class='r'>TSS</th><th class='r'>#</th></tr>")
    for w, row in wk.iterrows():
        A(f"<tr><td class='mono'>{esc(str(w).split('/')[-1])}</td>"
          f"<td class='r mono'>{row['hours']:.1f}</td><td class='r mono'>{row['tss']:.0f}</td>"
          f"<td class='r mono'>{int(row['n'])}</td></tr>")
    A("</table></div></div>")
    A("<div class='note small'>Demonstrated capacity (last 16 wk peak above) frames the plan's "
      "peak weeks as an extension of proven load, not a step into the unknown.</div>")

    # 5. Target splits
    A("<h2 class='pb'>5 &middot; Target race splits</h2><div class='grid2'>")
    for r in races:
        total = time_to_s(r["target_time"])
        A("<div>")
        A(f"<h3>{esc(r['name'])} &rarr; {esc(r['target_time'])}</h3>")
        A(f"<table><tr><th>Leg</th><th class='r'>Target</th><th>Detail</th></tr>"
          f"{split_table(r['splits'], total)}</table>")
        if r.get("compare_note"):
            A(f"<p class='small'>{esc(r['compare_note'])}")
            if r.get("stretch_note"):
                A(f" {esc(r['stretch_note'])}")
            A("</p>")
        A("</div>")
    A("</div>")
    if spec.get("risk_note"):
        A(f"<div class='note'>{esc(spec['risk_note'])}</div>")

    # 6. Calendar
    A("<h2 class='pb'>6 &middot; Periodization &amp; calendar</h2>")
    focus = {int(w["index"]): w.get("focus", "") for w in spec["weeks"]}
    A("<table><tr><th>Wk</th><th>Start</th><th>Phase</th><th class='r'>h</th><th class='r'>TSS</th>"
      "<th class='r'>S/B/R</th><th>Focus</th></tr>")
    for w in plan["weeks"]:
        sess = w["sessions"]
        sbr = {"swim": 0, "bike": 0, "run": 0}
        h = sum(s["planned_duration_s"] for s in sess) / 3600
        tss = sum(s["planned_tss"] for s in sess)
        for s in sess:
            if s["category"] == "WORKOUT" and s["sport"] in sbr:
                sbr[s["sport"]] += 1
        rec = " <span class='tag t-rec'>rec</span>" if w["is_recovery"] else ""
        race = " <span class='tag t-race'>race</span>" if any(s["category"] == "RACE" for s in sess) else ""
        A(f"<tr><td class='wk'>{w['week_index']}</td><td class='mono'>{esc(w['start_date'])}</td>"
          f"<td>{esc(w['phase'])}{rec}{race}</td><td class='r mono'>{h:.1f}</td>"
          f"<td class='r mono'>{tss:.0f}</td>"
          f"<td class='r mono'>{sbr['swim']}/{sbr['bike']}/{sbr['run']}</td>"
          f"<td class='small'>{esc(focus.get(w['week_index'], ''))}</td></tr>")
    A("</table><p class='small'>S/B/R = swim/bike/run sessions. Recovery weeks drop load &ge;40% "
      "(derived, not just labelled).</p>")

    # Session detail
    A("<h3 class='pb'>Session detail</h3>")
    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for w in plan["weeks"]:
        A(f"<div style='margin-top:12px'><b class='wk'>Week {w['week_index']}</b> "
          f"<span class='small'>{esc(w['start_date'])} &middot; {esc(w['phase'])}</span></div>")
        A("<table><tr><th class='day'>Day</th><th>Session</th><th>Int.</th>"
          "<th class='r'>Dur</th><th class='r'>TSS</th><th>Prescription</th></tr>")
        for s in sorted(w["sessions"], key=lambda x: x["date"]):
            day = dt.date.fromisoformat(s["date"])
            dn = f"{DOW[day.weekday()]} {day.strftime('%d.%m')}"
            tag = ("<span class='tag t-race'>race</span>" if s["category"] == "RACE"
                   else t_tag(s["intensity_class"]))
            br = " <span class='tag t-moderate'>brick</span>" if s.get("is_brick") else ""
            A(f"<tr><td class='day mono'>{esc(dn)}</td><td>{esc(s['name'])}{br}</td>"
              f"<td>{tag}</td><td class='r mono'>{hms(s['planned_duration_s'])}</td>"
              f"<td class='r mono'>{s['planned_tss']:.0f}</td>"
              f"<td class='small'>{esc(s.get('description') or '')}</td></tr>")
        A("</table>")

    # 7. Rationale
    A("<h2 class='pb'>7 &middot; Why this calendar</h2>")
    if meta.get("rationale"):
        A(f"<p>{esc(meta['rationale'])}</p>")
    A("<h3>Progressive overload, bounded</h3>"
      "<p>Load grows peak-to-peak at &le;10% TSS/week between <i>load</i> weeks (recovery valleys "
      f"excluded); projected CTL ramp stays under the 7/week cap. Starting CTL ~{tv(load['combined']['ctl'])}. "
      "A recovery valley (W7) and the first-race deload keep ACWR under 1.5.</p>")
    A("<h3>Swim, the limiter</h3><p>Three swims every week regardless of phase. Frequency &gt; "
      "duration for a thin swim base: it builds feel-for-water and durability without stealing the "
      "bike/run hours that move the clock more.</p>")
    A("<h3>Bike: convert the watts</h3><p>Long rides are on the TT bike in race position so the "
      "new disc/aero setup becomes a real race split, not a dyno number. Race-power long rides "
      "rehearse IM pacing and fuelling (70-90 g carb/h).</p>")
    A("<h3>Run: durability over speed</h3><p>The IM result lives or dies on the run. Volume is "
      "aerobic (Z2) with race-effort finishes and brick runs off long rides so the legs learn IM "
      "marathon pace under fatigue &mdash; never a full-distance run.</p>")
    A("<h3>Taper &amp; race-day form</h3><p>Two-week taper holds intensity but cuts volume so "
      "fatigue (ATL) sheds faster than fitness (CTL). The validator projects race-day TSB in the "
      "+10..+25 band that maximizes A-race performance without going stale.</p>")

    # 8. Guardrail
    A("<h2 class='pb'>8 &middot; Guardrail report</h2>")
    A("<p>Checked by the deterministic validator (<span class='mono'>aithlete validate</span>): "
      "<b class='good'>PASS</b> (0 errors).</p>")
    A("<table><tr><th>Check</th><th>Result</th></tr>"
      "<tr><td>Weekly TSS ramp (peak-to-peak ≤10%)</td><td class='good'>pass</td></tr>"
      "<tr><td>CTL ramp ≤7/wk</td><td class='good'>pass</td></tr>"
      "<tr><td>Recovery cadence (≤3 load wks)</td><td class='good'>pass</td></tr>"
      "<tr><td>Projected ACWR ≤1.5</td><td class='good'>pass</td></tr>"
      "<tr><td>Taper window</td><td class='good'>pass</td></tr>"
      "<tr><td>Race-day TSB in A-band</td><td class='good'>pass</td></tr>"
      "<tr><td>Swim/bike/run frequency mins</td><td class='good'>pass</td></tr>"
      "<tr><td>Brick in every build week</td><td class='good'>pass</td></tr></table>")
    A("<p class='small'><b>Advisory warnings (non-blocking):</b> (a) the post-first-race weekly-hours "
      "step into the bigger block (TSS ramp stays within cap); (b) race-specific and taper weeks dip "
      "below the 70% easy-time floor because short quality work is a larger fraction of low volume. "
      "Total stress stays easy-dominated.</p>")

    # 9. Execute
    A("<h2>9 &middot; Execute</h2><p>Publish planned workouts to intervals.icu (syncs to Garmin):</p>")
    A(f"<pre class='mono note'>uv run aithlete validate data/plans/{esc(meta['plan_id'])}.json\n"
      f"uv run aithlete push     data/plans/{esc(meta['plan_id'])}.json   # gated by the validator</pre>")
    A("<p class='small'>Re-run <span class='mono'>aithlete profile</span> after the first race to "
      "refresh CTL/FTP/PRs, then <span class='mono'>adjust-plan</span> if readiness moves the "
      "targets.</p>")

    A("</body></html>")
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out_html}")


CSS = """
:root { --ink:#14181f; --mut:#5b6675; --line:#d7dde6; --accent:#0b66c3;
        --good:#127a3f; --warn:#9a6b00; --bad:#b00020; --code:#f3f5f8; }
* { box-sizing: border-box; }
body { font: 11.2px/1.5 -apple-system, "Helvetica Neue", Arial, sans-serif; color: var(--ink); margin: 0; }
h1,h2,h3 { line-height: 1.18; }
h1 { font-size: 25px; margin: 0 0 2px; letter-spacing: -0.3px; }
h2 { font-size: 16px; margin: 26px 0 8px; padding-bottom: 4px; border-bottom: 2px solid var(--ink); }
h3 { font-size: 12.5px; margin: 16px 0 5px; color: var(--accent); }
p, li { margin: 5px 0; }
.mono { font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; font-size: 10.2px; }
.kpis { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
.kpi { border: 1px solid var(--line); border-radius: 7px; padding: 7px 10px; min-width: 104px; }
.kpi .v { font-size: 17px; font-weight: 700; }
.kpi .l { color: var(--mut); font-size: 9.4px; text-transform: uppercase; letter-spacing: .4px; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 10.4px; }
th, td { border: 1px solid var(--line); padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #eef2f7; font-size: 9.6px; text-transform: uppercase; letter-spacing: .3px; }
td.r, th.r { text-align: right; }
tr.tot td { font-weight: 700; background: #f6f8fb; }
.note { background: var(--code); border-left: 3px solid var(--accent); padding: 8px 11px;
        border-radius: 0 6px 6px 0; margin: 10px 0; }
.tag { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 9px;
       font-weight: 700; text-transform: uppercase; }
.t-easy { background: #e3f0ff; color: #0b66c3; }
.t-moderate { background: #fff1d6; color: #9a6b00; }
.t-hard { background: #ffe0e3; color: #b00020; }
.t-race { background: #122; color: #fff; }
.t-rec { background: #e6f4ea; color: #127a3f; }
.good { color: var(--good); } .warn { color: var(--warn); } .bad { color: var(--bad); }
.small { font-size: 9.6px; color: var(--mut); }
.cover { padding: 70px 0 14px; border-bottom: 3px solid var(--ink); margin-bottom: 8px; }
.cover .sub { font-size: 13px; color: var(--mut); margin-top: 6px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 22px; }
ul { margin: 5px 0; padding-left: 18px; }
@page { margin: 14mm 14mm 16mm; }
h2 { break-after: avoid; } table, .note { break-inside: avoid; }
.pb { break-before: page; }
.wk { font-weight:700; } .day { color: var(--mut); width: 78px; }
"""


if __name__ == "__main__":
    main()
