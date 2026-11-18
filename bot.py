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
import http.server
import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

import discord

GUILD_ID = 1572541420298829834
OWNER_ID = 1409498347294822502
COMMANDS_CHANNEL = 1572712087208329317  # #bot-commands
QA_CHANNEL = 1572712112260653246        # #mod-log
BOT_CATEGORY = 1572712069038604348      # #STAFF
IDEAS_CHANNEL = 0
STATS_CHANNEL = 1572712087208329317
WELCOME_CHANNEL = 1572712074390544465   # #welcome
MEMBER_ROLE_ID = 1572712053175754883    # Member
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

Fitur bot yang BENAR-BENAR ADA: !help, !qa, !qa test, !stats, !panel-role, !reset, !play <judul>, !skip, !queue, !stop, !lofi [next|stop], auto-role Member, welcome embed, temp voice "➕ ╎ Buat Room".
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

HELP = discord.Embed(
    title="🤖 ╎ DEV AI Assistant",
    colour=BLURPLE,
    description=(
        "Khusus owner. Sekarang mendukung **Full Kontrol Server** & **Ingatan Percakapan**.\n\n"
        "Contoh perintah:\n"
        "> bikinin kategori 🎮 GAMING isi #mabar dan voice Mabar 1\n"
        "> kasih role VIP ke putra\n"
        "> hapus channel #tes-ai / hapus role Tes\n"
        "> ubah topik #general jadi ... / hapus 10 pesan terakhir di #general\n\n"
        "Semua perubahan **ditampilkan dulu**, baru jalan setelah kamu tekan ✅."),
)
HELP.add_field(name="!qa", value="Cek struktur server", inline=True)
HELP.add_field(name="!qa test", value="Tes langsung", inline=True)
HELP.add_field(name="!reset", value="Hapus ingatan chat AI", inline=True)
HELP.add_field(name="!lofi", value="Radio lofi 24/7 (`!lofi next` / `!lofi stop`)", inline=True)
HELP.add_field(name="!play <judul>", value="Cari & putar lagu sesuai judul (`!skip`, `!queue`, `!stop`)", inline=True)
HELP.set_footer(text=f"Otak AI: Gemini ({GEMINI_MODEL}) • fallback Groq")


# --- Extra Features (Role Picker, Temp Voice, Auto-Ideas, Live Stats) -----
ROLE_PICKER_MAP = {
    "pick_dev": ("💻 ╎ Developer", "💻 Developer"),
    "pick_qa": ("🧪 ╎ QA Tester", "🧪 QA Tester"),
    "pick_creator": ("🎨 ╎ Creator", "🎨 Creator"),
    "pick_gamer": ("🎮 ╎ Gamer", "🎮 Gamer"),
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

    @discord.ui.button(label="Developer", emoji="💻", style=discord.ButtonStyle.primary, custom_id="pick_dev")
    async def dev_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_dev"])

    @discord.ui.button(label="QA Tester", emoji="🧪", style=discord.ButtonStyle.success, custom_id="pick_qa")
    async def qa_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_qa"])

    @discord.ui.button(label="Creator", emoji="🎨", style=discord.ButtonStyle.secondary, custom_id="pick_creator")
    async def creator_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_creator"])

    @discord.ui.button(label="Gamer", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id="pick_gamer")
    async def gamer_btn(self, interaction, _b):
        await self._toggle(interaction, *ROLE_PICKER_MAP["pick_gamer"])


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
    if vc and vc.channel and all(m.bot for m in vc.channel.members):
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
MUSIC_QUEUE = []  # list of {"title": str, "url": str, "webpage": str, "requester": str}
NOW_PLAYING = {"track": None}
FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"


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


def _make_audio_source(url, headers=None):
    is_hls = ".m3u8" in url
    before = [] if is_hls else [FFMPEG_BEFORE]
    if headers:
        hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() in ("user-agent", "referer"))
        if hdr:
            before.append(f'-headers "{hdr}"')
    raw = discord.FFmpegPCMAudio(url, before_options=" ".join(before) or None, options="-vn")
    return discord.PCMVolumeTransformer(raw, volume=0.6)


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
            await asyncio.sleep(0.4)
        return vc
    vc = await voice_ch.connect(timeout=30, self_deaf=True)
    await asyncio.sleep(0.5)
    return vc


async def handle_music_command(msg, text):
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

    # !lofi with no arg or known station key -> 24/7 radio
    station_keys = {k: i for i, (k, _t, _u) in enumerate(LOFI_STATIONS)}
    if cmd == "!lofi" and (not arg or arg.lower() in station_keys):
        vc = await _ensure_voice(msg)
        if not vc:
            return
        idx = station_keys.get(arg.lower(), LOFI_STATE["idx"])
        title = _play_station(vc, idx)
        await msg.reply(embed=_lofi_embed(title, vc.channel.name), view=LofiView(), mention_author=False)
        return

    # Search & play by song title (!play <judul>, !p <judul>, or !lofi <judul>)
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
    client.add_view(LofiView())
    if not getattr(client, "_bg_started", False):
        client._bg_started = True
        client.loop.create_task(background_qa_and_autorole())
    log(f"online sebagai {client.user} — melayani owner {OWNER_ID} di #commands")


@client.event
async def on_member_join(member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    ch = member.guild.get_channel(WELCOME_CHANNEL)
    if ch:
        e = discord.Embed(
            title="👋 ╎ Member Baru Bergabung!",
            description=f"Selamat datang {member.mention} di **{member.guild.name}**! 🎉\n"
                        f"• Baca aturan di <#1572712072179879996>\n"
                        f"• Kenalan di <#1572712080341991554>\n"
                        f"• Mulai ngobrol di <#1572712078295437413>",
            colour=0xEB459E,
        )
        e.set_thumbnail(url=member.display_avatar.url)
        e.set_footer(text=f"Member ke-{member.guild.member_count}")
        await ch.send(embed=e)


@client.event
async def on_message(msg):
    if msg.author.bot or not msg.guild or msg.guild.id != GUILD_ID:
        return
    if msg.channel.id == IDEAS_CHANNEL and msg.content.strip():
        try:
            await msg.add_reaction("👍")
            await msg.add_reaction("👎")
        except Exception:
            pass
    text = msg.content.strip()
    cmd_word = text.split(maxsplit=1)[0].lower() if text else ""
    if cmd_word in ("!lofi", "!play", "!p", "!skip", "!next", "!queue", "!q", "!stop", "!leave"):
        try:
            await handle_music_command(msg, text)
        except Exception as exc:
            log("music error:", exc)
            await msg.reply(f"❌ Gagal memutar musik: `{str(exc)[:200]}`", mention_author=False)
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
        w = guild.get_channel(WELCOME_CHANNEL)
        e = discord.Embed(
            title="🎭 ╎ PILIH ROLE KAMU SENDIRI",
            description="Klik tombol di bawah untuk memasang atau melepas role spesial kamu secara otomatis!",
            colour=BLURPLE,
        )
        e.add_field(name="💻 Developer", value="Akses `#💻╎dev-lab` & `#🐞╎bug-report`", inline=True)
        e.add_field(name="🧪 QA Tester", value="Akses `#🐞╎bug-report` & lapor bug", inline=True)
        e.add_field(name="🎨 Creator", value="Hak posting di `#🎨╎showcase`", inline=True)
        e.add_field(name="🎮 Gamer", value="Akses penuh Voice, Stream & Soundboard", inline=True)
        e.set_footer(text="DEV Server • Self-Role Picker")
        await (w or msg.channel).send(embed=e, view=RolePickerView())
        if w and w.id != msg.channel.id:
            await msg.reply(f"✅ Panel pilih role dikirim ke {w.mention}.", mention_author=False)
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
