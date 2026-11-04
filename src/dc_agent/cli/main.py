"""Production Typer CLI for DC-Agent."""

import asyncio
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Ensure UTF-8 output encoding across legacy Windows consoles
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import typer
import uvicorn
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from dc_agent.adapters.base import TestCase
from dc_agent.adapters.codeforces import CodeforcesAdapter
from dc_agent.adapters.leetcode import LeetCodeAdapter
from dc_agent.config import get_canonical_cycle_date, get_settings
from dc_agent.core.cloud_runner import CloudRunner
from dc_agent.core.orchestrator import DailyOrchestrator
from dc_agent.curriculum.engine import CurriculumEngine
from dc_agent.curriculum.models import TIER_BOUNDS, CurriculumTier, ProgressionState
from dc_agent.db.models import DailySession
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db, init_db
from dc_agent.executor.base import LocalExecutionVerdict
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
    table.add_row("LeetCode Session", settings.get_masked_leetcode_session())
    table.add_row("LeetCode CSRF Token", settings.get_masked_leetcode_csrf())
    table.add_row("Codeforces Handle", settings.codeforces_username)
    table.add_row("Daily Policy", settings.daily_policy)
    table.add_row("Scheduled Run Time", f"{settings.daily_run_time} ({settings.timezone})")
    table.add_row("Sandbox Backend", settings.sandbox_backend)
    table.add_row("Allow Insecure Fallback", str(settings.allow_insecure_fallback))
    table.add_row("Execution Timeout", f"{settings.execution_timeout}s")
    table.add_row("Memory Limit", f"{settings.memory_limit_mb} MB")
    table.add_row("Database URL", settings.database_url)

    console.print(table)


@app.command()
def status() -> None:
    """Display current system status and sandbox security capabilities."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    sandbox = Sandbox(settings=settings)
    caps = sandbox.get_security_capabilities()

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)
        prog = repo.get_curriculum_progress(settings.codeforces_username)

        console.print(
            Panel.fit(
                f"[bold white]Canonical Cycle Date (IST):[/bold white] [cyan]{cycle_date}[/cyan]\n"
                f"[bold white]Active Daily Session:[/bold white] "
                f"{session.problem.title + ' (' + session.platform.upper() + ') - ' + session.status if session else '[dim]None initialized yet[/dim]'}\n"
                f"[bold white]Codeforces Rating Target:[/bold white] {prog.current_rating_target} (Tier {prog.current_tier})\n"
                f"[bold white]Consecutive Solved / Failed:[/bold white] {prog.consecutive_accepted}/3 AC | {prog.consecutive_failed}/2 Fails",
                title="Daily Session & Curriculum Status",
                border_style="cyan",
            )
        )

        console.print(
            Panel.fit(
                f"[bold white]Active Backend:[/bold white] {caps['active_backend'].upper()}\n"
                f"[bold white]Configured Backend:[/bold white] {caps['configured_backend']}\n"
                f"[bold white]Docker Daemon Reachable:[/bold white] {'[green]Yes[/green]' if caps['docker_available'] else '[yellow]No[/yellow]'}\n"
                f"[bold white]Insecure Fallback Allowed:[/bold white] {'[yellow]Yes[/yellow]' if caps['allow_insecure_fallback'] else '[green]No (Strict)[/green]'}\n"
                f"[bold white]Memory Limit:[/bold white] {caps['memory_limit_mb']} MB | [bold white]Timeout:[/bold white] {caps['timeout_seconds']}s\n\n"
                f"[bold white]Isolation Guarantee:[/bold white]\n{caps['isolation_summary']}",
                title="Sandbox Security & Capability",
                border_style="magenta",
            )
        )


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

    console.print(f"\n[bold]Doctor Results:[/bold] {checks_passed}/{total_checks} checks passed.")


@app.command()
def curriculum() -> None:
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
def daily(
    platform: str = typer.Option(
        None,
        "--platform",
        "-p",
        help="Override platform for today: 'leetcode' or 'codeforces'",
    ),
    allow_insecure_fallback: bool = typer.Option(
        False,
        "--allow-insecure-fallback",
        help="Allow fallback to subprocess runner if Docker is unavailable",
    ),
) -> None:
    """Start or resume today's challenge."""
    settings = get_settings()
    if allow_insecure_fallback:
        settings.allow_insecure_fallback = True

    if platform:
        p_norm = platform.lower().strip()
        if p_norm not in ("leetcode", "codeforces"):
            console.print(
                f"[red]Error:[/red] Unsupported platform '{platform}'. "
                "Choose 'leetcode' or 'codeforces'."
            )
            raise typer.Exit(code=1)

    console.print("[bold cyan]🚀 Starting Daily Competitive Programming Challenge...[/bold cyan]\n")
    orchestrator = DailyOrchestrator(settings=settings)

    try:
        res = asyncio.run(orchestrator.run_daily(force_platform=platform))
        status_val = res.get("status")

        if status_val == "STATEMENT_REQUIRED":
            console.print(
                Panel.fit(
                    f"[bold yellow]Problem Statement Required![/bold yellow]\n\n"
                    f"[bold white]Problem:[/bold white] {res.get('problem_title')}\n"
                    f"[bold white]Official URL:[/bold white] {res.get('problem_url')}\n"
                    f"[bold white]Workspace:[/bold white] {res.get('workspace_dir')}\n\n"
                    f"[dim]{res.get('message')}[/dim]",
                    title="Action Required",
                    border_style="yellow",
                )
            )
            return

        color = "green" if status_val in ("LOCAL_PASSED", "ACCEPTED") else "yellow"
        unverified_text = (
            f" ({res.get('unverified_tests', 0)} unverified)" if res.get("unverified_tests") else ""
        )
        console.print(
            Panel.fit(
                f"[{color}]Status: {status_val}[/{color}]\n"
                f"[bold white]Platform:[/bold white] {res.get('platform', '').upper()}\n"
                f"[bold white]Problem:[/bold white] {res.get('problem_title')}\n"
                f"[bold white]URL:[/bold white] {res.get('problem_url')}\n"
                f"[bold white]Workspace:[/bold white] {res.get('workspace_dir')}\n"
                f"[bold white]Local Tests Passed:[/bold white] {res.get('tests_passed')}/{res.get('total_tests')}{unverified_text}\n"
                f"[bold white]AI Corrections Applied:[/bold white] {res.get('corrections', 0)}\n\n"
                f"[dim]Next step: Review code in workspace and submit on platform web interface.[/dim]",
                title="Daily Session Summary",
            )
        )
    except Exception as e:
        console.print(f"[red]Error executing daily challenge:[/red] {e}")
        raise typer.Exit(code=1) from e


@app.command(name="cloud-run")
def cloud_run(
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Validate execution pipeline and local Docker tests without submitting to LeetCode",
    ),
    platform: str = typer.Option(
        None,
        "--platform",
        "-p",
        help="Target platform override (defaults to leetcode)",
    ),
    max_retries: int = typer.Option(
        3,
        "--max-retries",
        help="Maximum platform judge diagnostic retries on Wrong Answer / TLE",
    ),
) -> None:
    """Execute autonomous cloud daily challenge workflow with end-to-end verification."""
    settings = get_settings()
    console.print(
        Panel.fit(
            f"[bold cyan]⚡ DC-Agent Autonomous Cloud Execution[/bold cyan]\n"
            f"[bold white]Mode:[/bold white] {'[yellow]Dry Run[/yellow]' if dry_run else '[green]Live Autonomous Submission[/green]'}\n"
            f"[bold white]Platform:[/bold white] {(platform or 'leetcode').upper()}\n"
            f"[bold white]Account:[/bold white] {settings.leetcode_username}\n"
            f"[bold white]Scheduled Run Target:[/bold white] 5:30 PM ({settings.timezone})",
            title="Cloud Runner Active",
            border_style="cyan",
        )
    )

    runner = CloudRunner(settings=settings)
    try:
        res = asyncio.run(
            runner.execute(
                dry_run=dry_run,
                force_platform=platform,
                max_platform_retries=max_retries,
            )
        )
        status_val = res.get("status")
        is_done = status_val == "DONE"
        color = "green" if is_done else ("yellow" if dry_run else "red")

        console.print(
            Panel.fit(
                f"[{color} bold]Terminal Status: {status_val}[/{color} bold]\n"
                f"[bold white]Problem:[/bold white] {res.get('problem_title')}\n"
                f"[bold white]URL:[/bold white] {res.get('problem_url')}\n"
                f"[bold white]Submission ID:[/bold white] {res.get('submission_id') or 'N/A'}\n"
                f"[bold white]Judge Verdict:[/bold white] {res.get('judge_verdict') or 'N/A'}\n"
                f"[bold white]Calendar Verified:[/bold white] {'[green]Yes (Green Active)[/green]' if res.get('calendar_verified') else '[yellow]Pending / Dry Run[/yellow]'}\n"
                f"[bold white]Streak:[/bold white] {res.get('streak', 0)} days | [bold white]Total Active:[/bold white] {res.get('total_active_days', 0)} days\n\n"
                f"[dim]Detailed execution report persisted at: {res.get('report_file', 'reports/cloud/LIVE_CLOUD_E2E_TEST_REPORT.md')}[/dim]",
                title="Cloud Execution Summary",
                border_style=color,
            )
        )
        if not is_done and not dry_run:
            raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"[bold red]Cloud Runner Execution Error:[/bold red] {e}")
        raise typer.Exit(code=1) from e


@app.command()
def today() -> None:
    """View today's problem and generated solution code."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)

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


@app.command(name="show-solution")
def show_solution() -> None:
    """View today's problem and generated solution code (alias for 'today')."""
    today()


@leetcode_app.command(name="open-profile")
def leetcode_open_profile() -> None:
    """Open official LeetCode user profile in default web browser."""
    import webbrowser

    settings = get_settings()
    url = LeetCodeAdapter.get_profile_url(settings.leetcode_username)
    console.print(f"[cyan]Opening LeetCode profile in browser:[/cyan] {url}")
    webbrowser.open(url)


@leetcode_app.command(name="set-daily")
def leetcode_set_daily(
    url: str = typer.Option(None, "--url", "-u", help="Official LeetCode problem URL"),
    slug: str = typer.Option(None, "--slug", "-s", help="LeetCode problem slug"),
) -> None:
    """Set or confirm the official LeetCode Daily Challenge URL or slug."""
    if not url and not slug:
        url = typer.prompt("Enter official LeetCode Daily Challenge URL")

    adapter = LeetCodeAdapter()
    if url and not adapter.validate_url(url):
        console.print(
            f"[red]Error:[/red] Invalid LeetCode URL '{url}'. Must belong to leetcode.com/problems/<slug>/"
        )
        raise typer.Exit(code=1)

    problem_slug = adapter.normalize_slug(url or slug or "")

    if not problem_slug:
        console.print("[red]Invalid problem URL or slug provided.[/red]")
        raise typer.Exit(code=1)

    problem_url = adapter.get_problem_url(problem_slug)
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

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
        session = repo.get_daily_session_for_date(cycle_date)
        if not session:
            session = repo.create_daily_session(
                platform="leetcode",
                handle=settings.leetcode_username,
                cycle_date=cycle_date,
                problem=problem,
            )
        else:
            session.problem_id = problem.id
            session.platform = "leetcode"
        db.commit()

    console.print(f"[green]✔ LeetCode Daily Challenge successfully set to:[/green] {problem_url}")
    console.print(
        "[dim]Run 'dc-agent solve' or 'dc-agent daily --platform leetcode' to proceed.[/dim]"
    )


@leetcode_app.command(name="verify-calendar")
def leetcode_verify_calendar() -> None:
    """Verify whether today's canonical date (Asia/Kolkata) is marked active on LeetCode calendar."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)
    adapter = LeetCodeAdapter()

    console.print(
        f"[cyan]Verifying activity calendar for user '[bold white]{settings.leetcode_username}[/bold white]' on date {cycle_date} (Asia/Kolkata)...[/cyan]"
    )
    res = asyncio.run(adapter.verify_calendar_activity(settings.leetcode_username, cycle_date))

    if res.is_active:
        console.print(
            Panel.fit(
                f"[bold green]✔ Date {cycle_date} is ACTIVE on LeetCode Calendar![/bold green]\n\n"
                f"[bold white]Submissions Today:[/bold white] {res.submission_count}\n"
                f"[bold white]Current Streak:[/bold white] {res.streak} days\n"
                f"[bold white]Total Active Days:[/bold white] {res.total_active_days} days\n"
                f"[bold white]Profile URL:[/bold white] {LeetCodeAdapter.get_profile_url(settings.leetcode_username)}",
                title="Calendar Verification Succeeded",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[bold yellow]✖ Date {cycle_date} is NOT yet marked active on LeetCode Calendar.[/bold yellow]\n\n"
                f"[bold white]Submissions Today:[/bold white] 0\n"
                f"[bold white]Current Streak:[/bold white] {res.streak} days\n"
                f"[bold white]Total Active Days:[/bold white] {res.total_active_days} days\n"
                f"[dim]Note: If a submission was just accepted, LeetCode cache may take 10-30 seconds to update.[/dim]",
                title="Calendar Verification Pending",
                border_style="yellow",
            )
        )


@leetcode_app.command(name="submit-daily")
def leetcode_submit_daily() -> None:
    """Submit the latest locally verified solution for today's challenge to LeetCode."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    if not settings.leetcode_session:
        console.print(
            "[bold red]Error: LEETCODE_SESSION is not configured in .env or environment secrets.[/bold red]"
        )
        raise typer.Exit(code=1)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)
        if not session or not session.attempts:
            console.print(
                f"[yellow]No verified solution available for {cycle_date}. Run 'dc-agent daily' first.[/yellow]"
            )
            raise typer.Exit(code=1)

        latest = session.attempts[-1]
        problem = session.problem
        adapter = LeetCodeAdapter()

        console.print(
            f"[bold cyan]Submitting solution for {problem.title} (#{problem.internal_question_id})...[/bold cyan]"
        )
        session_cookie = settings.leetcode_session.get_secret_value()
        csrf_token = (
            settings.leetcode_csrf_token.get_secret_value()
            if settings.leetcode_csrf_token
            else None
        )

        try:
            sub_id = asyncio.run(
                adapter.submit_solution(
                    slug=problem.slug,
                    question_id=problem.internal_question_id,
                    typed_code=latest.source_code,
                    session_cookie=session_cookie,
                    csrf_token=csrf_token,
                )
            )
            console.print(f"[green]✔ Submitted successfully! Submission ID: #{sub_id}[/green]")
            console.print("[cyan]Polling judge verdict...[/cyan]")

            verdict_res = asyncio.run(
                adapter.poll_submission_verdict(
                    submission_id=sub_id,
                    session_cookie=session_cookie,
                    csrf_token=csrf_token,
                )
            )

            repo.update_submission_verdict(
                session, submission_id=sub_id, verdict=verdict_res.status_msg
            )
            if verdict_res.is_accepted:
                repo.mark_session_accepted(session, submission_id=sub_id)
                db.commit()
                console.print(
                    Panel.fit(
                        f"[bold green]✔ JUDGE VERDICT: ACCEPTED![/bold green]\n"
                        f"[bold white]Runtime:[/bold white] {verdict_res.runtime}\n"
                        f"[bold white]Memory:[/bold white] {verdict_res.memory}\n"
                        f"[bold white]Testcases Passed:[/bold white] {verdict_res.total_correct}/{verdict_res.total_testcases}",
                        title="Submission Accepted",
                        border_style="green",
                    )
                )
            else:
                db.commit()
                console.print(
                    Panel.fit(
                        f"[bold red]✖ JUDGE VERDICT: {verdict_res.status_msg}[/bold red]\n"
                        f"[bold white]Testcases Passed:[/bold white] {verdict_res.total_correct}/{verdict_res.total_testcases}\n"
                        f"[bold white]Last Testcase:[/bold white] {verdict_res.last_testcase or 'N/A'}\n"
                        f"[bold white]Expected Output:[/bold white] {verdict_res.expected_output or 'N/A'}\n"
                        f"[bold white]Code Output:[/bold white] {verdict_res.code_output or 'N/A'}",
                        title="Submission Rejected",
                        border_style="red",
                    )
                )
        except Exception as e:
            console.print(f"[bold red]Submission error:[/bold red] {e}")
            raise typer.Exit(code=1) from e


@codeforces_app.command(name="set-statement")
def codeforces_set_statement(
    statement: str = typer.Option(None, "--statement", "-s", help="Problem statement text"),
    file: Path = typer.Option(
        None, "--file", "-f", help="Path to text or markdown file containing statement"
    ),
    sample_input: str = typer.Option(None, "--input", "-i", help="Sample input"),
    sample_output: str = typer.Option(None, "--output", "-o", help="Sample expected output"),
    session_id: int = typer.Option(None, "--session-id", help="Target session ID"),
) -> None:
    """Supply the official problem statement and test cases for Codeforces."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    content = ""
    if file:
        if not file.exists():
            console.print(f"[red]Error:[/red] File not found: {file}")
            raise typer.Exit(code=1)
        content = file.read_text(encoding="utf-8").strip()
    elif statement:
        content = statement.strip()
    else:
        content = typer.prompt("Enter problem statement")

    if not content:
        console.print("[red]Error:[/red] Problem statement cannot be empty.")
        raise typer.Exit(code=1)

    with get_db() as db:
        repo = Repository(db)
        session: DailySession | None = None
        if session_id:
            session = db.get(DailySession, session_id)
        else:
            session = repo.get_daily_session_for_date(cycle_date)

        if not session or not session.problem:
            console.print(
                "[yellow]No active daily session found. Run 'dc-agent daily' first to select a problem.[/yellow]"
            )
            raise typer.Exit(code=1)

        test_cases = []
        if sample_input and sample_output:
            test_cases.append(
                {
                    "input_data": sample_input,
                    "expected_output": sample_output,
                    "is_sample": True,
                    "is_verified": True,
                    "source": "USER_INPUT",
                }
            )

        repo.set_problem_statement(session.problem, description=content, test_cases=test_cases)
        if session.status == "STATEMENT_REQUIRED":
            session.status = "INITIALIZED"
        db.commit()

        console.print(f"[green]✔ Problem statement updated for:[/green] {session.problem.title}")
        if test_cases:
            console.print(f"[green]✔ Added {len(test_cases)} sample test case(s).[/green]")
        console.print("[dim]You can now run 'dc-agent daily' or 'dc-agent solve' to proceed.[/dim]")


@app.command(name="add-test")
def add_test(
    input_data: str = typer.Option(..., "--input", "-i", help="Test case input"),
    expected_output: str = typer.Option(..., "--output", "-o", help="Verified expected output"),
    session_id: int = typer.Option(None, "--session-id", "-s", help="Target session ID"),
) -> None:
    """Add a verified test case with expected output to the active problem."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session: DailySession | None = None
        if session_id:
            session = db.get(DailySession, session_id)
        else:
            session = repo.get_daily_session_for_date(cycle_date)

        if not session or not session.problem:
            console.print("[yellow]No active session found. Run 'dc-agent daily' first.[/yellow]")
            raise typer.Exit(code=1)

        repo.set_problem_statement(
            session.problem,
            description=session.problem.description or session.problem.title,
            test_cases=[
                {
                    "input_data": input_data,
                    "expected_output": expected_output,
                    "is_sample": True,
                    "is_verified": True,
                    "source": "USER_INPUT",
                }
            ],
        )
        db.commit()

        console.print(
            f"[green]✔ Verified test case added to problem:[/green] {session.problem.title}"
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
    curriculum()


@app.command()
def test(
    allow_insecure_fallback: bool = typer.Option(
        False,
        "--allow-insecure-fallback",
        help="Allow fallback to subprocess runner if Docker is unavailable",
    ),
) -> None:
    """Execute local sandbox tests against the current solution."""
    settings = get_settings()
    if allow_insecure_fallback:
        settings.allow_insecure_fallback = True

    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)

        if not session or not session.attempts:
            console.print(
                "[yellow]No solution attempt recorded to test. Run 'dc-agent daily' first.[/yellow]"
            )
            return

        latest = session.attempts[-1]
        sandbox = Sandbox(settings=settings)
        test_cases = [
            TestCase(
                input_data=tc.input_data,
                expected_output=tc.expected_output,
                is_verified=tc.is_verified,
            )
            for tc in session.problem.test_cases
        ]

        summary = sandbox.run_tests(latest.source_code, test_cases)
        color = "green" if summary.overall_verdict == LocalExecutionVerdict.PASS else "yellow"
        console.print(
            f"[bold cyan]Local Test Results ({summary.backend_used}):[/bold cyan] [{color}]{summary.overall_verdict}[/{color}]"
        )
        console.print(
            f"Passed: {summary.passed_tests}/{summary.total_tests} | Unverified: {summary.unverified_tests} (Time: {summary.total_time_ms:.1f}ms)\n"
        )

        for r in summary.results:
            if r.verdict == LocalExecutionVerdict.PASS:
                icon = "✔"
                color_name = "green"
            elif r.verdict == LocalExecutionVerdict.UNVERIFIED:
                icon = "ℹ"
                color_name = "yellow"
            else:
                icon = "✘"
                color_name = "red"

            console.print(
                f"[{color_name}]{icon} Test #{r.test_index}: {r.verdict}[/{color_name}] ({r.execution_time_ms:.1f}ms)"
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
            settings = get_settings()
            cycle_date = get_canonical_cycle_date(settings)
            session = repo.get_daily_session_for_date(cycle_date)

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
            db.commit()
            console.print(f"[yellow]Recorded verdict {v_norm} for session #{session.id}.[/yellow]")


@app.command()
def verify() -> None:
    """Poll official platform APIs to verify whether manual submission was Accepted."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)

        if not session:
            console.print("[yellow]No active session today to verify.[/yellow]")
            return

        if session.platform.lower() == "codeforces":
            console.print(
                f"[cyan]Querying Codeforces API for {settings.codeforces_username} submissions...[/cyan]"
            )
            cf = CodeforcesAdapter()
            parts = session.problem.external_id.split("_")
            if len(parts) == 2 and parts[0].isdigit():
                contest_id = int(parts[0])
                idx = parts[1]
                sub = asyncio.run(
                    cf.check_problem_verdict(settings.codeforces_username, contest_id, idx)
                )
                if sub:
                    repo.mark_session_accepted(session, submission_id=sub.submission_id)
                    db.commit()
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
                db.commit()
                console.print(
                    f"[bold green]✔ Verified Accepted on LeetCode! (Slug: {sub.problem_identifier})[/bold green]"
                )
                return
            console.print(
                "[yellow]No Accepted submission detected yet on LeetCode public profile. You can record it manually with 'dc-agent record-verdict -v ACCEPTED'.[/yellow]"
            )


@app.command()
def submit() -> None:
    """Open the problem page in the default browser and copy solution to system clipboard."""
    import webbrowser

    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)

        if not session:
            console.print("[yellow]No active challenge session found for today.[/yellow]")
            return

        latest_attempt = session.attempts[-1] if session.attempts else None
        if latest_attempt:
            # Try to copy to clipboard
            copied = False
            try:
                import pyperclip  # type: ignore[import-untyped]

                pyperclip.copy(latest_attempt.source_code)
                copied = True
            except Exception:
                pass

            if not copied and sys.platform == "win32":
                try:
                    import subprocess

                    subprocess.run(
                        ["clip"],
                        input=latest_attempt.source_code.encode("utf-8"),
                        check=True,
                    )
                    copied = True
                except Exception:
                    pass

            if copied:
                console.print("[green]✔ Solution code copied to clipboard![/green]")
            else:
                console.print(
                    "[yellow]Clipboard utility not available. Solution is in problem workspace.[/yellow]"
                )

        console.print(f"[cyan]Opening submission page in browser:[/cyan] {session.problem.url}")
        webbrowser.open(session.problem.url)
        console.print(
            "\n[dim]Instructions:\n"
            "1. Paste code on the platform submission tab.\n"
            "2. Submit and wait for platform judgment.\n"
            "3. Run 'dc-agent verify' or 'dc-agent record-verdict -v ACCEPTED' to record confirmation.[/dim]"
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
