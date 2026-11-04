"""Production Typer CLI for DC-Agent."""

import asyncio
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from dc_agent.adapters.base import TestCase
from dc_agent.adapters.codeforces import CodeforcesAdapter
from dc_agent.adapters.leetcode import LeetCodeAdapter
from dc_agent.config import get_settings
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.curriculum.engine import CurriculumEngine
from dc_agent.curriculum.models import TIER_BOUNDS, CurriculumTier, ProgressionState
from dc_agent.db.models import DailySession
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.executor.sandbox import Sandbox
from dc_agent.providers.factory import get_ai_provider
from dc_agent.scheduler.service import SchedulerService

app = typer.Typer(
    name="dc-agent",
    help="DC-Agent: Daily Competitive Programming Assistant powered by Gemini.",
    no_args_is_help=True,
)
leetcode_app = typer.Typer(help="LeetCode specific commands.")
codeforces_app = typer.Typer(help="Codeforces specific commands.")

app.add_typer(leetcode_app, name="leetcode")
app.add_typer(codeforces_app, name="codeforces")

console = Console()


@app.command()
def init() -> None:
    """Initialize database, default configuration, and workspace directories."""
    settings = get_settings()
    init_db(settings.database_url)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)

    with get_db() as db:
        repo = Repository(db)
        repo.get_or_create_user("leetcode", settings.leetcode_username)
        repo.get_or_create_user("codeforces", settings.codeforces_username)

    console.print(
        Panel.fit(
            f"[bold green]✔ DC-Agent Initialized Successfully![/bold green]\n"
            f"[cyan]Database:[/cyan] {settings.database_url}\n"
            f"[cyan]Workspace:[/cyan] {settings.workspace_dir.resolve()}\n"
            f"[cyan]Configured LeetCode:[/cyan] {settings.leetcode_username}\n"
            f"[cyan]Configured Codeforces:[/cyan] {settings.codeforces_username}",
            title="Initialization Complete",
        )
    )


@app.command()
def config() -> None:
    """Display active configuration settings and masked secrets."""
    settings = get_settings()
    table = Table(title="DC-Agent Active Configuration", border_style="cyan")
    table.add_column("Setting", style="bold white")
    table.add_column("Configured Value", style="green")

    table.add_row("AI Provider", settings.ai_provider)
    table.add_row("Gemini Model", settings.gemini_model)
    table.add_row("Gemini API Key", settings.get_masked_gemini_key())
    table.add_row("LeetCode Handle", settings.leetcode_username)
    table.add_row("Codeforces Handle", settings.codeforces_username)
    table.add_row("Daily Policy", settings.daily_policy)
    table.add_row("Scheduled Run Time", f"{settings.daily_run_time} ({settings.timezone})")
    table.add_row("Sandbox Backend", settings.sandbox_backend)
    table.add_row("Execution Timeout", f"{settings.execution_timeout}s")
    table.add_row("Memory Limit", f"{settings.memory_limit_mb} MB")
    table.add_row("Database URL", settings.database_url)

    console.print(table)


@app.command()
def doctor() -> None:
    """Run comprehensive system health checks across all subsystems."""
    settings = get_settings()
    console.print("[bold cyan]Running DC-Agent System Doctor...[/bold cyan]\n")

    checks_passed = 0
    total_checks = 5

    # 1. Database Check
    try:
        init_db(settings.database_url)
        with get_db() as db:
            repo = Repository(db)
            repo.get_or_create_user("codeforces", settings.codeforces_username)
        console.print("[green]✔ Database Connection & Models:[/green] Operational (WAL enabled)")
        checks_passed += 1
    except Exception as e:
        console.print(f"[red]✘ Database Error:[/red] {e}")

    # 2. AI Provider Check
    try:
        provider = get_ai_provider(settings=settings)
        healthy = asyncio.run(provider.health_check())
        if healthy:
            console.print(
                f"[green]✔ AI Provider ({provider.provider_name} / {provider.model_name}):[/green] Connected & responsive"
            )
            checks_passed += 1
        else:
            console.print(
                f"[yellow]⚠ AI Provider ({provider.provider_name}):[/yellow] Authentication or quota issue"
            )
    except Exception as e:
        console.print(f"[red]✘ AI Provider Error:[/red] {e}")

    # 3. Codeforces API Check
    try:
        cf = CodeforcesAdapter()
        user_info = asyncio.run(cf.fetch_user_info(settings.codeforces_username))
        rating = user_info.get("rating", "Unrated")
        rank = user_info.get("rank", "None")
        console.print(
            f"[green]✔ Codeforces API:[/green] Reachable (User {settings.codeforces_username}: Rank {rank}, Rating {rating})"
        )
        checks_passed += 1
    except Exception as e:
        console.print(f"[yellow]⚠ Codeforces API Warning:[/yellow] {e}")

    # 4. Sandbox Check
    sandbox = Sandbox(settings=settings)
    active_backend = sandbox.get_active_backend_name()
    if active_backend == "docker":
        console.print("[green]✔ Execution Sandbox:[/green] Docker containerized isolation active")
    else:
        console.print(
            f"[yellow]⚠ Execution Sandbox:[/yellow] Subprocess fallback active ({sandbox.get_active_backend_name()})"
        )
    checks_passed += 1

    # 5. Workspace Directory Check
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    if os.access(settings.workspace_dir, os.W_OK):
        console.print(
            f"[green]✔ Workspace Directory:[/green] Writable ({settings.workspace_dir.resolve()})"
        )
        checks_passed += 1
    else:
        console.print(f"[red]✘ Workspace Directory:[/red] Cannot write to {settings.workspace_dir}")

    console.print(f"\n[bold]{checks_passed}/{total_checks} Checks Passed.[/bold]")


@app.command()
def status() -> None:
    """Display today's problem, rating progress, and retry budget."""
    settings = get_settings()
    cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db() as db:
        repo = Repository(db)
        lc_session = repo.get_daily_session("leetcode", cycle_date)
        cf_session = repo.get_daily_session("codeforces", cycle_date)
        session = lc_session or cf_session

        prog = repo.get_curriculum_progress(settings.codeforces_username)

        # Status Table
        table = Table(title=f"DC-Agent Status :: {cycle_date}", border_style="cyan")
        table.add_column("Field", style="bold white")
        table.add_column("Value", style="green")

        if session:
            table.add_row("Today's Platform", session.platform.upper())
            table.add_row("Today's Problem", session.problem.title)
            table.add_row("Problem URL", session.problem.url)
            table.add_row("Session Status", session.status)
            table.add_row("Attempts Made", f"{session.retry_count}/{session.retry_budget}")
        else:
            table.add_row("Today's Session", "Not yet started. Run: dc-agent daily")

        tier_enum = CurriculumTier(prog.current_tier)
        low, high = TIER_BOUNDS[tier_enum]
        table.add_row(
            "Codeforces Tier", f"Tier {prog.current_tier} ({tier_enum.name}) [{low}–{high}]"
        )
        table.add_row("Target Rating", str(prog.current_rating_target))
        table.add_row("Consecutive Solved", f"{prog.consecutive_accepted}/3 toward promotion")

        console.print(table)


@app.command()
def daily(
    platform: str = typer.Option(
        None,
        "--platform",
        "-p",
        help="Override platform for today: 'leetcode' or 'codeforces'",
    ),
) -> None:
    """Start or resume today's challenge."""
    if platform:
        p_norm = platform.lower().strip()
        if p_norm not in ("leetcode", "codeforces"):
            console.print(
                f"[red]Error:[/red] Unsupported platform '{platform}'. "
                "Choose 'leetcode' or 'codeforces'."
            )
            raise typer.Exit(code=1)

    console.print("[bold cyan]🚀 Starting Daily Competitive Programming Challenge...[/bold cyan]\n")
    orchestrator = DailyOrchestrator()

    try:
        res = asyncio.run(orchestrator.run_daily(force_platform=platform))
        status_val = res.get("status")

        color = "green" if status_val in ("LOCAL_PASSED", "ACCEPTED") else "yellow"
        console.print(
            Panel.fit(
                f"[{color}]Status: {status_val}[/{color}]\n"
                f"[bold white]Platform:[/bold white] {res.get('platform', '').upper()}\n"
                f"[bold white]Problem:[/bold white] {res.get('problem_title')}\n"
                f"[bold white]URL:[/bold white] {res.get('problem_url')}\n"
                f"[bold white]Workspace:[/bold white] {res.get('workspace_dir')}\n"
                f"[bold white]Local Tests Passed:[/bold white] {res.get('tests_passed')}/{res.get('total_tests')}\n"
                f"[bold white]AI Corrections Applied:[/bold white] {res.get('corrections', 0)}\n\n"
                f"[dim]Next step: Review code in workspace and submit on platform web interface.[/dim]",
                title="Daily Session Summary",
            )
        )
    except Exception as e:
        console.print(f"[red]Error executing daily challenge:[/red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def today() -> None:
    """View today's problem and generated solution code."""
    get_settings()
    cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session("leetcode", cycle_date) or repo.get_daily_session(
            "codeforces", cycle_date
        )

        if not session:
            console.print(
                f"[yellow]No session active for {cycle_date}. Run 'dc-agent daily' to begin.[/yellow]"
            )
            return

        latest_attempt = session.attempts[-1] if session.attempts else None

        console.print(
            f"[bold cyan]Today's Problem:[/bold cyan] {session.problem.title} ({session.platform.upper()})"
        )
        console.print(f"[bold cyan]URL:[/bold cyan] {session.problem.url}")
        console.print(f"[bold cyan]Status:[/bold cyan] {session.status}\n")

        if latest_attempt:
            syntax = Syntax(
                latest_attempt.source_code, "python", theme="monokai", line_numbers=True
            )
            console.print(syntax)
        else:
            console.print("[yellow]No solution attempts recorded yet.[/yellow]")


@leetcode_app.command(name="set-daily")
def leetcode_set_daily(
    url: str = typer.Option(None, "--url", "-u", help="Official LeetCode problem URL"),
    slug: str = typer.Option(None, "--slug", "-s", help="LeetCode problem slug"),
) -> None:
    """Set or confirm the official LeetCode Daily Challenge URL or slug."""
    if not url and not slug:
        url = typer.prompt("Enter official LeetCode Daily Challenge URL")

    adapter = LeetCodeAdapter()
    problem_slug = adapter.normalize_slug(url or slug or "")

    if not problem_slug:
        console.print("[red]Invalid problem URL or slug provided.[/red]")
        raise typer.Exit(code=1)

    problem_url = adapter.get_problem_url(problem_slug)
    cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db() as db:
        repo = Repository(db)
        problem = repo.create_or_get_problem(
            platform="leetcode",
            external_id=problem_slug,
            title=problem_slug.replace("-", " ").title(),
            slug=problem_slug,
            url=problem_url,
            difficulty_label="Medium",
        )
        settings = get_settings()
        session = repo.get_daily_session("leetcode", cycle_date)
        if not session:
            session = repo.create_daily_session(
                platform="leetcode",
                handle=settings.leetcode_username,
                cycle_date=cycle_date,
                problem=problem,
            )
        else:
            session.problem_id = problem.id

    console.print(f"[green]✔ LeetCode Daily Challenge successfully set to:[/green] {problem_url}")
    console.print(
        "[dim]Run 'dc-agent solve' or 'dc-agent daily --platform leetcode' to proceed.[/dim]"
    )


@codeforces_app.command(name="profile")
def codeforces_profile() -> None:
    """Fetch and display official Codeforces user profile information."""
    settings = get_settings()
    cf = CodeforcesAdapter()
    try:
        info = asyncio.run(cf.fetch_user_info(settings.codeforces_username))
        table = Table(
            title=f"Codeforces Profile :: {settings.codeforces_username}", border_style="cyan"
        )
        table.add_column("Property", style="bold white")
        table.add_column("Value", style="green")

        table.add_row("Handle", info.get("handle", ""))
        table.add_row("Rating", str(info.get("rating", "Unrated")))
        table.add_row("Max Rating", str(info.get("maxRating", "Unrated")))
        table.add_row("Rank", str(info.get("rank", "Unrated")))
        table.add_row("Max Rank", str(info.get("maxRank", "Unrated")))
        table.add_row("Contribution", str(info.get("contribution", 0)))

        console.print(table)
    except Exception as e:
        console.print(f"[red]Error fetching Codeforces profile:[/red] {e}")


@codeforces_app.command(name="progress")
def codeforces_progress() -> None:
    """Display Codeforces curriculum tiers and current progression state."""
    settings = get_settings()
    with get_db() as db:
        repo = Repository(db)
        prog = repo.get_curriculum_progress(settings.codeforces_username)

        table = Table(title="Codeforces Curriculum Progress", border_style="cyan")
        table.add_column("Tier", style="bold white")
        table.add_column("Rating Band", style="cyan")
        table.add_column("Status", style="green")

        for tier in CurriculumTier:
            low, high = TIER_BOUNDS[tier]
            is_active = tier.value == prog.current_tier
            status_text = (
                f"★ CURRENT (Target: {prog.current_rating_target})" if is_active else "Unlocked"
            )
            style = "bold green" if is_active else "dim"
            table.add_row(
                f"Tier {tier.value}: {tier.name}", f"{low}–{high}", status_text, style=style
            )

        console.print(table)
        console.print(
            f"\n[cyan]Consecutive Accepted:[/cyan] {prog.consecutive_accepted}/3 toward next promotion (+100)"
        )
        console.print(
            f"[cyan]Consecutive Failed:[/cyan] {prog.consecutive_failed}/2 toward demotion (-100)"
        )


@app.command()
def test() -> None:
    """Execute local sandbox tests against the current solution."""
    settings = get_settings()
    cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session("leetcode", cycle_date) or repo.get_daily_session(
            "codeforces", cycle_date
        )

        if not session or not session.attempts:
            console.print(
                "[yellow]No solution attempt recorded to test. Run 'dc-agent daily' first.[/yellow]"
            )
            return

        latest = session.attempts[-1]
        sandbox = Sandbox(settings=settings)
        test_cases = [
            TestCase(input_data=tc.input_data, expected_output=tc.expected_output)
            for tc in session.problem.test_cases
        ]

        summary = sandbox.run_tests(latest.source_code, test_cases)
        console.print(
            f"[bold cyan]Local Test Results ({summary.backend_used}):[/bold cyan] {summary.overall_verdict}"
        )
        console.print(
            f"Passed: {summary.passed_tests}/{summary.total_tests} (Time: {summary.total_time_ms:.1f}ms)\n"
        )

        for r in summary.results:
            icon = "✔" if r.verdict == "PASS" else "✘"
            color = "green" if r.verdict == "PASS" else "red"
            console.print(
                f"[{color}]{icon} Test #{r.test_index}: {r.verdict}[/{color}] ({r.execution_time_ms:.1f}ms)"
            )
            if r.error_message:
                console.print(f"   [dim]{r.error_message}[/dim]")


@app.command()
def record_verdict(
    verdict: str = typer.Option(
        ..., "--verdict", "-v", help="Platform verdict: ACCEPTED, WRONG_ANSWER, TLE, MLE"
    ),
    session_id: int = typer.Option(None, "--session-id", "-s", help="Specific session ID"),
) -> None:
    """Record an official platform verdict confirmed by the user."""
    v_norm = verdict.upper().strip()
    with get_db() as db:
        repo = Repository(db)
        if session_id:
            session = db.get(DailySession, session_id)
        else:
            cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")
            session = repo.get_daily_session("leetcode", cycle_date) or repo.get_daily_session(
                "codeforces", cycle_date
            )

        if not session:
            console.print("[red]No active session found to record verdict for.[/red]")
            raise typer.Exit(code=1)

        if v_norm == "ACCEPTED":
            repo.mark_session_accepted(session)
            console.print(
                f"[bold green]✔ Marked session #{session.id} ({session.problem.title}) as ACCEPTED![/bold green]"
            )

            # Update progression if codeforces
            if session.platform.lower() == "codeforces":
                settings = get_settings()
                prog = repo.get_curriculum_progress(settings.codeforces_username)
                state = ProgressionState(
                    handle=settings.codeforces_username,
                    current_tier=CurriculumTier(prog.current_tier),
                    current_rating_target=prog.current_rating_target,
                    consecutive_accepted=prog.consecutive_accepted,
                    consecutive_failed=prog.consecutive_failed,
                )
                event = CurriculumEngine.record_accepted(state)
                prog.current_tier = state.current_tier.value
                prog.current_rating_target = state.current_rating_target
                prog.consecutive_accepted = state.consecutive_accepted
                prog.consecutive_failed = state.consecutive_failed
                db.commit()

                if event.promoted:
                    console.print(
                        f"[bold magenta]🎉 PROMOTION! Promoted to {event.new_tier.name} (Target: {event.new_rating})[/bold magenta]"
                    )
        else:
            session.status = "REJECTED"
            console.print(f"[yellow]Recorded verdict {v_norm} for session #{session.id}.[/yellow]")


@app.command()
def verify() -> None:
    """Poll official platform APIs to verify whether manual submission was Accepted."""
    settings = get_settings()
    cycle_date = datetime.now(UTC).strftime("%Y-%m-%d")

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session("leetcode", cycle_date) or repo.get_daily_session(
            "codeforces", cycle_date
        )

        if not session:
            console.print("[yellow]No active session today to verify.[/yellow]")
            return

        if session.platform.lower() == "codeforces":
            console.print(
                f"[cyan]Querying Codeforces API for {settings.codeforces_username} submissions...[/cyan]"
            )
            cf = CodeforcesAdapter()
            # Extract contest and index
            parts = session.problem.external_id.split("_")
            if len(parts) == 2 and parts[0].isdigit():
                contest_id = int(parts[0])
                idx = parts[1]
                sub = asyncio.run(
                    cf.check_problem_verdict(settings.codeforces_username, contest_id, idx)
                )
                if sub:
                    repo.mark_session_accepted(session, submission_id=sub.submission_id)
                    console.print(
                        f"[bold green]✔ Verified Accepted on Codeforces! (Submission #{sub.submission_id})[/bold green]"
                    )
                    return
            console.print(
                "[yellow]No Accepted submission detected yet on Codeforces. Check submission status on site.[/yellow]"
            )

        elif session.platform.lower() == "leetcode":
            console.print(
                f"[cyan]Querying LeetCode public submissions for {settings.leetcode_username}...[/cyan]"
            )
            lc = LeetCodeAdapter()
            sub = asyncio.run(
                lc.check_recent_submission(settings.leetcode_username, session.problem.slug)
            )
            if sub:
                repo.mark_session_accepted(session, submission_id=sub.submission_id)
                console.print(
                    f"[bold green]✔ Verified Accepted on LeetCode! (Slug: {sub.problem_identifier})[/bold green]"
                )
                return
            console.print(
                "[yellow]No Accepted submission detected yet on LeetCode public profile. You can record it manually with 'dc-agent record-verdict -v ACCEPTED'.[/yellow]"
            )


@app.command()
def history(
    limit: int = typer.Option(15, "--limit", "-n", help="Number of past sessions to display"),
) -> None:
    """Display history of past daily problem-solving sessions."""
    with get_db() as db:
        repo = Repository(db)
        sessions = repo.get_recent_sessions(limit=limit)

        if not sessions:
            console.print("[yellow]No sessions recorded yet.[/yellow]")
            return

        table = Table(title="DC-Agent Session History", border_style="cyan")
        table.add_column("Date", style="bold white")
        table.add_column("Platform", style="cyan")
        table.add_column("Problem", style="white")
        table.add_column("Difficulty", style="yellow")
        table.add_column("Status", style="green")
        table.add_column("Attempts", style="magenta")

        for s in sessions:
            table.add_row(
                s.cycle_date,
                s.platform.upper(),
                s.problem.title,
                s.problem.difficulty_label,
                s.status,
                str(s.retry_count),
            )

        console.print(table)


@app.command()
def stats() -> None:
    """Display aggregate statistics, streaks, and solve ratios."""
    with get_db() as db:
        repo = Repository(db)
        sessions = repo.get_recent_sessions(limit=100)
        total = len(sessions)
        accepted = sum(1 for s in sessions if s.status == "ACCEPTED")

        rate = (accepted / total * 100.0) if total > 0 else 0.0

        panel = Panel.fit(
            f"[bold white]Total Daily Challenges:[/bold white] {total}\n"
            f"[bold green]Accepted Solutions:[/bold green] {accepted}\n"
            f"[bold cyan]Success Rate:[/bold cyan] {rate:.1f}%\n",
            title="DC-Agent Performance Statistics",
        )
        console.print(panel)


@app.command()
def schedule() -> None:
    """Start the persistent background scheduler or inspect its status."""
    service = SchedulerService()
    status_info = service.get_status()

    console.print(
        Panel.fit(
            f"[bold green]Scheduler Configured[/bold green]\n"
            f"[cyan]Daily Target Time:[/cyan] {status_info['scheduled_time']} ({status_info['timezone']})\n"
            f"[cyan]Status:[/cyan] Ready\n\n"
            f"[dim]The scheduler fires automatically at {status_info['scheduled_time']}. "
            "To keep running in foreground, press Ctrl+C to stop.[/dim]",
            title="Scheduler Status",
        )
    )

    try:
        service.start()
        while True:
            import time

            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        service.shutdown()
        console.print("\n[yellow]Scheduler stopped.[/yellow]")


@app.command()
def backup(
    dest: Path = typer.Option(None, "--dest", "-d", help="Backup destination directory"),
) -> None:
    """Create a timestamped backup of the local SQLite database."""
    settings = get_settings()
    db_file = Path(settings.database_url.replace("sqlite:///", ""))
    if not db_file.exists():
        console.print(f"[red]Database file {db_file} does not exist.[/red]")
        raise typer.Exit(code=1)

    backup_dir = dest or Path("backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_target = backup_dir / f"dc_agent_backup_{timestamp}.db"
    shutil.copy2(db_file, backup_target)

    console.print(
        f"[bold green]✔ Database backed up successfully to:[/bold green] {backup_target.resolve()}"
    )


@app.command()
def reset(
    curriculum: bool = typer.Option(
        False, "--curriculum", help="Reset Codeforces curriculum progression back to 800"
    ),
) -> None:
    """Reset curriculum progression or clean scratch cache."""
    settings = get_settings()
    if curriculum:
        with get_db() as db:
            repo = Repository(db)
            prog = repo.get_curriculum_progress(settings.codeforces_username)
            prog.current_tier = 1
            prog.current_rating_target = 800
            prog.consecutive_accepted = 0
            prog.consecutive_failed = 0
            db.commit()
        console.print(
            f"[bold green]✔ Codeforces curriculum reset to Tier 1 (Rating 800) for {settings.codeforces_username}.[/bold green]"
        )
    else:
        console.print("[yellow]Specify '--curriculum' to reset progression.[/yellow]")


@app.command()
def dashboard(
    host: str = typer.Option(
        "127.0.0.1", "--host", "-h", help="Host interface (localhost only recommended)"
    ),
    port: int = typer.Option(8000, "--port", "-p", help="Port number"),
) -> None:
    """Launch the local FastAPI dashboard."""
    console.print(f"[bold green]Launching DC-Agent Dashboard at http://{host}:{port}[/bold green]")
    uvicorn.run("dc_agent.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
