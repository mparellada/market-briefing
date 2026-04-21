"""Fetch Marc's Task Command Center state and emit a plain-text section
for the daily briefing covering:

  - tasks due today
  - overdue tasks (still open, dueDate < today)
  - open shopping list items

Each task mentions its project and, when linked via goalId -> goal ->
northStarId -> northStar.title, the "venture" it belongs to.

Usage:
    python daily_tasks.py <output.txt>

The Firestore read-key is the public anon key used by the Task Command
Center web app — it only grants read/write to the app/taskData document,
so checking into the repo is safe. Confirmed by the task-command skill.
"""
from __future__ import annotations
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIRESTORE_URL = (
    "https://firestore.googleapis.com/v1/projects/"
    "task-command-center-df4dc/databases/(default)/documents/app/taskData"
    "?key=AIzaSyAaAPhcJUdtIIjXPJcFLkEy5K7yhPcWcR0"
)
SGT = timezone(timedelta(hours=8))

PROJECT_NAMES = {
    "nprime": "NPrime Operations",
    "trading": "BD",
    "clients": "Clients",
    "family": "Personal",
    "shopping": "Shopping",
    "tcc": "TCC",
    "capitalfacts": "CapitalFacts",
}

# Projects whose tasks should never appear in the daily briefing.
# TCC (Third Culture Collective) and CapitalFacts are handled separately —
# Marc doesn't want them mixed into the morning run.
EXCLUDED_PROJECTS = {"tcc", "capitalfacts"}

PRIORITY_LABEL = {"0": "low", "1": "medium", "2": "high", "3": "critical"}


def _str(field, key):
    val = field.get(key, {})
    for k in ("stringValue", "integerValue"):
        if k in val:
            return val[k]
    return ""


def _bool(field, key):
    return field.get(key, {}).get("booleanValue", False)


def fetch_state() -> dict:
    req = urllib.request.Request(FIRESTORE_URL, headers={"User-Agent": "sushi-daily-tasks"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract(doc: dict):
    state = doc["fields"]["state"]["mapValue"]["fields"]
    tasks = state.get("tasks", {}).get("arrayValue", {}).get("values", [])
    goals = state.get("goals", {}).get("arrayValue", {}).get("values", [])
    north_stars = state.get("northStars", {}).get("arrayValue", {}).get("values", [])

    ns_by_id = {}
    for n in north_stars:
        f = n["mapValue"]["fields"]
        ns_by_id[_str(f, "id")] = _str(f, "title")

    goal_to_ns = {}
    for g in goals:
        f = g["mapValue"]["fields"]
        goal_to_ns[_str(f, "id")] = _str(f, "northStarId")

    parsed = []
    for t in tasks:
        f = t["mapValue"]["fields"]
        parsed.append({
            "id": _str(f, "id"),
            "title": _str(f, "title"),
            "project": _str(f, "projectId"),
            "dueDate": _str(f, "dueDate"),
            "priority": _str(f, "priority"),
            "completed": _bool(f, "completed"),
            "kiv": _bool(f, "kiv"),
            "wfb": _bool(f, "wfb"),
            "goalId": _str(f, "goalId"),
        })
        parsed[-1]["venture"] = ns_by_id.get(goal_to_ns.get(parsed[-1]["goalId"], ""), "")
    return parsed


def _due_phrase(iso_date: str) -> str:
    """Turn 2026-04-12 into 'since April 12' style phrasing."""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%B %-d") if sys.platform != "win32" else dt.strftime("%B %#d")
    except ValueError:
        return iso_date


def fmt_task(t, include_due=False):
    """One-line TTS-friendly task description."""
    proj = PROJECT_NAMES.get(t["project"], t["project"].title() if t["project"] else "")
    title = t["title"].rstrip(".")
    prio = PRIORITY_LABEL.get(str(t["priority"]), "")
    parts = []
    if proj and proj.lower() != "shopping":
        parts.append(f"{proj}:")
    parts.append(title)
    if prio == "critical":
        parts.append("— critical")
    if include_due and t["dueDate"]:
        parts.append(f"(overdue since {_due_phrase(t['dueDate'])})")
    line = " ".join(parts).strip()
    if line.endswith(":"):
        line = line[:-1]
    if t["venture"]:
        line += f", tied to venture '{t['venture']}'"
    return line


OVERDUE_CAP = 8


def build_section(tasks: list[dict]) -> str:
    today_sgt = datetime.now(SGT).date()
    today_iso = today_sgt.isoformat()

    due_today = []
    overdue = []
    shopping = []

    for t in tasks:
        if t["completed"] or t["kiv"]:
            continue
        if t["project"] in EXCLUDED_PROJECTS:
            continue
        if t["project"] == "shopping":
            shopping.append(t)
            continue
        d = t["dueDate"]
        if not d:
            continue
        if d == today_iso:
            due_today.append(t)
        elif d < today_iso:
            overdue.append(t)

    # Sort: highest priority first, then oldest overdue first (for overdue),
    # earliest due date first (for due_today).
    due_today.sort(key=lambda t: (-int(t["priority"] or 0), t["dueDate"]))
    overdue.sort(key=lambda t: (-int(t["priority"] or 0), t["dueDate"]))

    lines: list[str] = ["=== YOUR DAY AHEAD ==="]

    if not due_today and not overdue and not shopping:
        lines.append("")
        lines.append(
            "Your task list is clear. No items due today, nothing overdue, no shopping list. "
            "Enjoy the breathing room."
        )
        return "\n".join(lines) + "\n"

    if due_today:
        lines.append("")
        lines.append(
            f"{len(due_today)} item{'s' if len(due_today) != 1 else ''} due today."
        )
        for t in due_today:
            lines.append(fmt_task(t))

    if overdue:
        lines.append("")
        shown = overdue[:OVERDUE_CAP]
        remaining = len(overdue) - len(shown)
        header = (
            f"{len(overdue)} overdue items in total."
            f" The top {len(shown)} by priority:"
            if remaining > 0
            else f"{len(overdue)} overdue items:"
        )
        lines.append(header)
        for t in shown:
            lines.append(fmt_task(t, include_due=True))
        if remaining > 0:
            lines.append(f"Plus {remaining} more lower-priority overdue items not read here.")

    if shopping:
        lines.append("")
        lines.append(
            f"Shopping list has {len(shopping)} item{'s' if len(shopping) != 1 else ''}:"
        )
        for t in shopping:
            lines.append(t["title"].rstrip("."))

    return "\n".join(lines) + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: daily_tasks.py <output.txt>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    try:
        doc = fetch_state()
    except Exception as e:
        out.write_text(
            f"=== YOUR DAY AHEAD ===\n\n(Task list unavailable: {e})\n",
            encoding="utf-8",
        )
        return 0
    tasks = extract(doc)
    out.write_text(build_section(tasks), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
