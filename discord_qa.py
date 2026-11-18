#!/usr/bin/env python3
"""Continuous QA checks for a Discord guild.

Every pass verifies the server's expected structure, roles, permissions,
guild settings, AutoMod rules, emoji and message readability. Results are
appended to a local log; when the failure set changes, a compact report is
posted to the QA channel so regressions are visible without tailing a file.

Usage:
    discord-qa.py              # one pass, print report
    discord-qa.py --daemon     # poll forever at --interval seconds
    discord-qa.py --quiet      # one pass, print nothing unless something fails
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

import os
GUILD_ID = "1572541420298829834"
QA_CHANNEL_ID = "1572712112260653246"  # #mod-log
TOKEN_FILE = "/root/.config/opencode/secrets/discord.token"
API = "https://discord.com/api/v10"
AUDIT_REASON = "QA check"

# --- expected state -------------------------------------------------------
EXPECT_CATEGORIES = [
    "╔══〔 📌 START HERE 〕══╗",
    "╔══〔 💬 COMMUNITY 〕══╗",
    "╔══〔 🎮 GAMING 〕══╗",
    "╔══〔 🎉 EVENTS 〕══╗",
    "╔══〔 🔊 VOICE LOUNGE 〕══╗",
    "╔══〔 🔒 STAFF ONLY 〕══╗",
]
EXPECT_CHANNELS = {
    "╔══〔 📌 START HERE 〕══╗": [("📜╎rules", 0), ("👋╎welcome", 0), ("📢╎announcements", 0)],
    "╔══〔 💬 COMMUNITY 〕══╗": [("💬╎general", 0), ("🪪╎introductions", 0), ("🎲╎off-topic", 0), ("📸╎media", 0), ("🤖╎bot-commands", 0)],
    "╔══〔 🎮 GAMING 〕══╗": [("🕹️╎game-chat", 0), ("🔎╎looking-for-group", 15), ("🎬╎clips-highlights", 0)],
    "╔══〔 🎉 EVENTS 〕══╗": [("📅╎events", 0), ("🎊╎event-chat", 0)],
    "╔══〔 🔊 VOICE LOUNGE 〕══╗": [("🎧 ╎ Lobby", 2), ("🎮 ╎ Squad 1", 2), ("🎮 ╎ Squad 2", 2), ("💤 ╎ AFK", 2), ("➕ ╎ Buat Room", 2)],
    "╔══〔 🔒 STAFF ONLY 〕══╗": [("🛡️╎staff-chat", 0), ("📋╎mod-log", 0), ("🎙️ ╎ Staff Room", 2)],
}
READ_ONLY = ["📜╎rules", "👋╎welcome", "📢╎announcements"]
REQUIRED_ROLES = [
    "BOT-ADMIN",
    "BOT-QA",
    "👑 ╎ Founder",
    "Moderator",
    "Member",
    "@everyone",
]
# Roles that are allowed to hold ADMINISTRATOR.
ADMIN_ALLOWED_ROLES = ["BOT-ADMIN", "BOT-QA", "👑 ╎ Founder"]
MEMBER_ROLE_NAME = "Member"
EXPECT_EMOJI = ["hiwave", "gg", "lol", "brb", "yes", "no", "wow", "hype"]
EXPECT_GUILD = {
    "verification_level": 2,
}
SEND_MESSAGES = 0x800


def api(method, path, body=None):
    token = os.environ.get("DISCORD_TOKEN") or open(TOKEN_FILE, encoding="utf-8").read().strip()
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": "Bot " + token,
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (https://github.com/omalauncher/discord-qa, 1.0)",
            "X-Audit-Log-Reason": AUDIT_REASON,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}, None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        return None, f"HTTP {exc.code}: {detail[:160]}"
    except Exception as exc:  # network blip
        return None, str(exc)


def fetch_all():
    """Pull everything a pass needs, so a check never hits the API itself."""
    data, err = {}, None
    for key, path in [
        ("guild", f"/guilds/{GUILD_ID}"),
        ("channels", f"/guilds/{GUILD_ID}/channels"),
        ("roles", f"/guilds/{GUILD_ID}/roles"),
        ("members", f"/guilds/{GUILD_ID}/members?limit=1000"),
        ("emoji", f"/guilds/{GUILD_ID}/emojis"),
        ("automod", f"/guilds/{GUILD_ID}/auto-moderation/rules"),
        ("me", "/users/@me"),
    ]:
        data[key], e = api("GET", path)
        if e:
            err = f"{key}: {e}"
    return data, err


# --- checks ---------------------------------------------------------------
def check_identity(d, out):
    me = d.get("me") or {}
    out.append((bool(me.get("bot")), f"token valid, identity = {me.get('username')} ({me.get('id')})"))


def check_categories(d, out):
    cats = [c["name"] for c in d["channels"] if c["type"] == 4]
    missing = [c for c in EXPECT_CATEGORIES if c not in cats]
    extra = [c for c in cats if c not in EXPECT_CATEGORIES]
    ok = not missing and not extra
    detail = "ok" if ok else f"missing={missing} unexpected={extra}"
    out.append((ok, detail))


def check_channels(d, out):
    by_parent = {}
    cats = {c["name"]: c["id"] for c in d["channels"] if c["type"] == 4}
    for c in d["channels"]:
        if c["type"] == 4:
            continue
        by_parent.setdefault(c.get("parent_id"), []).append(c)

    problems = []
    for cat_name, expected in EXPECT_CHANNELS.items():
        parent_id = cats.get(cat_name)
        if not parent_id:
            problems.append(f"{cat_name} hilang")
            continue
        have = {c["name"]: c["type"] for c in by_parent.get(parent_id, [])}
        for name, ctype in expected:
            if name not in have:
                problems.append(f"#{name} tidak ada di {cat_name}")
            elif have[name] != ctype:
                problems.append(f"{name} type {have[name]} != {ctype}")
    # no stray channels outside expected categories
    known_ids = set(cats.values())
    for parent_id, cs in by_parent.items():
        if parent_id not in known_ids:
            problems += [f"{c['name']} di luar kategori" for c in cs]
    out.append((not problems, "; ".join(problems) if problems else
                f"{sum(len(v) for v in EXPECT_CHANNELS.values())} channel sesuai rencana"))


def check_roles(d, out):
    roles = {r["name"]: r for r in d["roles"]}
    missing = [n for n in REQUIRED_ROLES if n not in roles]
    if missing:
        out.append((False, f"role hilang: {missing}"))
        return
    problems = []
    if roles["👑 ╎ Founder"]["position"] <= roles["Moderator"]["position"]:
        problems.append("Founder harus di atas Moderator")
    if roles["👑 ╎ Founder"]["position"] <= roles["Member"]["position"]:
        problems.append("Founder harus di atas Member")
    if roles["BOT-ADMIN"]["position"] <= roles["Moderator"]["position"]:
        problems.append("BOT-ADMIN harus di atas Moderator")
    out.append((not problems, "; ".join(problems) if problems else f"hierarchy ok ({len(roles)} role)"))


def check_role_permissions(d, out):
    roles = {r["name"]: r for r in d["roles"]}
    problems = []
    for name in ADMIN_ALLOWED_ROLES:
        if name not in roles:
            problems.append(f"role {name} hilang")
        elif not int(roles[name]["permissions"]) & 0x8:
            problems.append(f"{name} kehilangan ADMINISTRATOR")
    for r in d["roles"]:
        if r["name"] in ADMIN_ALLOWED_ROLES:
            continue
        if int(r["permissions"]) & 0x8:
            problems.append(f"{r['name']} seharusnya tidak punya ADMINISTRATOR")
    if MEMBER_ROLE_NAME in roles and int(roles[MEMBER_ROLE_NAME]["permissions"]) & SEND_MESSAGES == 0:
        problems.append("Member kehilangan SEND_MESSAGES")
    out.append((not problems, "; ".join(problems) if problems else
                "hak role sesuai (admin hanya di bot & Founder)"))


def check_readonly(d, out):
    everyone = GUILD_ID
    problems = []
    for c in d["channels"]:
        if c["name"] not in READ_ONLY or c["type"] != 0:
            continue
        ow = {o["id"]: int(o.get("deny") or 0) for o in c.get("permission_overwrites", [])}
        if not ow.get(everyone, 0) & SEND_MESSAGES:
            problems.append(f"#{c['name']} tidak read-only")
    found = {c["name"] for c in d["channels"] if c["type"] == 0}
    for name in READ_ONLY:
        if name not in found:
            problems.append(f"#{name} tidak ada")
    out.append((not problems, "; ".join(problems) if problems else
                f"{len(READ_ONLY)} channel info terkunci"))


def check_guild_settings(d, out):
    g = d.get("guild") or {}
    problems = []
    for key, want in EXPECT_GUILD.items():
        if g.get(key) != want:
            problems.append(f"{key}={g.get(key)} (expected {want})")
    if (g.get("explicit_content_filter") or 0) < 1:
        problems.append("explicit_content_filter mati")
    names = {c["id"]: c["name"] for c in d["channels"]}
    if names.get(g.get("rules_channel_id")) != "📜╎rules":
        problems.append("rules_channel tidak menunjuk #rules")
    if names.get(g.get("system_channel_id")) != "👋╎welcome":
        problems.append("system_channel tidak menunjuk #welcome")
    if names.get(g.get("afk_channel_id")) != "💤 ╎ AFK":
        problems.append("afk_channel tidak menunjuk voice AFK")
    out.append((not problems, "; ".join(problems) if problems else
                "verification=medium, filter aktif, rules/system/afk terhubung"))


def check_automod(d, out):
    rules = [r for r in (d.get("automod") or []) if r.get("enabled")]
    out.append((len(rules) > 0, f"{len(rules)} aturan aktif" if rules else "belum ada aturan AutoMod aktif"))


def check_emoji(d, out):
    have = {e["name"] for e in d.get("emoji") or []}
    missing = [n for n in EXPECT_EMOJI if n not in have]
    out.append((not missing, f"missing={missing}" if missing else f"{len(EXPECT_EMOJI)} emoji tersedia"))


def check_member_roles(d, out):
    role_ids = {r["name"]: r["id"] for r in d["roles"]}
    member_id = role_ids.get(MEMBER_ROLE_NAME)
    problems = []
    humans = 0
    for m in d.get("members") or []:
        if m["user"].get("bot"):
            continue
        humans += 1
        if member_id not in m.get("roles", []):
            problems.append(f"{m['user']['username']} belum punya role {MEMBER_ROLE_NAME}")
    out.append((not problems, "; ".join(problems) if problems else
                f"{humans}/{humans} member punya role {MEMBER_ROLE_NAME}"))


def check_message_content_intent(d, out):
    """A non-bot message must come back with non-empty content.

    Empty content on someone else's message means the app is not approved
    for the MESSAGE_CONTENT privileged intent.
    """
    for channel in d["channels"]:
        if channel["type"] != 0:
            continue
        msgs, err = api("GET", f"/channels/{channel['id']}/messages?limit=20")
        if err or not msgs:
            continue
        for m in msgs:
            if m["author"].get("bot"):
                continue
            if not m["content"].strip():
                out.append((False,
                            f"pesan member di #{channel['name']} terbaca kosong — "
                            "MESSAGE_CONTENT intent belum aktif"))
                return
            out.append((True, f"pesan member terbaca normal (#{channel['name']})"))
            return
    out.append((True, "belum ada pesan member untuk diuji (dilewati)"))


CHECKS = [
    ("identity", check_identity),
    ("kategori", check_categories),
    ("channel", check_channels),
    ("hierarchy-role", check_roles),
    ("hak-role", check_role_permissions),
    ("channel-readonly", check_readonly),
    ("setting-guild", check_guild_settings),
    ("automod", check_automod),
    ("emoji", check_emoji),
    ("role-member", check_member_roles),
    ("baca-pesan", check_message_content_intent),
]


def run_pass():
    d, err = fetch_all()
    results = []
    if err:
        return [("fetch", False, err)], d
    for name, fn in CHECKS:
        before = len(results)
        try:
            fn(d, results)
        except Exception as exc:
            results.append((name, False, f"check error: {exc}"))
        # label any unlabelled result produced by the check
        for i in range(before, len(results)):
            if results[i][0] is None:
                results[i] = (name, results[i][1], results[i][2])
            elif isinstance(results[i][0], bool):
                ok, detail = results[i][0], results[i][1]
                results[i] = (name, ok, detail)
    return results, d


def format_report(results, verbose=True):
    fails = [r for r in results if not r[1]]
    lines = []
    for name, ok, detail in results:
        if ok and not verbose:
            continue
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name:<18} {detail}")
    return fails, "\n".join(lines)


def post_report(text):
    body = {"content": text[:1900]}
    api("POST", f"/channels/{QA_CHANNEL_ID}/messages", body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daemon", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    last_failures = None

    def one_pass():
        nonlocal last_failures
        results, _ = run_pass()
        fails = [f"{n}: {d}" for n, ok, d in results if not ok]
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        if fails:
            print(f"[{stamp}] FAIL ({len(fails)}/{len(results)})", flush=True)
            for f in fails:
                print(f"    - {f}", flush=True)
        else:
            print(f"[{stamp}] PASS {len(results)}/{len(results)}", flush=True)

        # Post only when the failure set changes, so the QA channel stays quiet.
        key = tuple(sorted(fails))
        if key != last_failures:
            if last_failures is not None or fails:
                verdict = "🟢 QA PASS" if not fails else f"🔴 QA FAIL — {len(fails)} masalah"
                body = verdict
                if fails:
                    body += "\n" + "\n".join(f"• {f}" for f in fails[:12])
                post_report(body)
            last_failures = key
        return not fails

    if args.daemon:
        print(f"QA watching guild {GUILD_ID} every {args.interval}s", flush=True)
        while True:
            try:
                one_pass()
            except Exception as exc:
                print(f"  ! pass failed: {exc}", flush=True)
            time.sleep(args.interval)
        return 0

    results, _ = run_pass()
    fails, text = format_report(results, verbose=not args.quiet)
    print(text)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
