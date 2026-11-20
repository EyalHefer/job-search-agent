"""Turns the ranked jobs into a short Hebrew message."""

from datetime import datetime


def write_digest(jobs: list[dict], stats: dict, now: datetime) -> str:
    lines = [f"*משרות מומלצות · {now:%d.%m.%Y}*", summary(stats), ""]
    if not jobs:
        lines.append("לא נמצאו משרות חדשות שמתאימות מספיק.")
    for n, job in enumerate(jobs, 1):
        item = job["item"]
        company = item.get("company") or "חברה לא ידועה"
        lines.append(f"{n}. *{item['title']}* · {company} · {job['score']}%")
        lines.append(f"   למה: {job['why']}")
        if job.get("gaps"):
            lines.append(f"   חסר: {job['gaps']}")
        if item.get("page_status") == "unknown":
            lines.append("   (לא הצלחתי לוודא שהמשרה עדיין פתוחה)")
        lines.append(f"   {item['url']}")
        lines.append("")
    lines.append("פידבק: python agent.py feedback <מספר> --applied yes|no [--match 0-100] [--reason \"...\"]")
    return "\n".join(lines)


def summary(stats: dict) -> str:
    parts = [f"{stats.get('new', 0)} משרות חדשות"]
    if stats.get("rejected_by_rules"):
        parts.append(f"{stats['rejected_by_rules']} סוננו לפי הכללים")
    if stats.get("closed"):
        parts.append(f"{stats['closed']} כבר נסגרו")
    if stats.get("duplicates"):
        parts.append(f"{stats['duplicates']} כפולות")
    if stats.get("unscored"):
        parts.append(f"{stats['unscored']} לא דורגו וינוסו שוב בהרצה הבאה")
    return " · ".join(parts)
