#!/usr/bin/env python3
"""MAD Pipeline — filesystem-based kanban for AI-assisted feature development.

Usage:
    pipeline run [feature_id]
    pipeline restart <feature_id> [--from PHASE]
    pipeline auto [--all]
    pipeline lock status|clear
    pipeline service install|uninstall|status
    pipeline edit-feature <slug> set-field|get-field|set-test-results
    pipeline tui
    pipeline schedule add|list|show|pause|resume|delete|daemon
    pipeline runs list|show
"""

import logging
import sys
from pathlib import Path

import click
import json
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

logger = logging.getLogger("pipeline")

from config import Config
from lock import PipelineLock, PipelineLockError
from phases import run_pipeline, run_planning, run_pipeline_from_implementing, run_verify_tests
from runner import AgentRunner
from schedule import (
    RunRecord,
    RunStore,
    Schedule,
    ScheduleStore,
    SchedulerDaemon,
    _now_iso,
    validate_cron_expression,
    validate_interval,
)
from state import STAGES, FeatureFile

console = Console()

FIELD_SETTERS = {
    "title": "set_title",
    "description": "set_description",
    "plan": "set_plan",
    "impl_spec": "set_impl_spec",
    "test_spec": "set_test_spec",
    "impl_notes": "set_impl_notes",
    "type": "set_item_type",
    "design_ref": "set_design_ref",
    "done_script": "set_done_script",
    "questions": "set_questions",
    "requires_human_approval": "set_requires_human_approval",
    "ideation_prompt": "set_ideation_prompt",
    "ideation_max_rounds": "set_ideation_max_rounds",
}

FIELD_GETTERS = {
    "title": "title",
    "description": "description",
    "plan": "plan",
    "impl_spec": "impl_spec",
    "test_spec": "test_spec",
    "impl_notes": "impl_notes",
    "type": "item_type",
    "design_ref": "design_ref",
    "done_script": "done_script",
    "questions": "questions",
    "requires_human_approval": "requires_human_approval",
    "ideation_prompt": "ideation_prompt",
    "ideation_max_rounds": "ideation_max_rounds",
}


def _read_input(value, stdin_flag, file_path):
    """Read input from value, stdin, or file based on which source is specified."""
    if file_path:
        try:
            return Path(file_path).read_text()
        except FileNotFoundError:
            console.print(f"[red]File not found:[/red] {file_path}")
            sys.exit(1)
        except PermissionError:
            console.print(f"[red]Permission denied:[/red] {file_path}")
            sys.exit(1)
        except Exception as e:
            console.print(f"[red]Error reading file:[/red] {e}")
            sys.exit(1)
    
    if stdin_flag:
        content = sys.stdin.read()
        return content
    
    if value is not None:
        return value
    
    console.print("[red]Error:[/red] No value provided. Use argument, --stdin, or --file.")
    sys.exit(1)


def get_config() -> Config:
    """Load config and ensure board directories exist."""
    config = Config()
    config.setup_boards()
    return config


def find_feature_or_exit(query: str) -> FeatureFile:
    """Find a feature by id/slug/partial match, or exit with error."""
    feature = FeatureFile.find(query)
    if not feature:
        console.print(f"[red]Feature not found:[/red] {query}")
        console.print("[dim]Try the TUI or web UI to browse features.[/dim]")
        sys.exit(1)
    return feature


# ---------------------------------------------------------------------------
# CLI Group
# ---------------------------------------------------------------------------

@click.group()
def cli():
    """MAD Pipeline — filesystem-based kanban for AI-assisted feature development."""
    pass


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# pipeline run
# ---------------------------------------------------------------------------

@cli.command("run")
@click.argument("feature_id", required=False, default=None)
@click.option("--force", is_flag=True, help="Override existing pipeline lock")
def run_feature(feature_id: str, force: bool):
    """Run the full automated pipeline on a feature.

    If no feature_id given, picks the oldest approved feature.
    """
    config = get_config()
    runner = AgentRunner(config)
    lock = PipelineLock(config)

    if feature_id:
        feature = find_feature_or_exit(feature_id)
    else:
        # Pick oldest approved feature
        approved = FeatureFile.list_all(stage="approved")
        if not approved:
            console.print("[yellow]No approved features to run.[/yellow]")
            console.print("[dim]Approve a feature first via the TUI or web UI.[/dim]")
            sys.exit(1)

        # Sort by created date (oldest first)
        approved.sort(key=lambda f: f.created)
        feature = approved[0]
        console.print(f"[dim]Auto-selected oldest approved feature: {feature.slug}[/dim]")

    if feature.current_stage != "approved":
        console.print(
            f"[yellow]Warning:[/yellow] Feature is in '{feature.current_stage}', "
            f"not 'approved'. Proceeding anyway."
        )

    if not lock.acquire("impl", force=force):
        info = lock.check("impl")
        if info:
            console.print(f"[red]Error:[/red] Pipeline locked by PID {info.pid} on {info.hostname} since {info.timestamp} (user: {info.username})")
        else:
            console.print(f"[red]Error:[/red] Pipeline is locked")
        sys.exit(1)
    
    run_store = RunStore()
    run = RunRecord.create_manual(feature_id=feature.id, feature_slug=feature.slug)
    run_store.save(run)
    try:
        run_pipeline(feature, runner)
        run.status = "success"
        run.ended_at = _now_iso()
    except Exception as e:
        run.status = "failed"
        run.ended_at = _now_iso()
        run.error = str(e)
        raise
    finally:
        run_store.save(run)
        lock.release("impl")


@cli.command("restart")
@click.argument("feature_id")
@click.option("--from", "from_phase", 
              type=click.Choice(["spec-writing", "implementing", "testing", "review"]),
              default="implementing",
              help="Phase to restart from (default: implementing)")
@click.option("--force", is_flag=True, help="Override existing pipeline lock")
def restart_feature(feature_id: str, from_phase: str, force: bool):
    """Restart pipeline from a specific phase.
    
    Use this to resume a stuck feature or re-run from a specific stage.
    
    Examples:
    
        pipeline restart tutorial-quest-chain --from implementing
        pipeline restart tutorial-quest-chain --from testing
        pipeline restart tutorial-quest-chain --from review
    """
    from phases import (
        run_spec_writing, run_implementing, run_verify_tests,
        run_review_impl, run_pipeline_from_implementing
    )
    
    config = get_config()
    runner = AgentRunner(config)
    lock = PipelineLock(config)
    feature = find_feature_or_exit(feature_id)
    
    if not lock.acquire("impl", force=force):
        info = lock.check("impl")
        if info:
            console.print(f"[red]Error:[/red] Pipeline locked by PID {info.pid} on {info.hostname} since {info.timestamp} (user: {info.username})")
        else:
            console.print(f"[red]Error:[/red] Pipeline is locked")
        sys.exit(1)
    
    console.print(f"[bold]Restarting from:[/bold] {from_phase}")
    console.print(f"[dim]Current stage: {feature.current_stage}[/dim]")
    
    run_store = RunStore()
    run = RunRecord.create_manual(feature_id=feature.id, feature_slug=feature.slug)
    run_store.save(run)
    
    try:
        verdict = "FAIL"  # default
        
        if from_phase == "spec-writing":
            run_pipeline(feature, runner)
        elif from_phase == "implementing":
            run_pipeline_from_implementing(feature, runner)
        elif from_phase == "testing":
            from phases import run_fix_feedback, _get_latest_feedback
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                if attempt == 1:
                    test_verdict, test_fb = run_verify_tests(feature, runner)
                else:
                    fb = _get_latest_feedback(feature)
                    run_fix_feedback(feature, runner, fb)
                    test_verdict, test_fb = run_verify_tests(feature, runner)
                if test_verdict != "PASS":
                    if attempt < max_attempts:
                        console.print(f"[yellow]Tests failed, retry {attempt}/{max_attempts}[/yellow]")
                    continue
                verdict, feedback = run_review_impl(feature, runner)
                if verdict == "PASS":
                    break
                if attempt < max_attempts:
                    console.print(f"[yellow]Review failed, retry {attempt}/{max_attempts}[/yellow]")
            if verdict != "PASS":
                feature.move_to_stage("final-human-approval")
        elif from_phase == "review":
            verdict, feedback = run_review_impl(feature, runner)
            if verdict == "PASS":
                console.print(f"[green]Review passed![/green]")
            else:
                console.print(f"[yellow]Review failed, moved to implementing[/yellow]")
        
        run.status = "success"
        run.ended_at = _now_iso()
    except Exception as e:
        run.status = "failed"
        run.ended_at = _now_iso()
        run.error = str(e)
        raise
    finally:
        run_store.save(run)
        lock.release("impl")


@cli.command()
@click.option("--all", "run_all", is_flag=True, help="Run all approved features once")
@click.option("--interval", default=30, help="Polling interval in seconds (only with --all)")
@click.option("--force", is_flag=True, help="Override existing pipeline lock")
def auto(run_all: bool, interval: int, force: bool):
    """Run the pipeline automatically on approved features.
    
    Without --all: runs continuously, monitoring for new approved features.
    With --all: runs once through all currently approved features.
    
    Press Ctrl+C to stop the continuous loop.
    """
    import time
    
    config = get_config()
    runner = AgentRunner(config)
    lock = PipelineLock(config)
    processed_slugs: set[str] = set()
    
    def process_features():
        """Process features in pipeline stages, returns True if one was processed."""
        from phases import run_pipeline, run_review_impl
        
        for board in config.boards:
            # Check stages in order of priority
            # 1. implementing - features that came back from failed review
            implementing = FeatureFile.list_all(board=board, stage="implementing")
            for f in implementing:
                if f.slug in processed_slugs:
                    continue
                
                # Check lock
                if not force and lock.check('impl'):
                    console.print(f"[dim]Skipping {f.title} - locked by another process[/dim]")
                    continue
                
                if not lock.acquire('impl', force=force):
                    console.print(f"[dim]Skipping {f.title} - could not acquire lock[/dim]")
                    continue
                
                processed_slugs.add(f.slug)
                console.print(f"\n[bold cyan]Resuming:[/bold cyan] {f.title} (in implementing)")
                
                try:
                    # Run from implementing through review
                    run_pipeline_from_implementing(f, runner)
                    console.print(f"[green]Completed:[/green] {f.title}")
                except Exception as e:
                    console.print(f"[red]Failed:[/red] {e}")
                    processed_slugs.discard(f.slug)
                finally:
                    lock.release('impl')
                
                return True
            
            # 2. review - features that need re-review
            review = FeatureFile.list_all(board=board, stage="review")
            for f in review:
                if f.slug in processed_slugs:
                    continue
                
                # Check lock
                if not force and lock.check('impl'):
                    console.print(f"[dim]Skipping {f.title} - locked by another process[/dim]")
                    continue
                
                if not lock.acquire('impl', force=force):
                    console.print(f"[dim]Skipping {f.title} - could not acquire lock[/dim]")
                    continue
                
                processed_slugs.add(f.slug)
                console.print(f"\n[bold cyan]Re-reviewing:[/bold cyan] {f.title}")
                
                try:
                    verdict, feedback = run_review_impl(f, runner)
                    console.print(f"[dim]Review verdict: {verdict}[/dim]")
                    if verdict == "PASS":
                        console.print(f"[green]Passed review:[/green] {f.title}")
                    else:
                        console.print(f"[yellow]Failed review, moved to implementing:[/yellow] {f.title}")
                        processed_slugs.discard(f.slug)  # Allow retry after failure
                except Exception as e:
                    console.print(f"[red]Failed:[/red] {e}")
                    processed_slugs.discard(f.slug)
                finally:
                    lock.release('impl')
                
                return True
            
            # 3. ideating - run ideation debate, move back to ideas
            ideating = FeatureFile.list_all(board=board, stage="ideating")
            logger.info(f"[auto] Checking {board}/ideating: found {len(ideating)} items")
            for f in ideating:
                if f.slug in processed_slugs:
                    continue
                
                # Check lock
                if not force and lock.check('impl'):
                    console.print(f"[dim]Skipping {f.title} - locked by another process[/dim]")
                    continue
                
                if not lock.acquire('impl', force=force):
                    console.print(f"[dim]Skipping {f.title} - could not acquire lock[/dim]")
                    continue
                
                processed_slugs.add(f.slug)
                console.print(f"\n[bold cyan]Ideating:[/bold cyan] {f.title}")
                
                try:
                    from phases import run_ideating
                    run_ideating(f, runner)
                    console.print(f"[green]Ideation complete:[/green] {f.title}")
                except Exception as e:
                    console.print(f"[red]Failed:[/red] {e}")
                    processed_slugs.discard(f.slug)
                finally:
                    lock.release('impl')
                
                return True
            
            # 4. approved - fresh features starting the pipeline
            approved = FeatureFile.list_all(board=board, stage="approved")
            for f in approved:
                if f.slug in processed_slugs:
                    continue
                if f.current_stage != "approved":
                    continue
                
                # Check lock
                if not force and lock.check('impl'):
                    console.print(f"[dim]Skipping {f.title} - locked by another process[/dim]")
                    continue
                
                if not lock.acquire('impl', force=force):
                    console.print(f"[dim]Skipping {f.title} - could not acquire lock[/dim]")
                    continue
                
                processed_slugs.add(f.slug)
                console.print(f"\n[bold cyan]Auto-running:[/bold cyan] {f.title}")
                
                try:
                    run_pipeline(f, runner)
                    console.print(f"[green]Completed:[/green] {f.title}")
                except Exception as e:
                    console.print(f"[red]Failed:[/red] {e}")
                    processed_slugs.discard(f.slug)
                finally:
                    lock.release('impl')
                
                return True
        return False
    
    if run_all:
        console.print(Panel(
            f"[bold]Running all approved features once[/bold]",
            border_style="cyan",
        ))
        while process_features():
            pass
        console.print("[green]All features processed.[/green]")
        return
    
    console.print(Panel(
        f"[bold]Auto-run mode started[/bold]\n"
        f"Polling every {interval} seconds for approved features.\n"
        f"Press Ctrl+C to stop.",
        border_style="cyan",
    ))
    
    try:
        while True:
            if not process_features():
                time.sleep(interval)
            
    except KeyboardInterrupt:
        console.print("\n[yellow]Auto-run stopped.[/yellow]")


# ---------------------------------------------------------------------------
# pipeline lock
# ---------------------------------------------------------------------------

@cli.group('lock')
def lock_group():
    """Manage pipeline locks."""
    pass


@lock_group.command('status')
def lock_status():
    """Show the current lock state for plan and impl phases."""
    config = get_config()
    lock = PipelineLock(config)
    
    from datetime import datetime
    
    console.print(Panel(
        "[bold]Pipeline Lock Status[/bold]",
        border_style="cyan",
    ))
    
    for phase in ['plan', 'impl', 'tui']:
        info = lock.check(phase)
        if info:
            try:
                lock_time = datetime.fromisoformat(info.timestamp)
                age = datetime.now() - lock_time
                age_str = f"{int(age.total_seconds() // 60)}m"
            except ValueError:
                age_str = "unknown"
            
            console.print(f"[bold]{phase.upper()}:[/bold] Locked by PID {info.pid} on {info.hostname}")
            console.print(f"  Started: {info.timestamp} (age: {age_str})")
            console.print(f"  User: {info.username}")
        else:
            console.print(f"[bold]{phase.upper()}:[/bold] [green]Free[/green]")
        console.print()


@lock_group.command('clear')
@click.argument('phase', type=click.Choice(['plan', 'impl', 'tui', 'all']))
def lock_clear(phase: str):
    """Force-clear the lock for a phase."""
    config = get_config()
    lock = PipelineLock(config)
    
    phases_to_clear = ['plan', 'impl', 'tui'] if phase == 'all' else [phase]
    
    for p in phases_to_clear:
        lock_path = lock._lock_path(p)
        if lock_path.exists():
            try:
                lock_path.unlink()
                console.print(f"[green]Cleared lock for '{p}'[/green]")
            except Exception as e:
                console.print(f"[red]Failed to clear lock for '{p}': {e}[/red]")
        else:
            console.print(f"[dim]No lock file for '{p}'[/dim]")


# ---------------------------------------------------------------------------
# pipeline service
# ---------------------------------------------------------------------------

@cli.group('service')
def service_group():
    """Manage systemd user service for this MAD project."""
    pass


@service_group.command('install')
def service_install():
    """Install and start a systemd user service for this project's TUI."""
    from service import install_service
    config = get_config()
    project_dir = config.code_path or Path.cwd()
    install_service(project_dir)


@service_group.command('uninstall')
def service_uninstall():
    """Stop and remove the systemd user service for this project."""
    from service import uninstall_service
    config = get_config()
    project_dir = config.code_path or Path.cwd()
    uninstall_service(project_dir)


@service_group.command('status')
def service_status_cmd():
    """Show the systemd service status for this project."""
    from service import service_status
    config = get_config()
    project_dir = config.code_path or Path.cwd()
    service_status(project_dir)


# ---------------------------------------------------------------------------
# pipeline edit-feature
# ---------------------------------------------------------------------------

@cli.group("edit-feature")
@click.argument("slug")
@click.pass_context
def edit_feature(ctx, slug: str):
    """Modify feature JSON files using structured commands.
    
    Examples:
    
        pipeline edit-feature myfeature set-field title "New Title"
        pipeline edit-feature myfeature get-field plan
    """
    feature = find_feature_or_exit(slug)
    ctx.obj = {"feature": feature}


@edit_feature.command("set-field")
@click.argument("field_name")
@click.argument("value", required=False)
@click.option("--stdin", is_flag=True, help="Read value from stdin")
@click.option("--file", "file_path", type=click.Path(), help="Read value from file")
@click.pass_context
def set_field(ctx, field_name: str, value: str, stdin: bool, file_path: str):
    """Set a field on a feature file.
    
    Examples:
    
        pipeline edit-feature myfeature set-field title "New Title"
        echo "multi-line content" | pipeline edit-feature myfeature set-field plan --stdin
        pipeline edit-feature myfeature set-field impl_spec --file /path/to/spec.md
    """
    feature = ctx.obj["feature"]
    
    if field_name not in FIELD_SETTERS:
        console.print(f"[red]Invalid field:[/red] {field_name}")
        console.print(f"[dim]Supported fields: {', '.join(FIELD_SETTERS.keys())}[/dim]")
        sys.exit(1)
    
    content = _read_input(value, stdin, file_path)
    
    try:
        if field_name == "questions":
            parsed = json.loads(content)
            if not isinstance(parsed, list):
                console.print("[red]Error:[/red] questions must be a JSON array")
                sys.exit(1)
            for item in parsed:
                if not isinstance(item, dict) or "question" not in item:
                    console.print("[red]Error:[/red] each question must be a dict with 'question' key")
                    sys.exit(1)
            getattr(feature, FIELD_SETTERS[field_name])(parsed)
        elif field_name == "requires_human_approval":
            if content.lower() in ("true", "1", "yes"):
                getattr(feature, FIELD_SETTERS[field_name])(True)
            elif content.lower() in ("false", "0", "no"):
                getattr(feature, FIELD_SETTERS[field_name])(False)
            else:
                console.print("[red]Error:[/red] requires_human_approval must be true/false")
                sys.exit(1)
        elif field_name == "ideation_max_rounds":
            if content.strip() == "":
                getattr(feature, FIELD_SETTERS[field_name])(None)
            else:
                try:
                    val = int(content.strip())
                    if val < 0:
                        console.print("[red]Error:[/red] ideation_max_rounds must be a non-negative integer")
                        sys.exit(1)
                    getattr(feature, FIELD_SETTERS[field_name])(val)
                except ValueError:
                    console.print("[red]Error:[/red] ideation_max_rounds must be an integer")
                    sys.exit(1)
        else:
            getattr(feature, FIELD_SETTERS[field_name])(content)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    
    console.print(f"[green]Set {field_name} on {feature.slug}[/green]")


@edit_feature.command("get-field")
@click.argument("field_name")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
@click.pass_context
def get_field(ctx, field_name: str, json_output: bool):
    """Get a field value from a feature file.
    
    Examples:
    
        pipeline edit-feature myfeature get-field plan
        pipeline edit-feature myfeature get-field plan --json
    """
    feature = ctx.obj["feature"]
    
    if field_name not in FIELD_GETTERS:
        console.print(f"[red]Invalid field:[/red] {field_name}")
        console.print(f"[dim]Supported fields: {', '.join(FIELD_GETTERS.keys())}[/dim]")
        sys.exit(1)
    
    val = getattr(feature, FIELD_GETTERS[field_name])
    
    if json_output:
        print(json.dumps(val))
    else:
        if isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2))
        else:
            print(val)


@edit_feature.command("set-test-results")
@click.argument("json_data", required=False)
@click.option("--stdin", is_flag=True, help="Read JSON from stdin")
@click.pass_context
def set_test_results(ctx, json_data: str, stdin: bool):
    """Set test results on a feature file.
    
    Examples:
    
        pipeline edit-feature myfeature set-test-results '{"passed": 5, "failed": 1, "details": "..."}'
        echo '{"passed": 3}' | pipeline edit-feature myfeature set-test-results --stdin
    """
    feature = ctx.obj["feature"]
    
    if stdin:
        content = sys.stdin.read()
    elif json_data:
        content = json_data
    else:
        console.print("[red]Error:[/red] Provide JSON data as argument or use --stdin")
        sys.exit(1)
    
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            console.print("[red]Error:[/red] test results must be a JSON object")
            sys.exit(1)
        feature.set_test_results(parsed)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON:[/red] {e}")
        sys.exit(1)
    
    console.print(f"[green]Set test_results on {feature.slug}[/green]")


# ---------------------------------------------------------------------------
# pipeline tui
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--force", is_flag=True, help="Override existing TUI lock and start a new instance")
def tui(force: bool):
    """Open the interactive TUI dashboard."""
    from tui import run_tui
    run_tui(force=force)


# ---------------------------------------------------------------------------
# pipeline schedule
# ---------------------------------------------------------------------------

@cli.group()
def schedule():
    """Manage pipeline schedules."""
    pass


@schedule.command("add")
@click.argument("feature_id")
@click.option("--cron", "expression", default=None, help="Cron expression (e.g. '0 * * * *')")
@click.option("--interval", "interval_seconds", default=None, type=int,
              help="Interval in seconds (alternative to --cron)")
@click.option("--tz", default="UTC", help="Timezone for cron expression (default: UTC)")
@click.option("--conflict", default="skip",
              type=click.Choice(["skip", "queue"]),
              help="Policy when a previous run is still in progress (default: skip)")
@click.option("--catch-up", default="skip",
              type=click.Choice(["skip", "run_once"]),
              help="Policy for missed runs after downtime (default: skip)")
def schedule_add(
    feature_id: str,
    expression: str,
    interval_seconds: int,
    tz: str,
    conflict: str,
    catch_up: str,
):
    """Add a schedule to a pipeline/feature.

    Provide either --cron or --interval (not both).

    Examples:

      pipeline schedule add my-feature --cron "0 9 * * 1-5"

      pipeline schedule add my-feature --interval 3600
    """
    if expression is None and interval_seconds is None:
        console.print("[red]Error:[/red] Provide either --cron or --interval.")
        sys.exit(1)
    if expression is not None and interval_seconds is not None:
        console.print("[red]Error:[/red] Provide either --cron or --interval, not both.")
        sys.exit(1)

    feature = find_feature_or_exit(feature_id)

    # Validate
    try:
        if expression is not None:
            validate_cron_expression(expression, tz)
        else:
            validate_interval(interval_seconds)
    except (ValueError, RuntimeError) as e:
        console.print(f"[red]Invalid schedule:[/red] {e}")
        sys.exit(1)

    store = ScheduleStore()
    try:
        sched = Schedule.create(
            feature_id=feature.id,
            feature_slug=feature.slug,
            board=feature.board,
            expression=expression,
            interval_seconds=interval_seconds,
            tz=tz,
            conflict_policy=conflict,
            catch_up_policy=catch_up,
        )
    except Exception as e:
        console.print(f"[red]Failed to create schedule:[/red] {e}")
        sys.exit(1)

    store.save(sched)
    console.print(f"[green]Schedule created:[/green] {sched.id}")
    console.print(f"  Feature:     {feature.slug}")
    if expression:
        console.print(f"  Cron:        {expression} (tz: {tz})")
    else:
        console.print(f"  Interval:    {interval_seconds}s")
    console.print(f"  Next run at: {sched.next_run_at}")


@schedule.command("list")
@click.option("--feature", default=None, help="Filter by feature ID or slug")
def schedule_list(feature: str):
    """List all schedules."""
    store = ScheduleStore()
    schedules = store.all()

    if feature:
        f = find_feature_or_exit(feature)
        schedules = [s for s in schedules if s.feature_id == f.id]

    if not schedules:
        console.print("[dim]No schedules found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=10)
    table.add_column("Feature")
    table.add_column("Expression/Interval")
    table.add_column("Status")
    table.add_column("Next Run At")
    table.add_column("Last Status")

    for s in schedules:
        expr_display = s.expression or f"{s.interval_seconds}s"
        status_color = "green" if s.status == "active" else "yellow"
        table.add_row(
            s.id[:10],
            s.feature_slug,
            expr_display,
            f"[{status_color}]{s.status}[/{status_color}]",
            s.next_run_at or "-",
            s.last_run_status or "-",
        )

    console.print(table)


@schedule.command("show")
@click.argument("schedule_id")
def schedule_show(schedule_id: str):
    """Show details of a schedule."""
    store = ScheduleStore()
    sched = store.get(schedule_id)
    if sched is None:
        # Try prefix match
        all_scheds = store.all()
        matches = [s for s in all_scheds if s.id.startswith(schedule_id)]
        if len(matches) == 1:
            sched = matches[0]
        elif len(matches) > 1:
            console.print(f"[red]Ambiguous schedule ID prefix: {schedule_id}[/red]")
            sys.exit(1)

    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        sys.exit(1)

    expr_display = sched.expression or f"every {sched.interval_seconds}s"
    console.print(Panel(
        f"[bold]Schedule {sched.id}[/bold]\n"
        f"Feature:          {sched.feature_slug} ({sched.feature_id})\n"
        f"Board:            {sched.board}\n"
        f"Expression:       {expr_display}\n"
        f"Timezone:         {sched.timezone}\n"
        f"Status:           {sched.status}\n"
        f"Conflict policy:  {sched.conflict_policy}\n"
        f"Catch-up policy:  {sched.catch_up_policy}\n"
        f"Next run at:      {sched.next_run_at or '-'}\n"
        f"Last run at:      {sched.last_run_at or '-'}\n"
        f"Last run status:  {sched.last_run_status or '-'}\n"
        f"Created at:       {sched.created_at}\n"
        f"Updated at:       {sched.updated_at}",
        border_style="cyan",
        title="Schedule Details",
    ))


@schedule.command("pause")
@click.argument("schedule_id")
def schedule_pause(schedule_id: str):
    """Pause a schedule (no runs will be triggered while paused)."""
    store = ScheduleStore()
    sched = _find_schedule_or_exit(store, schedule_id)
    if sched.status == "paused":
        console.print(f"[yellow]Schedule {sched.id[:10]} is already paused.[/yellow]")
        return
    sched.status = "paused"
    sched.updated_at = _now_iso()
    store.save(sched)
    console.print(f"[yellow]Paused:[/yellow] schedule {sched.id[:10]} for {sched.feature_slug}")


@schedule.command("resume")
@click.argument("schedule_id")
def schedule_resume(schedule_id: str):
    """Resume a paused schedule. Resumes from the next future slot."""
    store = ScheduleStore()
    sched = _find_schedule_or_exit(store, schedule_id)
    if sched.status == "active":
        console.print(f"[yellow]Schedule {sched.id[:10]} is already active.[/yellow]")
        return
    sched.status = "active"
    # Ensure next_run_at is in the future
    from schedule import _now_utc, _parse_iso
    if sched.next_run_at and _parse_iso(sched.next_run_at) <= _now_utc():
        sched.advance()
    else:
        sched.updated_at = _now_iso()
    store.save(sched)
    console.print(
        f"[green]Resumed:[/green] schedule {sched.id[:10]} for {sched.feature_slug}. "
        f"Next run: {sched.next_run_at}"
    )


@schedule.command("delete")
@click.argument("schedule_id")
def schedule_delete(schedule_id: str):
    """Delete a schedule. The pipeline/feature is not affected."""
    store = ScheduleStore()
    sched = _find_schedule_or_exit(store, schedule_id)
    store.delete(sched.id)
    console.print(f"[red]Deleted:[/red] schedule {sched.id[:10]} for {sched.feature_slug}")


@schedule.command("daemon")
@click.option("--interval", default=30, type=int, help="Poll interval in seconds (default: 30)")
def schedule_daemon(interval: int):
    """Run the scheduler daemon — triggers scheduled pipeline runs.

    Polls all active schedules every INTERVAL seconds and runs any that are due.
    Press Ctrl+C to stop.
    """
    config = get_config()
    runner = AgentRunner(config)
    daemon = SchedulerDaemon(
        runner=runner,
        schedule_store=ScheduleStore(),
        run_store=RunStore(),
        poll_interval=interval,
        console=console,
    )
    console.print(Panel(
        f"[bold]Scheduler daemon[/bold]\n"
        f"Polling every {interval} seconds.\n"
        f"Press Ctrl+C to stop.",
        border_style="cyan",
    ))
    daemon.run()


def _find_schedule_or_exit(store: ScheduleStore, schedule_id: str) -> Schedule:
    """Find a schedule by full ID or prefix, or exit with error."""
    sched = store.get(schedule_id)
    if sched is None:
        all_scheds = store.all()
        matches = [s for s in all_scheds if s.id.startswith(schedule_id)]
        if len(matches) == 1:
            sched = matches[0]
        elif len(matches) > 1:
            console.print(f"[red]Ambiguous schedule ID prefix:[/red] {schedule_id}")
            sys.exit(1)
    if sched is None:
        console.print(f"[red]Schedule not found:[/red] {schedule_id}")
        sys.exit(1)
    return sched


# ---------------------------------------------------------------------------
# pipeline runs
# ---------------------------------------------------------------------------

@cli.group("runs")
def runs_group():
    """Query pipeline run history."""
    pass


@runs_group.command("list")
@click.option("--feature", default=None, help="Filter by feature ID or slug")
@click.option("--limit", default=20, type=int, help="Maximum number of runs to show (default: 20)")
def runs_list(feature: str, limit: int):
    """List recent pipeline runs."""
    store = RunStore()
    all_runs = store.all()

    if feature:
        f = find_feature_or_exit(feature)
        all_runs = [r for r in all_runs if r.feature_id == f.id]

    # Most recent first
    all_runs.sort(key=lambda r: r.started_at, reverse=True)
    all_runs = all_runs[:limit]

    if not all_runs:
        console.print("[dim]No run records found.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", width=10)
    table.add_column("Feature")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("Started At")
    table.add_column("Ended At")

    for r in all_runs:
        source_color = "blue" if r.trigger_source == "scheduled" else "dim"
        status_color = "green" if r.status == "success" else (
            "red" if r.status == "failed" else "yellow"
        )
        table.add_row(
            r.id[:10],
            r.feature_slug,
            f"[{source_color}]{r.trigger_source}[/{source_color}]",
            f"[{status_color}]{r.status}[/{status_color}]",
            r.started_at,
            r.ended_at or "-",
        )

    console.print(table)


@runs_group.command("show")
@click.argument("run_id")
def runs_show(run_id: str):
    """Show details of a single run record."""
    store = RunStore()
    run = store.get(run_id)
    if run is None:
        all_runs = store.all()
        matches = [r for r in all_runs if r.id.startswith(run_id)]
        if len(matches) == 1:
            run = matches[0]
        elif len(matches) > 1:
            console.print(f"[red]Ambiguous run ID prefix:[/red] {run_id}")
            sys.exit(1)
    if run is None:
        console.print(f"[red]Run not found:[/red] {run_id}")
        sys.exit(1)

    console.print(Panel(
        f"[bold]Run {run.id}[/bold]\n"
        f"Feature:                {run.feature_slug} ({run.feature_id})\n"
        f"Trigger source:         {run.trigger_source}\n"
        f"Schedule ID:            {run.schedule_id or '-'}\n"
        f"Scheduled trigger time: {run.scheduled_trigger_time or '-'}\n"
        f"Started at:             {run.started_at}\n"
        f"Ended at:               {run.ended_at or '-'}\n"
        f"Status:                 {run.status}\n"
        f"Error:                  {run.error or '-'}",
        border_style="cyan",
        title="Run Details",
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
