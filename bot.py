#!/usr/bin/env python3
"""DEV server AI assistant (Groq) + on-demand QA, owner-only.

Talk to it in #commands. Only the server owner is answered; everyone else is
ignored. Every change the AI proposes is shown first with buttons, and only
runs after the owner presses ✅. Destructive actions (delete/ban/kick) are not
available at all.

    !help        what the bot can do
    !qa          run the full server QA check now
    !qa test     live tests (send/edit/react/delete, role visibility)
    anything else -> AI turns it into a plan you approve
"""

import asyncio
import collections
import http.server
import importlib.util
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import discord

GUILD_ID = 1572541420298829834
OWNER_ID = 1409498347294822502
COMMANDS_CHANNEL = 1573525454311264356  # #🤖╎bot-commands
QA_CHANNEL = 1573525475437977640        # #📋╎mod-log
MODLOG_CHANNEL = 1573525475437977640    # #📋╎mod-log
BOT_CATEGORY = 1573525392273186849      # 🔒 STAFF
COMMUNITY_CATEGORY = 1573525346261934080  # 💬 CHAT
IDEAS_CHANNEL = 0
STATS_CHANNEL = 1573525454311264356
WELCOME_CHANNEL = 1573522401873104907   # #👋╎welcome
MEMBER_ROLE_ID = 1572712053175754883    # Member
MOD_ROLE_ID = 1572712033500135605       # Moderator
FOUNDER_ROLE_ID = 1572717150089650238   # 👑 ╎ Founder
XP_FILE = os.environ.get("XP_FILE", "dev-xp-data.json")
DISCORD_TOKEN_FILE = "/root/.config/opencode/secrets/discord.token"
GEMINI_KEY_FILE = "/root/.config/opencode/secrets/gemini.key"
GROQ_KEY_FILE = "/root/.config/opencode/secrets/groq.key"
MEMORY_FILE = os.environ.get("MEMORY_FILE", "dev-ai-memory.json")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

BLURPLE, GREEN, RED, GOLD = 0x5865F2, 0x57F287, 0xED4245, 0xFEE75C


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def get_discord_token():
    return os.environ.get("DISCORD_TOKEN") or open(DISCORD_TOKEN_FILE, encoding="utf-8").read().strip()


def _read_secret(env_names, file_path):
    for name in env_names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    if os.path.exists(file_path):
        return read(file_path)
    return ""


def get_gemini_key():
    return _read_secret(("GEMINI_API_KEY", "GEMINI_KEY"), GEMINI_KEY_FILE)


def get_groq_key():
    return _read_secret(("GROQ_KEY", "GROQ_API_KEY"), GROQ_KEY_FILE)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


# --- QA module (reuses local discord_qa.py) -------------------------------
_qa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_qa.py")
_spec = importlib.util.spec_from_file_location("discord_qa", _qa_path)
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)


# --- AI (Gemini primary + token-saving + Groq fallback) ---------------------
SYSTEM_PROMPT = """Asisten manajemen server Discord "DEV" untuk owner. Balas HANYA JSON: {"reply":str,"actions":[...]}.

Aksi (pakai nama persis dari state server; maks 15):
- create_category: name
- create_text_channel: name, category|null, topic|null, read_only(bool)
- create_voice_channel: name, category|null, user_limit(int|null)
- rename_channel: channel, new_name
- move_channel: channel, category
- delete_channel: channel
- set_topic: channel, topic
- set_slowmode: channel, seconds(int)
- lock_channel / unlock_channel: channel
- hide_channel: channel, visible_to_roles([str])
- create_role: name, color(#RRGGBB), hoist(bool), mentionable(bool)
- edit_role: role, new_name|null, color|null, hoist(bool|null)
- delete_role: role
- give_role / remove_role: member, role
- edit_server: name|null, description|null
- send_embed: channel, title, description, color(#RRGGBB)
- purge_messages: channel, limit(int)

Fitur bot yang BENAR-BENAR ADA: /suggestion, /rank, /leaderboard, !help, !qa, !qa test, !stats, !panel-role, !panel-ticket, !reset, !lofi [next|stop|list], !play <judul>, !skip, !queue, !stop, !vdebug, auto-role Member, welcome embed, mod-log otomatis, temp voice "➕ ╎ Buat Room".
Aturan ketat:
1. DILARANG mengarang slash command (/...) atau fitur otomatis yang tidak ada di daftar di atas. Jika owner minta fitur baru (mis. sistem /suggestion, ticket, leveling), jawab jujur di "reply" bahwa fitur itu butuh update kode bot dan beri actions [].
2. Jika hanya ngobrol/tanya/komplain, jawab ringkas & jelas di "reply" dengan actions [].
3. "reply" bahasa Indonesia santai, padat, langsung ke inti (hemat kata).
4. Nama text channel huruf kecil pakai "-" (boleh prefix emoji seperti "💬╎nama").
5. Gunakan konteks chat sebelumnya untuk kata rujukan ("tadi", "itu", "hapus")."""

CHAT_HISTORY = []
MAX_HISTORY = 12  # 6 turns (user + compact assistant summary)

if os.path.exists(MEMORY_FILE):
    try:
        CHAT_HISTORY = json.loads(read(MEMORY_FILE))[-MAX_HISTORY:]
    except Exception:
        CHAT_HISTORY = []


def _save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(CHAT_HISTORY[-MAX_HISTORY:], fh, ensure_ascii=False)
    except Exception:
        pass


def _compact_assistant_memory(plan):
    acts = ", ".join(a.get("type", "?") for a in plan.get("actions") or [])
    reply = (plan.get("reply") or "").strip()[:240]
    return f"{reply} [aksi: {acts}]".strip() if acts else reply


def _record_turn(request_text, plan):
    CHAT_HISTORY.append({"role": "user", "content": request_text[:500]})
    CHAT_HISTORY.append({"role": "assistant", "content": _compact_assistant_memory(plan)})
    if len(CHAT_HISTORY) > MAX_HISTORY:
        del CHAT_HISTORY[:-MAX_HISTORY]
    _save_memory()


def _parse_plan(raw_content):
    text = (raw_content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    plan = json.loads(text)
    plan.setdefault("reply", "")
    actions = plan.get("actions") or []
    plan["actions"] = [a for a in actions if isinstance(a, dict) and a.get("type") in ACTIONS][:15]
    return plan


def gemini_plan(request_text, summary, remember=True):
    key = get_gemini_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY belum diset")
    contents = []
    if remember:
        for m in CHAT_HISTORY[-MAX_HISTORY:]:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
    contents.append({"role": "user", "parts": [{"text": f"State server:\n{summary}\n\nPermintaan: {request_text}"}]})
    gen_cfg = {
        "temperature": 0.2,
        "maxOutputTokens": 900,
        "responseMimeType": "application/json",
    }
    if "2.5" in GEMINI_MODEL:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    url = GEMINI_URL.format(model=GEMINI_MODEL) + f"?key={key}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "dev-ai-bot/2.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Gemini HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini tidak mengembalikan kandidat jawaban")
    parts = candidates[0].get("content", {}).get("parts") or []
    raw_content = "".join(p.get("text", "") for p in parts)
    plan = _parse_plan(raw_content)
    usage = data.get("usageMetadata") or {}
    meta = {
        "provider": f"Gemini ({GEMINI_MODEL})",
        "tokens": usage.get("totalTokenCount"),
    }
    if remember:
        _record_turn(request_text, plan)
    return plan, meta


def groq_plan(request_text, summary, remember=True):
    key = get_groq_key()
    if not key:
        raise RuntimeError("GROQ_KEY belum diset")
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "State server:\n" + summary},
    ]
    if remember:
        msgs.extend(CHAT_HISTORY[-MAX_HISTORY:])
    msgs.append({"role": "user", "content": request_text})
    body = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "max_tokens": 900,
        "response_format": {"type": "json_object"},
        "messages": msgs,
    }
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": "application/json", "User-Agent": "dev-ai-bot/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Groq HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}")
    plan = _parse_plan(data["choices"][0]["message"]["content"])
    usage = data.get("usage") or {}
    meta = {
        "provider": f"Groq ({GROQ_MODEL})",
        "tokens": usage.get("total_tokens"),
    }
    if remember:
        _record_turn(request_text, plan)
    return plan, meta


def ai_plan(request_text, summary, remember=True):
    if get_gemini_key():
        return gemini_plan(request_text, summary, remember=remember)
    if get_groq_key():
        return groq_plan(request_text, summary, remember=remember)
    raise RuntimeError("Tidak ada API key AI (GEMINI_API_KEY / GROQ_KEY)")


# --- helpers --------------------------------------------------------------
def color(hexstr, default=BLURPLE):
    try:
        return int(str(hexstr).lstrip("#"), 16)
    except (TypeError, ValueError):
        return default


def find_channel(guild, name):
    if not name:
        return None
    n = str(name).lstrip("#").strip().lower()
    for c in guild.channels:
        if c.name.lower() == n:
            return c
    for c in guild.channels:  # tolerate missing emoji prefix
        if c.name.lower().endswith(n):
            return c
    return None


NEW_CATEGORIES = {}  # created this run; the gateway cache may lag behind


def find_category(guild, name):
    if not name:
        return None
    n = str(name).strip().lower()
    if n in NEW_CATEGORIES:
        return NEW_CATEGORIES[n]
    for c in guild.categories:
        if c.name.lower() == n or n in c.name.lower():
            return c
    return None


def find_role(guild, name):
    n = str(name).lstrip("@").strip().lower()
    for r in guild.roles:
        if r.name.lower() == n or n in r.name.lower():
            return r
    return None


def find_member(guild, name):
    n = str(name).lstrip("@").strip().lower()
    for m in guild.members:
        if str(m.id) == n or m.name.lower() == n or m.display_name.lower() == n:
            return m
    for m in guild.members:
        if n in m.name.lower() or n in m.display_name.lower():
            return m
    return None


def server_summary(guild, query=""):
    lines = [f"Server:{guild.name}"]
    for cat, chans in guild.by_category():
        ch_str = ",".join(("#" if isinstance(c, discord.TextChannel) else "🔊") + c.name for c in chans)
        lines.append(f"[{cat.name if cat else 'none'}] {ch_str}")
    roles = [r.name for r in reversed(guild.roles) if r.name != "@everyone"]
    lines.append("Roles:" + ",".join(roles))
    q = (query or "").lower()
    if any(k in q for k in ("role", "kasih", "beri", "cabut", "member", "vip", "@", "siapa")):
        members = [m.name for m in guild.members if not m.bot]
        if members:
            lines.append("Members:" + ",".join(members[:25]))
    return "\n".join(lines)


def describe(a):
    t = a["type"]
    g = a.get
    return {
        "create_category": lambda: f"📁 Buat kategori **{g('name')}**",
        "create_text_channel": lambda: f"💬 Buat channel **#{g('name')}**" + (f" di {g('category')}" if g('category') else "") + (" (hanya-baca)" if g('read_only') else ""),
        "create_voice_channel": lambda: f"🔊 Buat voice **{g('name')}**" + (f" di {g('category')}" if g('category') else ""),
        "rename_channel": lambda: f"✏️ Ganti nama **#{g('channel')}** → **{g('new_name')}**",
        "move_channel": lambda: f"📦 Pindah **#{g('channel')}** ke kategori **{g('category')}**",
        "delete_channel": lambda: f"🗑️ Hapus channel **#{g('channel')}**",
        "set_topic": lambda: f"📝 Topik **#{g('channel')}**: {g('topic')}",
        "set_slowmode": lambda: f"🐢 Slowmode **#{g('channel')}**: {g('seconds')} detik",
        "lock_channel": lambda: f"🔒 Kunci **#{g('channel')}** (hanya-baca)",
        "unlock_channel": lambda: f"🔓 Buka kunci **#{g('channel')}**",
        "hide_channel": lambda: f"🙈 Sembunyikan **#{g('channel')}**, hanya untuk: {', '.join(g('visible_to_roles') or []) or 'admin'}",
        "create_role": lambda: f"🏷️ Buat role **{g('name')}** ({g('color')})",
        "edit_role": lambda: f"🎨 Edit role **{g('role')}**",
        "delete_role": lambda: f"🗑️ Hapus role **{g('role')}**",
        "give_role": lambda: f"➕ Beri role **{g('role')}** ke **{g('member')}**",
        "remove_role": lambda: f"➖ Cabut role **{g('role')}** dari **{g('member')}**",
        "edit_server": lambda: f"🏠 Ubah info server ({g('name') or 'nama tetap'})",
        "send_embed": lambda: f"📨 Kirim embed **{g('title')}** ke #{g('channel')}" + (f" (`{(g('description') or '')[:60]}…`)" if g('description') else ""),
        "purge_messages": lambda: f"🧹 Hapus **{g('limit')}** pesan terakhir di #{g('channel')}",
    }[t]()


# --- actions (whitelist; nothing destructive) -----------------------------
async def act_create_category(guild, a):
    c = await guild.create_category(a["name"], reason="AI setup (owner approved)")
    NEW_CATEGORIES[c.name.lower()] = c
    return f"kategori {c.name} dibuat"


async def act_create_text_channel(guild, a):
    cat = find_category(guild, a.get("category"))
    ow = None
    if a.get("read_only"):
        ow = {guild.default_role: discord.PermissionOverwrite(send_messages=False)}
    c = await guild.create_text_channel(a["name"], category=cat, topic=a.get("topic") or None,
                                        overwrites=ow or discord.utils.MISSING,
                                        reason="AI setup (owner approved)")
    return f"{c.mention} dibuat"


async def act_create_voice_channel(guild, a):
    cat = find_category(guild, a.get("category"))
    c = await guild.create_voice_channel(a["name"], category=cat, user_limit=int(a.get("user_limit") or 0),
                                         reason="AI setup (owner approved)")
    return f"voice {c.name} dibuat"


async def _chan(guild, a):
    c = find_channel(guild, a.get("channel"))
    if not c:
        raise ValueError(f"channel '{a.get('channel')}' tidak ditemukan")
    return c


async def act_rename_channel(guild, a):
    c = await _chan(guild, a)
    old = c.name
    await c.edit(name=a["new_name"], reason="AI setup (owner approved)")
    return f"#{old} → #{a['new_name']}"


async def act_move_channel(guild, a):
    c = await _chan(guild, a)
    cat = find_category(guild, a.get("category"))
    if not cat:
        raise ValueError(f"kategori '{a.get('category')}' tidak ditemukan")
    await c.edit(category=cat, reason="AI setup (owner approved)")
    return f"{c.mention} dipindah ke {cat.name}"


async def act_delete_channel(guild, a):
    c = await _chan(guild, a)
    name = c.name
    await c.delete(reason="AI setup (owner approved)")
    return f"channel #{name} dihapus"


async def act_set_topic(guild, a):
    c = await _chan(guild, a)
    await c.edit(topic=a["topic"], reason="AI setup (owner approved)")
    return f"topik {c.mention} diubah"


async def act_set_slowmode(guild, a):
    c = await _chan(guild, a)
    await c.edit(slowmode_delay=max(0, min(21600, int(a["seconds"]))), reason="AI setup (owner approved)")
    return f"slowmode {c.mention} diatur"


async def act_lock_channel(guild, a):
    c = await _chan(guild, a)
    await c.set_permissions(guild.default_role, send_messages=False, reason="AI setup (owner approved)")
    return f"{c.mention} dikunci"


async def act_unlock_channel(guild, a):
    c = await _chan(guild, a)
    await c.set_permissions(guild.default_role, send_messages=None, reason="AI setup (owner approved)")
    return f"{c.mention} dibuka"


async def act_hide_channel(guild, a):
    c = await _chan(guild, a)
    await c.set_permissions(guild.default_role, view_channel=False, reason="AI setup (owner approved)")
    shown = []
    for name in a.get("visible_to_roles") or []:
        r = find_role(guild, name)
        if r:
            await c.set_permissions(r, view_channel=True, reason="AI setup (owner approved)")
            shown.append(r.name)
    return f"{c.mention} disembunyikan (terlihat: {', '.join(shown) or 'admin saja'})"


async def act_create_role(guild, a):
    r = await guild.create_role(name=a["name"], colour=discord.Colour(color(a.get("color"))),
                                hoist=bool(a.get("hoist")), mentionable=bool(a.get("mentionable")),
                                permissions=discord.Permissions.none(), reason="AI setup (owner approved)")
    return f"role {r.mention} dibuat"


async def act_edit_role(guild, a):
    r = find_role(guild, a.get("role"))
    if not r:
        raise ValueError(f"role '{a.get('role')}' tidak ditemukan")
    kw = {}
    if a.get("new_name"):
        kw["name"] = a["new_name"]
    if a.get("color"):
        kw["colour"] = discord.Colour(color(a["color"]))
    if a.get("hoist") is not None:
        kw["hoist"] = bool(a["hoist"])
    await r.edit(**kw, reason="AI setup (owner approved)")
    return f"role {r.mention} diperbarui"


async def act_delete_role(guild, a):
    r = find_role(guild, a.get("role"))
    if not r:
        raise ValueError(f"role '{a.get('role')}' tidak ditemukan")
    name = r.name
    await r.delete(reason="AI setup (owner approved)")
    return f"role {name} dihapus"


async def act_give_role(guild, a):
    m = find_member(guild, a.get("member"))
    r = find_role(guild, a.get("role"))
    if not m or not r:
        raise ValueError("member atau role tidak ditemukan")
    await m.add_roles(r, reason="AI setup (owner approved)")
    return f"{r.mention} diberikan ke {m.mention}"


async def act_remove_role(guild, a):
    m = find_member(guild, a.get("member"))
    r = find_role(guild, a.get("role"))
    if not m or not r:
        raise ValueError("member atau role tidak ditemukan")
    await m.remove_roles(r, reason="AI setup (owner approved)")
    return f"{r.mention} dicabut dari {m.mention}"


async def act_edit_server(guild, a):
    kw = {}
    if a.get("name"):
        kw["name"] = a["name"]
    if a.get("description") is not None:
        kw["description"] = a["description"]
    await guild.edit(**kw, reason="AI setup (owner approved)")
    return f"info server diperbarui"


async def act_send_embed(guild, a):
    c = await _chan(guild, a)
    e = discord.Embed(title=a.get("title"), description=a.get("description"), colour=color(a.get("color")))
    e.set_footer(text="DEV Server")
    await c.send(embed=e)
    return f"embed terkirim ke {c.mention}"


async def act_purge_messages(guild, a):
    c = await _chan(guild, a)
    n = max(1, min(50, int(a.get("limit") or 5)))
    deleted = await c.purge(limit=n)
    return f"{len(deleted)} pesan di {c.mention} dihapus"


ACTIONS = {name[4:]: fn for name, fn in globals().items() if name.startswith("act_")}


# --- UI -------------------------------------------------------------------
class PlanView(discord.ui.View):
    def __init__(self, guild, actions):
        super().__init__(timeout=300)
        self.guild, self.actions = guild, actions

    async def interaction_check(self, interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message("⛔ Bot ini khusus owner.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Jalankan", emoji="✅", style=discord.ButtonStyle.success)
    async def run(self, interaction, _button):
        self.stop()
        await interaction.response.edit_message(view=None)
        lines = []
        for a in self.actions:
            try:
                lines.append("✅ " + await ACTIONS[a["type"]](self.guild, a))
            except Exception as exc:  # report and keep going
                lines.append(f"❌ {describe(a)} — {exc}")
        ok = all(l.startswith("✅") for l in lines)
        e = discord.Embed(title="Selesai" if ok else "Selesai (ada yang gagal)",
                          description="\n".join(lines), colour=GREEN if ok else RED)
        await interaction.followup.send(embed=e)

    @discord.ui.button(label="Batal", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, _button):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(title="Dibatalkan", colour=0x99AAB5), view=None)


# --- QA -------------------------------------------------------------------
def qa_embed(results, title):
    fails = [r for r in results if not r[1]]
    e = discord.Embed(title=f"{'🟢' if not fails else '🔴'} {title}",
                      description=f"**{len(results) - len(fails)}/{len(results)} lulus**",
                      colour=GREEN if not fails else RED)
    for name, ok, detail in results[:25]:
        e.add_field(name=f"{'✅' if ok else '❌'} {name}", value=str(detail)[:1000] or "-", inline=False)
    e.set_footer(text="BOT-QA • " + time.strftime("%d %b %Y %H:%M"))
    return e


async def live_tests(guild):
    out = []
    ch = guild.get_channel(QA_CHANNEL)
    t0 = time.monotonic()
    try:
        m = await ch.send("🧪 tes QA…")
        out.append(("kirim-pesan", True, f"{(time.monotonic() - t0) * 1000:.0f} ms"))
        await m.edit(content="🧪 tes QA (edit)")
        out.append(("edit-pesan", True, "ok"))
        await m.add_reaction("✅")
        out.append(("reaction", True, "ok"))
        await m.delete()
        out.append(("hapus-pesan", True, "ok"))
    except Exception as exc:
        out.append(("pesan", False, str(exc)))

    member = find_role(guild, "Member")
    everyone = guild.default_role
    bot_cat = guild.get_channel(BOT_CATEGORY)
    for role in (everyone, member):
        if not role:
            continue
        leak = [c.name for c in bot_cat.channels if c.permissions_for(role).view_channel] if bot_cat else []
        out.append((f"privasi-bot ({role.name})", not leak,
                    "kategori BOT tersembunyi" if not leak else f"bisa melihat: {leak}"))
    if member:
        gen = find_channel(guild, "general")
        p = gen.permissions_for(member) if gen else None
        ok = bool(p and p.view_channel and p.send_messages)
        out.append(("member-bisa-chat", ok, "#general bisa dibaca & ditulis" if ok else "Member tidak bisa chat di #general"))
        extras = {"add_reactions": "react", "attach_files": "kirim file", "embed_links": "link",
                  "connect": "masuk voice", "speak": "bicara di voice"}
        perms = member.permissions
        missing = [label for attr, label in extras.items() if not getattr(perms, attr)]
        out.append(("member-izin-dasar", not missing, "lengkap" if not missing else "tidak bisa: " + ", ".join(missing)))
    out.append(("latency", client.latency < 1.5, f"{client.latency * 1000:.0f} ms"))
    try:
        _, meta = await asyncio.to_thread(ai_plan, "halo", "-", False)
        tok = f" ({meta['tokens']} tok)" if meta.get("tokens") else ""
        out.append(("ai-brain", True, f"{meta['provider']} merespons{tok}"))
    except Exception as exc:
        out.append(("ai-brain", False, str(exc)[:200]))
    return out


# --- client ---------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

HELP = discord.Embed(
    title="🤖 ╎ DEV AI Assistant",
    colour=BLURPLE,
    description=(
        "Khusus owner di `#🤖╎bot-commands`. Mendukung **Full Kontrol Server** & **Ingatan Percakapan**.\n\n"
        "Contoh perintah AI:\n"
        "> bikinin kategori 🎮 GAMING isi #mabar dan voice Mabar 1\n"
        "> kasih role Moderator ke putra\n"
        "> ubah topik #💬╎general jadi ...\n\n"
        "Semua perubahan **ditampilkan dulu**, baru jalan setelah kamu tekan ✅."),
)
HELP.add_field(name="Admin Commands", value="`!qa` • `!qa test` • `!stats` • `!reset` • `!vdebug`", inline=False)
HELP.add_field(name="Panel Komunitas", value="`!panel-role` • `!panel-ticket`", inline=True)
HELP.add_field(name="Slash Commands Member", value="`/suggestion` • `/rank` • `/leaderboard`", inline=True)
HELP.add_field(name="Musik & Lofi 24/7", value="`!lofi` • `!lofi next` • `!play <judul>` • `!skip` • `!queue` • `!stop`", inline=False)
HELP.set_footer(text=f"Otak AI: Gemini ({GEMINI_MODEL}) • fallback Groq")


# --- Extra Features (Role Picker, Suggestion, Ticket, XP, Mod-Log, Temp Voice)
ROLE_PICKER_MAP = {
    "pick_pc": ("PC", "🖥️ PC"),
    "pick_mobile": ("Mobile", "📱 Mobile"),
    "pick_nightowl": ("🌙 ╎ Night Owl", "🌙 Night Owl"),
    "pick_chill": ("☕ ╎ Chill Vibes", "☕ Chill Vibes"),
    "pick_tryhard": ("🎯 ╎ Tryhard", "🎯 Tryhard"),
    "pick_creative": ("🎨 ╎ Creative", "🎨 Creative"),
    "pick_music": ("🎧 ╎ Music Lover", "🎧 Music Lover"),
}
TEMP_VOICE_HUB_NAME = "➕ ╎ Buat Room"
TEMP_VOICE_IDS = set()


class RolePickerView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _toggle(self, interaction, role_name, label):
        r = find_role(interaction.guild, role_name)
        if not r:
            await interaction.response.send_message("❌ Role tidak ditemukan.", ephemeral=True)
            return
        if r in interaction.user.roles:
            await interaction.user.remove_roles(r, reason="Self-role toggle")
            await interaction.response.send_message(f"➖ Role **{label}** dilepas.", ephemeral=True)
        else:
            await interaction.user.add_roles(r, reason="Self-role toggle")
            await interaction.response.send_message(f"✅ Role **{label}** dipasang!", ephemeral=True)

    @discord.ui.button(label="PC", emoji="🖥️", style=discord.ButtonStyle.primary, custom_id="pick_pc", row=0)
    async def pc_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_pc"])

    @discord.ui.button(label="Mobile", emoji="📱", style=discord.ButtonStyle.secondary, custom_id="pick_mobile", row=0)
    async def mobile_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_mobile"])

    @discord.ui.button(label="Night Owl", emoji="🌙", style=discord.ButtonStyle.secondary, custom_id="pick_nightowl", row=1)
    async def nightowl_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_nightowl"])

    @discord.ui.button(label="Chill Vibes", emoji="☕", style=discord.ButtonStyle.secondary, custom_id="pick_chill", row=1)
    async def chill_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_chill"])

    @discord.ui.button(label="Tryhard", emoji="🎯", style=discord.ButtonStyle.danger, custom_id="pick_tryhard", row=1)
    async def tryhard_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_tryhard"])

    @discord.ui.button(label="Creative", emoji="🎨", style=discord.ButtonStyle.primary, custom_id="pick_creative", row=1)
    async def creative_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_creative"])

    @discord.ui.button(label="Music Lover", emoji="🎧", style=discord.ButtonStyle.success, custom_id="pick_music", row=1)
    async def music_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_music"])


# --- Suggestion System (/suggestion) --------------------------------------
SUGGEST_VOTES = {}  # message_id -> {"up": set(uid), "down": set(uid)}


class SuggestionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _vote(self, interaction, kind):
        mid = interaction.message.id
        v = SUGGEST_VOTES.setdefault(mid, {"up": set(), "down": set()})
        uid = interaction.user.id
        other = "down" if kind == "up" else "up"
        v[other].discard(uid)
        if uid in v[kind]:
            v[kind].discard(uid)
        else:
            v[kind].add(uid)
        e = interaction.message.embeds[0]
        e.set_footer(text=f"👍 {len(v['up'])} Setuju  •  👎 {len(v['down'])} Tolak")
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="Setuju", emoji="👍", style=discord.ButtonStyle.success, custom_id="suggest_up")
    async def up_btn(self, interaction, _b):
        await self._vote(interaction, "up")

    @discord.ui.button(label="Tolak", emoji="👎", style=discord.ButtonStyle.danger, custom_id="suggest_down")
    async def down_btn(self, interaction, _b):
        await self._vote(interaction, "down")


@tree.command(name="suggestion", description="Kirim saran untuk server DEV", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(ide="Tulis saran atau ide kamu")
async def slash_suggestion(interaction: discord.Interaction, ide: str):
    e = discord.Embed(title="💡 ╎ Saran Baru", description=ide[:2000], colour=GOLD)
    e.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    e.set_footer(text="👍 0 Setuju  •  👎 0 Tolak")
    await interaction.response.send_message(embed=e, view=SuggestionView())
    msg = await interaction.original_response()
    try:
        await msg.create_thread(name=f"Diskusi: {ide[:60]}")
    except Exception:
        pass


# --- Ticket Support System ------------------------------------------------
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Tutup Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_btn(self, interaction, _b):
        await interaction.response.send_message("🔒 Ticket ditutup dalam 3 detik…")
        await asyncio.sleep(3)
        await interaction.channel.delete(reason=f"Ticket ditutup oleh {interaction.user}")


class TicketLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buka Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket_open")
    async def open_btn(self, interaction, _b):
        guild = interaction.guild
        safe = "".join(c for c in interaction.user.name.lower() if c.isalnum() or c == "-")[:18] or "user"
        cname = f"ticket-{safe}"
        existing = discord.utils.get(guild.text_channels, name=cname)
        if existing:
            await interaction.response.send_message(f"🎫 Ticket kamu sudah ada: {existing.mention}", ephemeral=True)
            return
        cat = guild.get_channel(COMMUNITY_CATEGORY)
        ow = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        for rid in (MOD_ROLE_ID, FOUNDER_ROLE_ID):
            role = guild.get_role(rid)
            if role:
                ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        ch = await guild.create_text_channel(cname, category=cat, overwrites=ow, reason="Support Ticket")
        e = discord.Embed(
            title="🎫 ╎ Ticket Support",
            description=f"Halo {interaction.user.mention}, silakan jelaskan kendala atau pertanyaanmu di sini.\n"
                        f"Staff akan segera membantu!",
            colour=BLURPLE,
        )
        await ch.send(embed=e, view=TicketCloseView())
        await interaction.response.send_message(f"✅ Ticket dibuat: {ch.mention}", ephemeral=True)


# --- XP & Leveling --------------------------------------------------------
XP_DATA = {}
XP_COOLDOWN = {}  # user_id -> last_timestamp
if os.path.exists(XP_FILE):
    try:
        XP_DATA = json.loads(read(XP_FILE))
    except Exception:
        XP_DATA = {}


def _save_xp():
    try:
        with open(XP_FILE, "w", encoding="utf-8") as fh:
            json.dump(XP_DATA, fh)
    except Exception:
        pass


def _xp_level(xp):
    lvl = 0
    while xp >= (lvl + 1) * (lvl + 1) * 100:
        lvl += 1
    return lvl


def _award_xp(user_id):
    import random
    now = time.monotonic()
    if now - XP_COOLDOWN.get(user_id, 0) < 30:
        return None
    XP_COOLDOWN[user_id] = now
    uid = str(user_id)
    old_xp = XP_DATA.get(uid, 0)
    new_xp = old_xp + random.randint(15, 25)
    XP_DATA[uid] = new_xp
    _save_xp()
    old_lvl, new_lvl = _xp_level(old_xp), _xp_level(new_xp)
    return new_lvl if new_lvl > old_lvl else None


def _rank_embed(member):
    xp = XP_DATA.get(str(member.id), 0)
    lvl = _xp_level(xp)
    next_xp = (lvl + 1) * (lvl + 1) * 100
    e = discord.Embed(title=f"🏅 ╎ Rank {member.display_name}", colour=GOLD)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Level", value=f"**{lvl}**", inline=True)
    e.add_field(name="XP", value=f"**{xp}** / {next_xp}", inline=True)
    return e


def _leaderboard_embed(guild):
    top = sorted(XP_DATA.items(), key=lambda kv: -kv[1])[:10]
    lines = []
    for i, (uid, xp) in enumerate(top, 1):
        m = guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        lines.append(f"`{i}.` **{name}** — Level **{_xp_level(xp)}** ({xp} XP)")
    return discord.Embed(
        title=f"🏆 ╎ Leaderboard {guild.name}",
        description="\n".join(lines) or "Belum ada data XP.",
        colour=GOLD,
    )


@tree.command(name="rank", description="Cek level & XP kamu", guild=discord.Object(id=GUILD_ID))
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.send_message(embed=_rank_embed(member or interaction.user))


@tree.command(name="leaderboard", description="Lihat top 10 member paling aktif", guild=discord.Object(id=GUILD_ID))
async def slash_leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_leaderboard_embed(interaction.guild))


def _has_mod_perm(member, perm_name):
    return member.id == OWNER_ID or getattr(member.guild_permissions, perm_name, False) or member.guild_permissions.administrator


def _parse_target_id(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return int(digits) if len(digits) >= 17 else None


async def _do_ban(guild, actor, target_id, reason):
    if target_id in (OWNER_ID, guild.me.id):
        return "❌ Tidak bisa ban owner atau bot sendiri."
    try:
        await guild.ban(discord.Object(id=target_id), reason=f"{actor} | {reason}"[:512])
    except discord.Forbidden:
        return "❌ Bot tidak punya izin ban atau role target lebih tinggi dari role bot."
    except discord.HTTPException as exc:
        return f"❌ Gagal ban: `{exc}`"
    await _send_modlog(guild, "🔨 Member Di-ban", f"Target: <@{target_id}> (`{target_id}`)\nOleh: {actor.mention}\nAlasan: {reason}", RED)
    return f"🔨 <@{target_id}> (`{target_id}`) berhasil di-ban. Alasan: **{reason}**"


async def _do_unban(guild, actor, target_id, reason):
    try:
        await guild.unban(discord.Object(id=target_id), reason=f"{actor} | {reason}"[:512])
    except discord.NotFound:
        return "❌ User tersebut tidak ada di daftar ban."
    except discord.HTTPException as exc:
        return f"❌ Gagal unban: `{exc}`"
    await _send_modlog(guild, "🔓 Member Di-unban", f"Target: <@{target_id}> (`{target_id}`)\nOleh: {actor.mention}\nAlasan: {reason}", GREEN)
    return f"🔓 <@{target_id}> (`{target_id}`) berhasil di-unban."


async def _do_kick(guild, actor, member, reason):
    if member.id in (OWNER_ID, guild.me.id):
        return "❌ Tidak bisa kick owner atau bot sendiri."
    try:
        await member.kick(reason=f"{actor} | {reason}"[:512])
    except discord.Forbidden:
        return "❌ Bot tidak punya izin kick atau role target lebih tinggi dari role bot."
    except discord.HTTPException as exc:
        return f"❌ Gagal kick: `{exc}`"
    await _send_modlog(guild, "👢 Member Di-kick", f"Target: {member.mention} (`{member.id}`)\nOleh: {actor.mention}\nAlasan: {reason}", GOLD)
    return f"👢 {member.mention} berhasil di-kick. Alasan: **{reason}**"


@tree.command(name="ban", description="Ban user dari server", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(user="Member yang mau di-ban", alasan="Alasan ban (opsional)")
async def slash_ban(interaction: discord.Interaction, user: discord.User, alasan: str = "Tidak ada alasan"):
    if not _has_mod_perm(interaction.user, "ban_members"):
        await interaction.response.send_message("❌ Kamu tidak punya izin untuk ban.", ephemeral=True)
        return
    await interaction.response.send_message(await _do_ban(interaction.guild, interaction.user, user.id, alasan))


@tree.command(name="unban", description="Unban user berdasarkan User ID", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(user_id="ID user yang mau di-unban", alasan="Alasan unban (opsional)")
async def slash_unban(interaction: discord.Interaction, user_id: str, alasan: str = "Tidak ada alasan"):
    if not _has_mod_perm(interaction.user, "ban_members"):
        await interaction.response.send_message("❌ Kamu tidak punya izin untuk unban.", ephemeral=True)
        return
    tid = _parse_target_id(user_id)
    if not tid:
        await interaction.response.send_message("❌ User ID tidak valid.", ephemeral=True)
        return
    await interaction.response.send_message(await _do_unban(interaction.guild, interaction.user, tid, alasan))


@tree.command(name="kick", description="Kick member dari server", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(member="Member yang mau di-kick", alasan="Alasan kick (opsional)")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, alasan: str = "Tidak ada alasan"):
    if not _has_mod_perm(interaction.user, "kick_members"):
        await interaction.response.send_message("❌ Kamu tidak punya izin untuk kick.", ephemeral=True)
        return
    await interaction.response.send_message(await _do_kick(interaction.guild, interaction.user, member, alasan))


def _members_page(guild, page=0, per_page=20):
    all_m = sorted(guild.members, key=lambda m: (m.bot, -m.top_role.position, m.display_name.lower()))
    humans = sum(1 for m in all_m if not m.bot)
    bots = len(all_m) - humans
    total_pages = max(1, (len(all_m) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = all_m[page * per_page:(page + 1) * per_page]
    lines = []
    for idx, m in enumerate(chunk, start=page * per_page + 1):
        tag = "🤖" if m.bot else "👤"
        top = m.top_role.name if m.top_role and m.top_role.name != "@everyone" else "-"
        lines.append(f"`{idx}.` {tag} {m.mention} (`{m.name}` • `{m.id}`) — *{top}*")
    e = discord.Embed(
        title=f"👥 ╎ Daftar Member {guild.name}",
        description="\n".join(lines) if lines else "Belum ada member.",
        colour=BLURPLE,
    )
    e.set_footer(text=f"Total: {len(all_m)} ({humans} user, {bots} bot) • Hal {page + 1}/{total_pages}")
    return e, total_pages


class MembersView(discord.ui.View):
    def __init__(self, guild, page=0):
        super().__init__(timeout=180)
        self.guild = guild
        self.page = page

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction, _b):
        self.page = max(0, self.page - 1)
        e, _ = _members_page(self.guild, self.page)
        await interaction.response.edit_message(embed=e, view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction, _b):
        _, total = _members_page(self.guild, self.page)
        self.page = min(total - 1, self.page + 1)
        e, _ = _members_page(self.guild, self.page)
        await interaction.response.edit_message(embed=e, view=self)


@tree.command(name="members", description="Lihat daftar semua member di server", guild=discord.Object(id=GUILD_ID))
async def slash_members(interaction: discord.Interaction):
    e, total = _members_page(interaction.guild, 0)
    await interaction.response.send_message(embed=e, view=MembersView(interaction.guild, 0) if total > 1 else None)


# --- Mod-Log Helpers ------------------------------------------------------
async def _send_modlog(guild, title, description, colour=BLURPLE):
    ch = guild.get_channel(MODLOG_CHANNEL)
    if not ch:
        return
    e = discord.Embed(title=title, description=description[:2000], colour=colour)
    e.set_footer(text=time.strftime("%d %b %Y %H:%M"))
    try:
        await ch.send(embed=e)
    except Exception:
        pass


@client.event
async def on_member_remove(member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    await _send_modlog(member.guild, "📤 Member Keluar", f"{member.mention} (`{member.name}`)", RED)


@client.event
async def on_message_delete(msg):
    if not msg.guild or msg.guild.id != GUILD_ID or msg.author.bot:
        return
    content = msg.content[:500] if msg.content else "_(tanpa teks / lampiran)_"
    await _send_modlog(
        msg.guild,
        "🗑️ Pesan Dihapus",
        f"**Pengirim:** {msg.author.mention} di {msg.channel.mention}\n**Isi:** {content}",
        RED,
    )


@client.event
async def on_message_edit(before, after):
    if not after.guild or after.guild.id != GUILD_ID or after.author.bot:
        return
    if before.content == after.content:
        return
    await _send_modlog(
        after.guild,
        "✏️ Pesan Diedit",
        f"**Pengirim:** {after.author.mention} di {after.channel.mention}\n"
        f"**Sebelum:** {before.content[:300]}\n**Sesudah:** {after.content[:300]}",
        GOLD,
    )


@client.event
async def on_voice_state_update(member, before, after):
    if member.guild.id != GUILD_ID:
        return
    if after.channel and after.channel.name == TEMP_VOICE_HUB_NAME:
        cat = after.channel.category
        room = await member.guild.create_voice_channel(
            f"🔊 ╎ {member.display_name}", category=cat, reason="Join-to-Create Voice"
        )
        TEMP_VOICE_IDS.add(room.id)
        try:
            await member.move_to(room)
        except Exception:
            pass
    if before.channel and before.channel.id in TEMP_VOICE_IDS and len(before.channel.members) == 0:
        TEMP_VOICE_IDS.discard(before.channel.id)
        try:
            await before.channel.delete(reason="Temp voice kosong")
        except Exception:
            pass
    vc = member.guild.voice_client
    if vc and vc.is_connected() and vc.channel:
        humans = [m for m in vc.channel.members if not m.bot]
        if not humans:
            MUSIC_QUEUE.clear()
            NOW_PLAYING["track"] = None
            try:
                vc.stop()
                await vc.disconnect()
            except Exception:
                pass


# --- Lofi Radio & Search-by-Title Music Player -----------------------------
LOFI_STATIONS = [
    ("lofi", "☕ Lofi Beats Radio", "https://play.streamafrica.net/lofiradio"),
    ("chillsynth", "🌃 Nightride Chillsynth", "https://stream.nightride.fm/chillsynth.mp3"),
    ("groove", "🌿 SomaFM Groove Salad", "https://ice2.somafm.com/groovesalad-128-mp3"),
    ("lush", "🌙 SomaFM Lush", "https://ice2.somafm.com/lush-128-mp3"),
]
LOFI_STATE = {"idx": 0}
MUSIC_QUEUE = []
NOW_PLAYING = {"track": None}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
VOICE_LOGS = collections.deque(maxlen=25)


class _VoiceLogHandler(logging.Handler):
    def emit(self, record):
        msg = self.format(record)
        low = msg.lower()
        if any(k in low for k in ("dave", "mls", "transition", "epoch", "encrypt", "voice", "error", "exception", "fail", "close")):
            VOICE_LOGS.append(msg[:220])


def _setup_voice_logging():
    h = _VoiceLogHandler()
    h.setLevel(logging.DEBUG)
    h.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    for name in ("discord.gateway", "discord.voice_state", "discord.voice_client"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        if not any(isinstance(x, _VoiceLogHandler) for x in lg.handlers):
            lg.addHandler(h)


_setup_voice_logging()


def _search_track(query):
    import yt_dlp
    opts = {
        "format": "bestaudio[protocol^=http][ext!=m3u8]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
    }
    candidates = [query] if query.startswith(("http://", "https://")) else [f"ytsearch1:{query}", f"scsearch1:{query}"]
    last_err = None
    with yt_dlp.YoutubeDL(opts) as ydl:
        for target in candidates:
            try:
                info = ydl.extract_info(target, download=False)
                if "entries" in info:
                    entries = [e for e in (info.get("entries") or []) if e]
                    if not entries:
                        continue
                    info = entries[0]
                stream_url = info.get("url")
                if not stream_url:
                    continue
                return {
                    "title": info.get("title") or query,
                    "url": stream_url,
                    "webpage": info.get("webpage_url") or "",
                    "headers": info.get("http_headers") or {},
                }
            except Exception as exc:
                last_err = exc
    raise RuntimeError(f"Lagu tidak ditemukan ({last_err or 'kosong'})")


def _ensure_opus():
    if not discord.opus.is_loaded():
        for name in ("libopus.so.0", "libopus.so", "opus"):
            try:
                discord.opus.load_opus(name)
                break
            except Exception:
                continue


def _make_audio_source(url, headers=None):
    _ensure_opus()
    is_hls = ".m3u8" in url
    before = [] if is_hls else [FFMPEG_BEFORE]
    if headers:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() in ("user-agent", "referer"))
        if hdr:
            before.append(f'-headers "{hdr}"')
    before_str = " ".join(before) or None
    if discord.opus.is_loaded():
        raw = discord.FFmpegPCMAudio(url, before_options=before_str, options="-vn")
        return discord.PCMVolumeTransformer(raw, volume=1.0)
    return discord.FFmpegOpusAudio(url, before_options=before_str, options="-vn")


async def _wait_dave_ready(vc, timeout=5.0):
    conn = getattr(vc, "_connection", None)
    if not conn:
        await asyncio.sleep(0.5)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not vc.is_connected():
            return
        dave_ver = getattr(conn, "dave_protocol_version", 0)
        can_enc = getattr(conn, "can_encrypt", False)
        if dave_ver == 0 or can_enc:
            await asyncio.sleep(0.2)
            return
        await asyncio.sleep(0.1)


def _play_next_in_queue(guild, text_channel=None):
    vc = guild.voice_client
    if not vc or not vc.is_connected():
        MUSIC_QUEUE.clear()
        NOW_PLAYING["track"] = None
        return
    if not MUSIC_QUEUE:
        NOW_PLAYING["track"] = None
        return
    track = MUSIC_QUEUE.pop(0)
    NOW_PLAYING["track"] = track
    source = _make_audio_source(track["url"], track.get("headers"))

    def _after(err):
        if err:
            log("music stream err:", err)
        fut = asyncio.run_coroutine_threadsafe(_on_track_end(guild, text_channel, err), client.loop)
        try:
            fut.result()
        except Exception:
            pass

    vc.play(source, after=_after)


async def _on_track_end(guild, text_channel, err=None):
    if err and text_channel:
        await text_channel.send(f"⚠️ Error audio stream: `{str(err)[:160]}`")
    if MUSIC_QUEUE:
        _play_next_in_queue(guild, text_channel)
        t = NOW_PLAYING["track"]
        if text_channel and t:
            await text_channel.send(embed=_music_embed(t, guild.voice_client.channel.name), view=LofiView())
    else:
        NOW_PLAYING["track"] = None


def _play_station(vc, idx):
    MUSIC_QUEUE.clear()
    idx = idx % len(LOFI_STATIONS)
    LOFI_STATE["idx"] = idx
    _key, title, url = LOFI_STATIONS[idx]
    NOW_PLAYING["track"] = {"title": title, "url": url, "webpage": "", "requester": "Radio 24/7"}
    if vc.is_playing() or vc.is_paused():
        vc.stop()
    source = _make_audio_source(url)
    vc.play(source, after=lambda err: log("lofi stream end:", err) if err else None)
    return title


def _music_embed(track, channel_name):
    title = track["title"]
    link = f"[{title}]({track['webpage']})" if track.get("webpage") else f"**{title}**"
    q_info = f"\nAntrian berikutnya: **{len(MUSIC_QUEUE)}** lagu" if MUSIC_QUEUE else ""
    e = discord.Embed(
        title="🎵 ╎ Sedang Diputar",
        description=f"{link}\nVoice: **{channel_name}**{q_info}",
        colour=0x9B59B6,
    )
    e.set_footer(text=f"Diminta oleh {track.get('requester', 'member')} • !play <judul> | !skip | !stop")
    return e


def _lofi_embed(title, channel_name):
    e = discord.Embed(
        title="🎧 ╎ Lofi Radio 24/7",
        description=f"Sedang memutar **{title}** di **{channel_name}**\n"
                    f"Perintah: `!lofi` • `!lofi next` • `!play <judul>` • `!stop`",
        colour=0x9B59B6,
    )
    e.set_footer(text="Otomatis keluar jika voice kosong")
    return e


class LofiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Skip / Ganti", emoji="⏭️", style=discord.ButtonStyle.primary, custom_id="lofi_next")
    async def next_btn(self, interaction, _b):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            await interaction.response.send_message("🔇 Bot tidak sedang di voice channel.", ephemeral=True)
            return
        if MUSIC_QUEUE:
            vc.stop()
            await interaction.response.send_message("⏭️ Lagu di-skip!", ephemeral=True)
            return
        title = _play_station(vc, LOFI_STATE["idx"] + 1)
        await interaction.response.edit_message(embed=_lofi_embed(title, vc.channel.name), view=self)

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="lofi_stop")
    async def stop_btn(self, interaction, _b):
        MUSIC_QUEUE.clear()
        NOW_PLAYING["track"] = None
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()
        await interaction.response.edit_message(
            embed=discord.Embed(title="⏹️ ╎ Musik Dimatikan", colour=0x99AAB5), view=None
        )


async def _ensure_voice(msg):
    if not msg.author.voice or not msg.author.voice.channel:
        await msg.reply("🎧 Masuk ke voice channel dulu!", mention_author=False)
        return None
    voice_ch = msg.author.voice.channel
    vc = msg.guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != voice_ch.id:
            await vc.move_to(voice_ch)
            await _wait_dave_ready(vc)
    else:
        vc = await voice_ch.connect(timeout=30, self_deaf=True)
        await _wait_dave_ready(vc)
    me_voice = msg.guild.me.voice if msg.guild.me else None
    if me_voice and me_voice.suppress:
        try:
            await msg.guild.me.edit(suppress=False)
        except Exception:
            pass
    if me_voice and me_voice.mute:
        await msg.channel.send("⚠️ Bot sedang di-**Server Mute** di voice channel, unmute bot agar suaranya terdengar.")
    return vc


async def handle_music_command(msg, text):
    import shutil
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    guild = msg.guild
    vc = guild.voice_client

    if cmd in ("!stop", "!leave") or (cmd == "!lofi" and arg.lower() in ("stop", "leave", "keluar", "mati")):
        MUSIC_QUEUE.clear()
        NOW_PLAYING["track"] = None
        if not vc:
            await msg.reply("🔇 Bot tidak sedang di voice channel.", mention_author=False)
            return
        vc.stop()
        await vc.disconnect()
        await msg.reply("⏹️ Musik dimatikan & keluar dari voice.", mention_author=False)
        return

    if cmd in ("!queue", "!q"):
        cur = NOW_PLAYING.get("track")
        lines = [f"**Sedang diputar:** {cur['title']}" if cur else "**Sedang diputar:** tidak ada"]
        for i, t in enumerate(MUSIC_QUEUE[:10], 1):
            lines.append(f"`{i}.` {t['title']}")
        await msg.reply(embed=discord.Embed(title="🎶 Antrian Lagu", description="\n".join(lines), colour=0x9B59B6),
                        mention_author=False)
        return

    if cmd in ("!skip", "!next") or (cmd == "!lofi" and arg.lower() in ("next", "skip")):
        if not vc or not vc.is_connected():
            await msg.reply("🔇 Bot tidak sedang di voice.", mention_author=False)
            return
        if MUSIC_QUEUE:
            vc.stop()
            await msg.reply("⏭️ Skip ke lagu berikutnya.", mention_author=False)
        else:
            title = _play_station(vc, LOFI_STATE["idx"] + 1)
            await msg.reply(embed=_lofi_embed(title, vc.channel.name), view=LofiView(), mention_author=False)
        return

    if cmd == "!lofi" and arg.lower() in ("list", "stations", "stasiun"):
        lines = "\n".join(f"• `{k}` — {t}" for k, t, _ in LOFI_STATIONS)
        await msg.reply(embed=discord.Embed(title="📻 Daftar Stasiun Lofi", description=lines, colour=0x9B59B6),
                        mention_author=False)
        return

    station_keys = {k: i for i, (k, _t, _u) in enumerate(LOFI_STATIONS)}
    if cmd == "!lofi" and (not arg or arg.lower() in station_keys):
        vc = await _ensure_voice(msg)
        if not vc:
            return
        idx = station_keys.get(arg.lower(), LOFI_STATE["idx"])
        title = _play_station(vc, idx)
        await msg.reply(embed=_lofi_embed(title, vc.channel.name), view=LofiView(), mention_author=False)
        await asyncio.sleep(2.5)
        if vc.is_connected() and not vc.is_playing():
            await msg.channel.send(
                f"⚠️ Stream `{title}` langsung berhenti (`opus={discord.opus.is_loaded()}`, `ffmpeg={bool(shutil.which('ffmpeg'))}`). "
                f"Coba `!lofi next` untuk ganti stasiun."
            )
        return

    query = arg
    if not query:
        await msg.reply("🔎 Tulis judul lagunya, contoh: `!play lofi hip hop` atau `!play judul lagu`", mention_author=False)
        return
    vc = await _ensure_voice(msg)
    if not vc:
        return
    async with msg.channel.typing():
        track = await asyncio.to_thread(_search_track, query)
    track["requester"] = msg.author.display_name
    if vc.is_playing() or vc.is_paused():
        MUSIC_QUEUE.append(track)
        await msg.reply(f"➕ Ditambahkan ke antrian (#{len(MUSIC_QUEUE)}): **{track['title']}**", mention_author=False)
    else:
        MUSIC_QUEUE.append(track)
        _play_next_in_queue(guild, msg.channel)
        await msg.reply(embed=_music_embed(track, vc.channel.name), view=LofiView(), mention_author=False)


async def background_qa_and_autorole():
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            guild = client.get_guild(GUILD_ID)
            if guild:
                member_role = guild.get_role(MEMBER_ROLE_ID)
                if member_role:
                    for m in guild.members:
                        if not m.bot and member_role not in m.roles:
                            try:
                                await m.add_roles(member_role, reason="Auto-assign role Member")
                            except Exception:
                                pass
        except Exception as exc:
            log("bg loop error:", exc)
        await asyncio.sleep(300)


def start_health_server():
    port = int(os.environ.get("PORT", "8080"))
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DEV Bot Online")
        def log_message(self, *_args):
            pass
    threading.Thread(target=http.server.HTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()


@client.event
async def on_ready():
    client.add_view(RolePickerView())
    client.add_view(SuggestionView())
    client.add_view(TicketLauncherView())
    client.add_view(TicketCloseView())
    client.add_view(LofiView())
    if not getattr(client, "_bg_started", False):
        client._bg_started = True
        try:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
        except Exception as exc:
            log("tree sync error:", exc)
        client.loop.create_task(background_qa_and_autorole())
    log(f"online sebagai {client.user} — melayani owner {OWNER_ID} di #commands")


@client.event
async def on_member_join(member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    await _send_modlog(member.guild, "📥 Member Bergabung", f"{member.mention} (`{member.name}`)", GREEN)
    ch = member.guild.get_channel(WELCOME_CHANNEL) or discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and "welcome" in c.name.lower(),
        member.guild.channels,
    )
    if ch:
        e = discord.Embed(
            title="👋 ╎ Member Baru Bergabung!",
            description=(
                f"Selamat datang {member.mention} di **{member.guild.name}**! 🎉\n"
                f"• Baca aturan di <#1573525422585684185>\n"
                f"• Ambil role di <#1573524001240780820>\n"
                f"• Mulai ngobrol di <#1573525427228516382>"
            ),
            colour=0xEB459E,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"Member ke-{member.guild.member_count}")
        await ch.send(embed=e)


@client.event
async def on_message(msg):
    if msg.author.bot or not msg.guild or msg.guild.id != GUILD_ID:
        return
    new_lvl = _award_xp(msg.author.id)
    if new_lvl is not None:
        try:
            await msg.channel.send(f"🎉 Selamat {msg.author.mention}, kamu naik ke **Level {new_lvl}**!")
        except Exception:
            pass
    text = msg.content.strip()
    cmd_word = text.split(maxsplit=1)[0].lower() if text else ""
    if cmd_word == "!rank":
        await msg.reply(embed=_rank_embed(msg.author), mention_author=False)
        return
    if cmd_word in ("!top", "!leaderboard", "!lb"):
        await msg.reply(embed=_leaderboard_embed(msg.guild), mention_author=False)
        return
    if cmd_word == "!vdebug":
        import shutil
        _ensure_opus()
        vc = msg.guild.voice_client
        conn = getattr(vc, "_connection", None) if vc else None
        me_v = msg.guild.me.voice if msg.guild.me else None
        try:
            import davey
            has_davey = getattr(davey, "__version__", "installed")
        except Exception:
            has_davey = False
        ds = getattr(conn, "dave_session", None) if conn else None
        info = [
            f"discord.py: `{discord.__version__}`",
            f"opus_loaded: `{discord.opus.is_loaded()}`",
            f"ffmpeg: `{shutil.which('ffmpeg')}`",
            f"davey: `{has_davey}`",
            f"dave_version: `{getattr(conn, 'dave_protocol_version', '-') if conn else '-'}`",
            f"dave_session: `{ds is not None}` | ready: `{getattr(ds, 'ready', '-') if ds else '-'}` | epoch: `{getattr(ds, 'epoch', '-') if ds else '-'}`",
            f"can_encrypt: `{getattr(conn, 'can_encrypt', '-') if conn else '-'}`",
            f"vc_connected: `{vc.is_connected() if vc else False}`",
            f"vc_playing: `{vc.is_playing() if vc else False}`",
            f"vc_channel: `{vc.channel.name if vc and vc.channel else None}`",
            f"vc_latency: `{round(vc.latency * 1000)} ms`" if vc else "vc_latency: `-`",
            f"server_muted: `{me_v.mute if me_v else '-'}` | suppressed: `{me_v.suppress if me_v else '-'}`",
        ]
        if VOICE_LOGS:
            info.append("```text\n" + "\n".join(list(VOICE_LOGS)[-12:]) + "\n```")
        await msg.reply("\n".join(info), mention_author=False)
        return
    if cmd_word in ("!lofi", "!play", "!p", "!skip", "!next", "!queue", "!q", "!stop", "!leave"):
        try:
            await handle_music_command(msg, text)
        except Exception as exc:
            log("music error:", exc)
            await msg.reply(f"❌ Gagal memutar audio: `{str(exc)[:180]}`", mention_author=False)
        return
    if cmd_word in ("!members", "!member", "!listmember"):
        e, total = _members_page(msg.guild, 0)
        await msg.reply(embed=e, view=MembersView(msg.guild, 0) if total > 1 else None, mention_author=False)
        return
    if cmd_word in ("!ban", "!unban", "!kick"):
        parts = text.split(maxsplit=2)
        target_arg = parts[1] if len(parts) > 1 else ""
        reason = parts[2] if len(parts) > 2 else "Tidak ada alasan"
        if cmd_word == "!ban":
            if not _has_mod_perm(msg.author, "ban_members"):
                await msg.reply("❌ Kamu tidak punya izin untuk ban.", mention_author=False)
                return
            tid = msg.mentions[0].id if msg.mentions else _parse_target_id(target_arg)
            if not tid:
                await msg.reply("Pakai: `!ban @user [alasan]` atau `!ban <user_id> [alasan]`", mention_author=False)
                return
            await msg.reply(await _do_ban(msg.guild, msg.author, tid, reason), mention_author=False)
            return
        if cmd_word == "!unban":
            if not _has_mod_perm(msg.author, "ban_members"):
                await msg.reply("❌ Kamu tidak punya izin untuk unban.", mention_author=False)
                return
            tid = _parse_target_id(target_arg)
            if not tid:
                await msg.reply("Pakai: `!unban <user_id> [alasan]`", mention_author=False)
                return
            await msg.reply(await _do_unban(msg.guild, msg.author, tid, reason), mention_author=False)
            return
        if cmd_word == "!kick":
            if not _has_mod_perm(msg.author, "kick_members"):
                await msg.reply("❌ Kamu tidak punya izin untuk kick.", mention_author=False)
                return
            tid = msg.mentions[0].id if msg.mentions else _parse_target_id(target_arg)
            member = msg.guild.get_member(tid) if tid else None
            if not member:
                await msg.reply("Pakai: `!kick @user [alasan]`", mention_author=False)
                return
            await msg.reply(await _do_kick(msg.guild, msg.author, member, reason), mention_author=False)
            return
    if msg.author.id != OWNER_ID or msg.channel.id != COMMANDS_CHANNEL:
        return
    if not text:
        return
    guild = msg.guild
    low = text.lower()

    if low in ("!help", "help", "!bantuan"):
        await msg.reply(embed=HELP, mention_author=False)
        return
    if low == "!reset":
        CHAT_HISTORY.clear()
        _save_memory()
        await msg.reply("🧠 Ingatan percakapan AI sudah di-reset.", mention_author=False)
        return
    if low == "!panel-role":
        w = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and "role" in c.name.lower(),
            guild.channels,
        ) or guild.get_channel(WELCOME_CHANNEL)
        e = discord.Embed(
            title="🎭 ╎ PILIH ROLE KAMU",
            description="Klik tombol di bawah untuk memasang atau melepas role kamu!",
            colour=BLURPLE,
        )
        e.add_field(name="🖥️ Platform", value="PC • Mobile", inline=False)
        e.add_field(name="✨ Vibes & Keren-kerenan", value="🌙 Night Owl • ☕ Chill Vibes • 🎯 Tryhard • 🎨 Creative • 🎧 Music Lover", inline=False)
        e.set_footer(text="DEV Server • Self-Role Picker")
        await (w or msg.channel).send(embed=e, view=RolePickerView())
        if w and w.id != msg.channel.id:
            await msg.reply(f"✅ Panel pilih role dikirim ke {w.mention}.", mention_author=False)
        return
    if low == "!panel-ticket":
        g = guild.get_channel(1572712078295437413) or msg.channel
        e = discord.Embed(
            title="🎫 ╎ BANTUAN & TICKET SUPPORT",
            description="Butuh bantuan staff, lapor masalah, atau ada keperluan khusus?\n"
                        "Klik tombol **🎫 Buka Ticket** di bawah untuk membuat channel privat dengan staff.",
            colour=BLURPLE,
        )
        e.set_footer(text="DEV Server • Support Ticket")
        await g.send(embed=e, view=TicketLauncherView())
        if g.id != msg.channel.id:
            await msg.reply(f"✅ Panel ticket dikirim ke {g.mention}.", mention_author=False)
        return
    if low == "!stats":
        g = guild
        bots = sum(1 for m in g.members if m.bot)
        humans = g.member_count - bots
        e = discord.Embed(title=f"📊 ╎ STATISTIK LIVE {g.name}", colour=0x3498DB)
        e.add_field(name="👥 Member", value=f"{humans} orang ({bots} bot)", inline=True)
        e.add_field(name="💬 Channel", value=f"{len(g.text_channels)} teks · {len(g.voice_channels)} voice", inline=True)
        e.add_field(name="🏷️ Role", value=str(len(g.roles)), inline=True)
        e.add_field(name="⚡ Latency", value=f"{client.latency * 1000:.0f} ms", inline=True)
        e.set_footer(text="DEV Server • Live Telemetry")
        await msg.reply(embed=e, mention_author=False)
        return
    if low == "!qa":
        async with msg.channel.typing():
            results, _ = await asyncio.to_thread(qa.run_pass)
        await msg.reply(embed=qa_embed(results, "QA Struktur Server"), mention_author=False)
        return
    if low == "!qa test":
        async with msg.channel.typing():
            results = await live_tests(guild)
        e = qa_embed(results, "QA Tes Langsung")
        await msg.reply(embed=e, mention_author=False)
        if msg.channel.id != QA_CHANNEL:
            await guild.get_channel(QA_CHANNEL).send(embed=e)
        return

    async with msg.channel.typing():
        try:
            plan, meta = await asyncio.to_thread(ai_plan, text, server_summary(guild, text))
        except Exception as exc:
            log("ai error:", exc)
            await msg.reply(embed=discord.Embed(title="AI error", description=str(exc)[:1500], colour=RED),
                            mention_author=False)
            return
    tok_info = f" • {meta['tokens']} tok" if meta.get("tokens") else ""
    footer_ai = f"{meta['provider']}{tok_info}"
    e = discord.Embed(description=plan["reply"][:3000] or "​", colour=GOLD if plan["actions"] else BLURPLE)
    if not plan["actions"]:
        e.set_footer(text=footer_ai)
        await msg.reply(embed=e, mention_author=False)
        return
    e.title = "📋 Rencana — tekan ✅ untuk menjalankan"
    e.add_field(name=f"{len(plan['actions'])} aksi", value="\n".join(describe(a) for a in plan["actions"])[:1024],
                inline=False)
    e.set_footer(text=f"{footer_ai} • Berlaku 5 menit")
    await msg.reply(embed=e, view=PlanView(guild, plan["actions"]), mention_author=False)


if __name__ == "__main__":
    start_health_server()
    try:
        client.run(get_discord_token(), log_handler=None)
    except discord.PrivilegedIntentsRequired:
        sys.exit("MESSAGE_CONTENT intent belum aktif di Developer Portal")
