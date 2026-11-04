"""Lightweight localhost FastAPI dashboard for DC-Agent."""

from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from dc_agent.config import get_canonical_cycle_date, get_settings
from dc_agent.curriculum.models import TIER_BOUNDS, CurriculumTier
from dc_agent.db.repository import Repository
from dc_agent.db.session import get_db
from dc_agent.providers.factory import get_ai_provider

app = FastAPI(
    title="DC-Agent Local Dashboard",
    description="Local monitoring and solution viewer for daily competitive programming.",
    version="0.1.0",
)


@app.get("/api/status")
async def get_system_status() -> dict[str, Any]:
    """Return system health and configuration status without exposing secrets."""
    settings = get_settings()

    ai_healthy = False
    try:
        provider = get_ai_provider(settings=settings)
        ai_healthy = await provider.health_check()
    except Exception:
        ai_healthy = False

    return {
        "status": "ONLINE",
        "ai_provider": settings.ai_provider,
        "ai_model": settings.gemini_model,
        "ai_healthy": ai_healthy,
        "codeforces_handle": settings.codeforces_username,
        "leetcode_handle": settings.leetcode_username,
        "daily_policy": settings.daily_policy,
        "scheduled_time": settings.daily_run_time,
        "timezone": settings.timezone,
        "sandbox_backend": settings.sandbox_backend,
    }


@app.get("/api/today")
def get_today_session() -> dict[str, Any]:
    """Return today's active challenge session and latest solution."""
    settings = get_settings()
    cycle_date = get_canonical_cycle_date(settings)

    with get_db() as db:
        repo = Repository(db)
        session = repo.get_daily_session_for_date(cycle_date)

        if not session:
            return {
                "active": False,
                "date": cycle_date,
                "message": "No session initialized yet today.",
            }

        latest_attempt = session.attempts[-1] if session.attempts else None

        return {
            "active": True,
            "session_id": session.id,
            "platform": session.platform,
            "date": session.cycle_date,
            "status": session.status,
            "problem": {
                "title": session.problem.title,
                "url": session.problem.url,
                "difficulty": session.problem.difficulty_label,
                "tags": session.problem.tags,
            },
            "latest_attempt": {
                "number": latest_attempt.attempt_number if latest_attempt else 0,
                "local_verdict": latest_attempt.local_verdict if latest_attempt else None,
                "platform_verdict": latest_attempt.platform_verdict if latest_attempt else None,
                "code": latest_attempt.source_code if latest_attempt else None,
            }
            if latest_attempt
            else None,
        }


@app.get("/api/history")
def get_history(limit: int = 15) -> list[dict[str, Any]]:
    """Return historical problem-solving sessions."""
    with get_db() as db:
        repo = Repository(db)
        sessions = repo.get_recent_sessions(limit=limit)

        return [
            {
                "id": s.id,
                "platform": s.platform,
                "date": s.cycle_date,
                "problem_title": s.problem.title,
                "url": s.problem.url,
                "difficulty": s.problem.difficulty_label,
                "status": s.status,
                "attempts": len(s.attempts),
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
            }
            for s in sessions
        ]


@app.get("/api/curriculum")
def get_curriculum() -> dict[str, Any]:
    """Return Codeforces progression metrics."""
    settings = get_settings()
    with get_db() as db:
        repo = Repository(db)
        prog = repo.get_curriculum_progress(settings.codeforces_username)
        tier_enum = CurriculumTier(prog.current_tier)
        low, high = TIER_BOUNDS[tier_enum]

        return {
            "handle": settings.codeforces_username,
            "current_tier": prog.current_tier,
            "tier_name": tier_enum.name,
            "target_rating": prog.current_rating_target,
            "tier_bounds": [low, high],
            "consecutive_accepted": prog.consecutive_accepted,
            "consecutive_failed": prog.consecutive_failed,
        }


@app.get("/", response_class=HTMLResponse)
def dashboard_html() -> str:
    """Render a clean, modern localhost dashboard interface."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>DC-Agent :: Daily Competitive Programming Assistant</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }
    </style>
</head>
<body class="p-6 max-w-6xl mx-auto">
    <header class="flex justify-between items-center mb-8 pb-4 border-b border-slate-800">
        <div>
            <h1 class="text-3xl font-bold text-sky-400">⚡ DC-Agent</h1>
            <p class="text-sm text-slate-400">Daily Competitive Programming Assistant &middot; Gemini Powered</p>
        </div>
        <div id="status-badge" class="px-3 py-1 bg-slate-800 border border-slate-700 rounded-full text-xs text-slate-300">
            Checking status...
        </div>
    </header>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <!-- Today Card -->
        <div class="md:col-span-2 bg-slate-900 border border-slate-800 rounded-lg p-5">
            <h2 class="text-lg font-semibold text-slate-200 mb-3 flex items-center justify-between">
                <span>🎯 Today's Challenge</span>
                <span id="today-platform" class="text-xs px-2 py-0.5 rounded bg-sky-950 text-sky-300 border border-sky-800">PENDING</span>
            </h2>
            <div id="today-details" class="space-y-2">
                <p class="text-slate-400 text-sm">Loading daily challenge details...</p>
            </div>
            <div id="solution-container" class="mt-4 hidden">
                <h3 class="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-1">Generated Solution</h3>
                <pre id="solution-code" class="bg-slate-950 p-4 rounded text-xs text-emerald-400 overflow-x-auto max-h-80 border border-slate-800"></pre>
            </div>
        </div>

        <!-- Curriculum Card -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-5">
            <h2 class="text-lg font-semibold text-slate-200 mb-3">📈 Codeforces Curriculum</h2>
            <div id="curriculum-details" class="space-y-3 text-sm">
                <p class="text-slate-400">Loading progression...</p>
            </div>
        </div>
    </div>

    <!-- History Table -->
    <div class="bg-slate-900 border border-slate-800 rounded-lg p-5">
        <h2 class="text-lg font-semibold text-slate-200 mb-4">📜 Recent Session History</h2>
        <div class="overflow-x-auto">
            <table class="w-full text-left text-xs text-slate-300">
                <thead class="bg-slate-950 text-slate-400 uppercase border-b border-slate-800">
                    <tr>
                        <th class="p-3">Date</th>
                        <th class="p-3">Platform</th>
                        <th class="p-3">Problem</th>
                        <th class="p-3">Difficulty</th>
                        <th class="p-3">Status</th>
                        <th class="p-3">Attempts</th>
                    </tr>
                </thead>
                <tbody id="history-rows" class="divide-y divide-slate-800">
                    <tr><td colspan="6" class="p-3 text-center text-slate-500">Loading history...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        async function loadDashboard() {
            try {
                // Load status
                const sRes = await fetch('/api/status');
                const status = await sRes.json();
                document.getElementById('status-badge').innerHTML = `
                    <span class="inline-block w-2 h-2 rounded-full ${status.ai_healthy ? 'bg-emerald-400' : 'bg-amber-400'} mr-1"></span>
                    ${status.ai_provider.toUpperCase()} (${status.ai_model}) &middot; ${status.timezone}
                `;

                // Load today
                const tRes = await fetch('/api/today');
                const today = await tRes.json();
                const tPlatform = document.getElementById('today-platform');
                const tDetails = document.getElementById('today-details');

                if (today.active) {
                    tPlatform.innerText = today.platform.toUpperCase();
                    tDetails.innerHTML = `
                        <div class="text-xl font-bold text-white"><a href="${today.problem.url}" target="_blank" class="hover:underline text-sky-400">${today.problem.title} ↗</a></div>
                        <div class="text-xs text-slate-400">Difficulty: <span class="text-amber-300">${today.problem.difficulty}</span> &middot; Status: <span class="text-emerald-400 font-bold">${today.status}</span></div>
                        <div class="text-xs text-slate-500">Tags: ${(today.problem.tags || []).join(', ') || 'None'}</div>
                    `;
                    if (today.latest_attempt && today.latest_attempt.code) {
                        document.getElementById('solution-container').classList.remove('hidden');
                        document.getElementById('solution-code').innerText = today.latest_attempt.code;
                    }
                } else {
                    tDetails.innerHTML = `<p class="text-slate-400">${today.message}</p><p class="text-xs text-slate-500 mt-1">Run <code>dc-agent daily</code> to start today's session.</p>`;
                }

                // Load curriculum
                const cRes = await fetch('/api/curriculum');
                const cur = await cRes.json();
                document.getElementById('curriculum-details').innerHTML = `
                    <div class="flex justify-between py-1 border-b border-slate-800">
                        <span class="text-slate-400">Handle</span>
                        <span class="text-white font-bold">${cur.handle}</span>
                    </div>
                    <div class="flex justify-between py-1 border-b border-slate-800">
                        <span class="text-slate-400">Current Tier</span>
                        <span class="text-sky-400 font-bold">Tier ${cur.current_tier} (${cur.tier_name})</span>
                    </div>
                    <div class="flex justify-between py-1 border-b border-slate-800">
                        <span class="text-slate-400">Target Rating</span>
                        <span class="text-amber-400 font-bold">${cur.target_rating}</span>
                    </div>
                    <div class="flex justify-between py-1 border-b border-slate-800">
                        <span class="text-slate-400">Consecutive Solved</span>
                        <span class="text-emerald-400 font-bold">${cur.consecutive_accepted}/3 toward promotion</span>
                    </div>
                `;

                // Load history
                const hRes = await fetch('/api/history');
                const history = await hRes.json();
                const tbody = document.getElementById('history-rows');
                if (history.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="6" class="p-3 text-center text-slate-500">No sessions recorded yet.</td></tr>`;
                } else {
                    tbody.innerHTML = history.map(h => `
                        <tr class="hover:bg-slate-800/50">
                            <td class="p-3 font-mono">${h.date}</td>
                            <td class="p-3 uppercase font-semibold text-sky-400">${h.platform}</td>
                            <td class="p-3 font-medium text-white"><a href="${h.url}" target="_blank" class="hover:underline">${h.problem_title}</a></td>
                            <td class="p-3 text-slate-400">${h.difficulty}</td>
                            <td class="p-3"><span class="px-2 py-0.5 rounded text-xs ${h.status === 'ACCEPTED' ? 'bg-emerald-950 text-emerald-300 border border-emerald-800' : 'bg-slate-800 text-slate-300'}">${h.status}</span></td>
                            <td class="p-3">${h.attempts}</td>
                        </tr>
                    `).join('');
                }
            } catch (err) {
                console.error('Dashboard load error:', err);
            }
        }
        loadDashboard();
    </script>
</body>
</html>
"""
