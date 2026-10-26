"""Upgrade old project office leaders independently of opening the team page."""
from __future__ import annotations

import json

PROFILES = {"文献抽取": "builtin-literature-lead", "文件撰写": "builtin-writing-lead"}
MIGRATION = "team_leader_bindings_v1"


def legacy_plan(db, project_id=None):
    teams = [t for t in db.teams() if t["kind"] == "fixed"]
    plan = []
    for team in teams:
        if project_id is not None and team.get("project_id") != project_id:
            continue
        peers = [t for t in teams if t.get("project_id") == team.get("project_id")]
        profile = PROFILES.get(team["name"]) or list(PROFILES.values())[peers.index(team) % len(PROFILES)]
        assistant = db.assistant(profile)
        if not assistant:
            continue
        for member in db.team_members(team["id"]):
            if member["role"] != "leader":
                continue
            old = member.get("assistant_id") or ""
            if old != "builtin-steward" and (old or member["name"] not in ("智研管家", "全局 Leader")):
                continue
            plan.append({"team": team, "member": member, "assistant": assistant})
    return plan


def migrate(db, project_id=None, *, once=True):
    if once:
        import service_settings
        service_settings.ensure(db)
        row = db.db.execute("SELECT data FROM service_settings WHERE service=? AND owner=''", (MIGRATION,)).fetchone()
        if row:
            return 0
    plan = legacy_plan(db, project_id)
    changed = 0
    with db.db:
        for item in plan:
            member, assistant = item["member"], item["assistant"]
            existing = next((m for m in db.team_members(item["team"]["id"]) if m["id"] != member["id"]
                             and m["assistant_id"] == assistant["id"]), None)
            if existing:
                if existing["role"] != "member":
                    continue
                updated = db.db.execute("UPDATE team_members SET role='observer' WHERE id=? AND role='leader' AND assistant_id=? AND name=?",
                                        (member["id"], member["assistant_id"], member["name"]))
                if not updated.rowcount:
                    continue
                promoted = db.db.execute("UPDATE team_members SET role='leader' WHERE id=? AND role='member' AND assistant_id=?",
                                         (existing["id"], assistant["id"]))
                if not promoted.rowcount:
                    raise ValueError("团队成员配置已变化，请刷新后重试")
            else:
                # Keep custom prompts, runtime settings, member IDs and task links.
                updated = db.db.execute("UPDATE team_members SET assistant_id=?,name=? WHERE id=? AND role='leader' AND assistant_id=? AND name=?",
                                       (assistant["id"], assistant["name"], member["id"], member["assistant_id"], member["name"]))
                if not updated.rowcount:
                    continue
            changed += 1
        if once:
            db.db.execute("INSERT OR REPLACE INTO service_settings(service,owner,data) VALUES (?,'',?)",
                          (MIGRATION, json.dumps({"version": 1, "updated_leaders": changed})))
    return changed
