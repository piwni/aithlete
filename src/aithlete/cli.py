"""Aithlete CLI — the commands the agent skills (and humans) invoke.

    aithlete fetch       pull data from sources -> raw (offline by default)
    aithlete analyze     print a metrics summary from raw data
    aithlete readiness   compute today's readiness flag (+persist)
    aithlete profile     build a baseline provenance-tagged profile (+persist)
    aithlete context     build the bounded context digest the agent reads (+persist)
    aithlete validate    run the deterministic guardrails against a plan JSON
    aithlete push        validate then push a plan to intervals.icu (gated)
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from aithlete import knowledge
from aithlete.analysis import context as context_mod
from aithlete.analysis import metrics, profile_builder
from aithlete.analysis import readiness as readiness_mod
from aithlete.analysis.loaders import data_as_of, load_activities, load_settings, load_wellness
from aithlete.config import get_config
from aithlete.connectors.fetch import fetch_all
from aithlete.connectors.intervals import IntervalsClient
from aithlete.models.plan import PlanInputs, TrainingPlan
from aithlete.models.profile import AthleteProfile
from aithlete.planning.validator import validate_plan
from aithlete.planning.workout_builder import garmin_sync_warnings, plan_to_events
from aithlete.storage.io import load_json, save_json
from aithlete.storage.paths import (
    active_plan_path,
    calendar_path,
    context_path,
    plan_path,
    profile_path,
)

app = typer.Typer(add_completion=False, help="Triathlete analysis + AI training planning.")
console = Console()


def _load_frames():
    acts = load_activities()
    well = load_wellness()
    return acts, well


@app.command()
def fetch(
    full_resync: bool = typer.Option(False, help="Ignore watermark and refetch the lookback window."),
    since: str = typer.Option(None, help="Fetch from this date (YYYY-MM-DD), e.g. your first training. Overrides --days."),
    days: int = typer.Option(180, help="Lookback window in days when --since is not given."),
    reset: bool = typer.Option(False, help="Wipe the regenerable raw cache + watermark before fetching (clean slate)."),
):
    """Pull data from sources, dedup, and persist to the partitioned raw store."""
    since_date = None
    if since:
        try:
            since_date = dt.date.fromisoformat(since)
        except ValueError as exc:
            console.print(f"[red]Invalid --since date '{since}': {exc}. Use YYYY-MM-DD.[/]")
            raise typer.Exit(2) from exc
    res = fetch_all(full_resync=full_resync, since=since_date, lookback_days=days, reset=reset)
    mode = "offline (synthetic athlete)" if res["offline"] else "live"
    console.print(f"[bold green]Fetched[/] {res['activities']} activities, "
                  f"{res['wellness_days']} wellness days [{mode}] window={res['window']}")


@app.command()
def analyze():
    """Print a human/agent-readable metrics summary from the raw data."""
    acts, well = _load_frames()
    if acts.empty:
        console.print("[yellow]No activities. Run `aithlete fetch` first.[/]")
        raise typer.Exit(1)
    as_of = data_as_of(acts, well)
    load = metrics.latest_load(acts)
    base = metrics.hrv_rhr_baselines(well, as_of)
    dist = metrics.intensity_distribution(acts)

    t = Table(title=f"Training load as of {as_of}")
    for col in ("series", "CTL", "ATL", "TSB", "trend"):
        t.add_column(col)
    for k, v in load.items():
        t.add_row(k, f"{v['ctl']:.1f}", f"{v['atl']:.1f}",
                  f"{v['tsb']:.1f}" if v["tsb"] is not None else "-", v["trend"])
    console.print(t)
    console.print(f"Ramp/wk: [bold]{metrics.ctl_ramp_per_week(acts)}[/] CTL/week")
    if dist:
        console.print(f"Intensity (time): easy {dist.get('easy',0)*100:.0f}% / "
                      f"moderate {dist.get('moderate',0)*100:.0f}% / hard {dist.get('hard',0)*100:.0f}%")
    console.print(f"HRV 60d baseline: {base.get('hrv_baseline_60d_ms')} ms (SWC {base.get('hrv_swc_ms')}) | "
                  f"RHR baseline: {base.get('resting_hr_baseline_bpm')} bpm")
    for w in metrics.weekly_summary(acts, weeks=4):
        console.print(f"  week {w['week_start']}: {w['tss']:.0f} TSS / {w['hours']:.1f} h")


@app.command()
def readiness(
    subjective: float = typer.Option(None, help="Subjective wellness mean 1-5 (5 best)."),
    illness: str = typer.Option(None, help="'above_neck' or 'below_neck' if ill."),
):
    """Compute today's readiness flag and persist it for the context digest."""
    acts, well = _load_frames()
    rep = readiness_mod.evaluate(well, acts, subjective_wellness=subjective, illness=illness)
    color = {"green": "green", "amber": "yellow", "red": "red"}[rep.overall_flag.value]
    console.print(f"Readiness: [bold {color}]{rep.overall_flag.value.upper()}[/] -> {rep.recommended_action}")
    for r in rep.results:
        c = {"green": "green", "amber": "yellow", "red": "red"}[r.flag.value]
        console.print(f"  [{c}]{r.flag.value:5}[/] {r.rule}: {r.message}")
    out = get_config().context_dir / "readiness-athlete.json"
    save_json(out, rep.model_dump(mode="json"))
    console.print(f"[dim]saved {out}[/]")


@app.command()
def profile(athlete: str = typer.Option("athlete", help="Athlete id.")):
    """Build a baseline provenance-tagged profile from fetched data (+persist).

    The agent refines interpretive fields (strengths/limiters, notes) afterwards.
    """
    acts, well = _load_frames()
    settings = load_settings()
    as_of = data_as_of(acts, well)
    prof = profile_builder.build_profile(settings, acts, well, as_of, athlete_id=athlete)
    # Preserve agent-authored interpretive fields if a profile already exists.
    # Race results are auto-detected from history on every run (not preserved).
    existing = load_json(profile_path(athlete))
    if existing:
        prev = AthleteProfile.model_validate(existing)
        prof.strengths = prev.strengths
        prof.limiters = prev.limiters
        prof.injury_history = prev.injury_history
        prof.training_age = prev.training_age
        prof.weekly_volume_tolerance_hours = prev.weekly_volume_tolerance_hours
        prof.notes = prev.notes
    save_json(profile_path(athlete), prof.model_dump(mode="json"))
    console.print(f"[green]Wrote baseline profile[/] {profile_path(athlete)}")
    if prof.results:
        bests = prof.best_times()
        console.print(f"  Detected [bold]{len(prof.results)}[/] races; "
                      f"PRs in {len(bests)} distance(s):")
        for bucket, bt in sorted(bests.items(), key=lambda kv: kv[0].value):
            h, rem = divmod(int(bt.total_time_s), 3600)
            m, s = divmod(rem, 60)
            console.print(f"    {bucket.value:>4} {h}:{m:02d}:{s:02d}  "
                          f"{bt.event_name or '—'} ({bt.date})")
    console.print(f"  FTP {prof.bike.ftp_w.value} W ({prof.bike.ftp_w.provenance.value}), "
                  f"CTL {prof.load.combined.ctl.value}, VO2max {prof.bike.vo2max.value} (estimated)")


@app.command()
def context(athlete: str = typer.Option("athlete", help="Athlete id.")):
    """Build the bounded context digest (profile + data + readiness + calendar)."""
    acts, well = _load_frames()
    prof_data = load_json(profile_path(athlete))
    if not prof_data:
        console.print("[yellow]No profile yet. Run `aithlete profile` first.[/]")
        raise typer.Exit(1)
    prof = AthleteProfile.model_validate(prof_data)
    rep_data = load_json(get_config().context_dir / "readiness-athlete.json")
    rep = None
    if rep_data:
        from aithlete.models.readiness import ReadinessReport
        rep = ReadinessReport.model_validate(rep_data)
    cal = load_json(calendar_path(athlete), default=[])
    digest = context_mod.build_digest(prof, acts, well, readiness=rep, calendar=cal, athlete_id=athlete)
    save_json(context_path(athlete), digest.model_dump(mode="json"))
    console.print(f"[green]Wrote context digest[/] {context_path(athlete)}")
    console.print(f"  sha256={digest.sha256()[:16]} | readiness={digest.readiness_flag.value} | "
                  f"races={len(digest.upcoming_races)} | missing={digest.missing}")


@app.command()
def validate(plan_file: Path = typer.Argument(..., help="Path to a plan JSON.")):
    """Run the deterministic guardrails. Exits non-zero on errors."""
    plan = TrainingPlan.model_validate(load_json(plan_file))
    rep = _validate_with_profile(plan)
    _print_validation(rep)
    raise typer.Exit(0 if rep.ok else 1)


@app.command()
def push(
    plan_file: Path = typer.Argument(..., help="Path to a plan JSON."),
    target: str = typer.Option("intervals", help="Push target (currently: intervals)."),
    dry_run: bool = typer.Option(False, help="Build + validate events but do not push."),
    set_active: bool = typer.Option(True, help="Record this as the active plan."),
):
    """Validate (gate) then push a plan to intervals.icu."""
    if target != "intervals":
        console.print(f"[red]Unsupported target '{target}'. Only 'intervals' is supported.[/]")
        raise typer.Exit(2)
    plan = TrainingPlan.model_validate(load_json(plan_file))

    # Stamp provenance inputs.
    plan.inputs = _stamp_inputs(plan)

    rep = _validate_with_profile(plan)
    _print_validation(rep)
    if not rep.ok:
        console.print("[red]Push blocked: fix validation errors first.[/]")
        raise typer.Exit(1)

    events = plan_to_events(plan)
    for w in garmin_sync_warnings(plan):
        console.print(f"[yellow]garmin-sync:[/] {w}")

    # Persist the (now stamped) plan and active pointer.
    save_json(plan_path(plan.plan_id), plan.model_dump(mode="json"))
    if set_active:
        save_json(active_plan_path(plan.athlete_id), plan.model_dump(mode="json"))

    if dry_run:
        out = get_config().plans_dir / f"{plan.plan_id}-events.json"
        save_json(out, {"events": events})
        console.print(f"[green]Dry run:[/] {len(events)} events written to {out} (not pushed)")
        return

    client = IntervalsClient()
    result = client.push_events(events, upsert=True)
    console.print(f"[green]Pushed[/] {len(events)} events to intervals.icu: {result}")


# --- helpers ---------------------------------------------------------------
def _validate_with_profile(plan: TrainingPlan):
    prof_data = load_json(profile_path(plan.athlete_id))
    tolerance = starting_ctl = None
    training_age = "intermediate"
    if prof_data:
        prof = AthleteProfile.model_validate(prof_data)
        tolerance = prof.weekly_volume_tolerance_hours
        training_age = prof.training_age.value
        if prof.load.combined.ctl.known:
            starting_ctl = prof.load.combined.ctl.value
    if plan.starting_ctl is not None:
        starting_ctl = plan.starting_ctl
    return validate_plan(plan, starting_ctl=starting_ctl,
                         weekly_hours_tolerance=tolerance, training_age=training_age)


def _stamp_inputs(plan: TrainingPlan) -> PlanInputs:
    digest_data = load_json(context_path(plan.athlete_id))
    sha = None
    if digest_data:
        from aithlete.models.context_digest import ContextDigest
        sha = ContextDigest.model_validate(digest_data).sha256()
    prof_data = load_json(profile_path(plan.athlete_id))
    prof_at = None
    if prof_data:
        prof_at = AthleteProfile.model_validate(prof_data).generated_at
    return PlanInputs(
        context_digest_sha256=sha,
        knowledge_version=knowledge.knowledge_version(),
        profile_generated_at=prof_at,
        rules_sha256=knowledge.file_sha256("rules.yaml"),
    )


def _print_validation(rep) -> None:
    status = "[bold green]PASS[/]" if rep.ok else "[bold red]FAIL[/]"
    console.print(f"Validation: {status}  (errors={len(rep.errors)}, warnings={len(rep.warnings)})")
    if rep.projected_race_tsb is not None:
        console.print(f"  projected race-day TSB: {rep.projected_race_tsb}")
    for i in rep.issues:
        c = "red" if i.severity.value == "error" else "yellow"
        console.print(f"  [{c}]{i.severity.value:7}[/] {i.code}: {i.message}")


if __name__ == "__main__":
    app()
