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
COMMANDS_CHANNEL = 1572669201980727439
QA_CHANNEL = 1572669204497301575
BOT_CATEGORY = 1572549198719484065
IDEAS_CHANNEL = 1572654533484609744
STATS_CHANNEL = 1572654530338889841
WELCOME_CHANNEL = 1572549312880189461
MEMBER_ROLE_ID = 1572549618057613332
DISCORD_TOKEN_FILE = "/root/.config/opencode/secrets/discord.token"
GROQ_KEY_FILE = "/root/.config/opencode/secrets/groq.key"
MEMORY_FILE = os.environ.get("MEMORY_FILE", "dev-ai-memory.json")
GROQ_MODEL = "openai/gpt-oss-120b"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

BLURPLE, GREEN, RED, GOLD = 0x5865F2, 0x57F287, 0xED4245, 0xFEE75C


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def get_discord_token():
    return os.environ.get("DISCORD_TOKEN") or open(DISCORD_TOKEN_FILE, encoding="utf-8").read().strip()


def get_groq_key():
    return os.environ.get("GROQ_KEY") or open(GROQ_KEY_FILE, encoding="utf-8").read().strip()


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


# --- QA module (reuses local discord_qa.py) -------------------------------
_qa_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "discord_qa.py")
_spec = importlib.util.spec_from_file_location("discord_qa", _qa_path)
qa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qa)


# --- Groq -----------------------------------------------------------------
SYSTEM_PROMPT = """Kamu asisten setup & manajemen server Discord "DEV". Pemilik server menulis permintaan
dalam bahasa Indonesia santai. Ubah jadi rencana aksi JSON.

Balas HANYA JSON: {"reply": "<penjelasan singkat bahasa Indonesia>", "actions": [ ... ]}

Aksi yang tersedia (pakai nama channel/kategori/role/member persis seperti di daftar server):
- {"type":"create_category","name":str}
- {"type":"create_text_channel","name":str,"category":str|null,"topic":str|null,"read_only":bool}
- {"type":"create_voice_channel","name":str,"category":str|null,"user_limit":int|null}
- {"type":"rename_channel","channel":str,"new_name":str}
- {"type":"move_channel","channel":str,"category":str}
- {"type":"delete_channel","channel":str}
- {"type":"set_topic","channel":str,"topic":str}
- {"type":"set_slowmode","channel":str,"seconds":int}
- {"type":"lock_channel","channel":str}      (member hanya bisa baca)
- {"type":"unlock_channel","channel":str}
- {"type":"hide_channel","channel":str,"visible_to_roles":[str]}  (cuma role itu yang bisa lihat)
- {"type":"create_role","name":str,"color":"#RRGGBB","hoist":bool,"mentionable":bool}
- {"type":"edit_role","role":str,"new_name":str|null,"color":"#RRGGBB"|null,"hoist":bool|null}
- {"type":"delete_role","role":str}
- {"type":"give_role","member":str,"role":str}
- {"type":"remove_role","member":str,"role":str}
- {"type":"edit_server","name":str|null,"description":str|null}
- {"type":"send_embed","channel":str,"title":str,"description":str,"color":"#RRGGBB"}
- {"type":"purge_messages","channel":str,"limit":int}

Aturan:
- Semua aksi di atas aman dijalankan karena owner harus menekan tombol ✅ terlebih dahulu.
- Kamu punya ingatan percakapan sebelumnya, gunakan konteks itu untuk memahami kata rujukan ("tadi", "yang barusan", "itu", dll).
- Kalau permintaan cuma ngobrol/pertanyaan, jawab di "reply" dan actions [].
- Nama text channel huruf kecil pakai tanda hubung. Boleh pakai emoji gaya "💬╎nama".
- Maksimal 15 aksi.
"""

CHAT_HISTORY = []
MAX_HISTORY = 12

if os.path.exists(MEMORY_FILE):
    try:
        CHAT_HISTORY = json.loads(read(MEMORY_FILE))[-MAX_HISTORY * 2:]
    except Exception:
        CHAT_HISTORY = []


def _save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as fh:
            json.dump(CHAT_HISTORY[-MAX_HISTORY * 2:], fh, ensure_ascii=False)
    except Exception:
        pass


def groq_plan(request_text, server_summary, remember=True):
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Kondisi server sekarang:\n" + server_summary},
    ]
    if remember:
        msgs.extend(CHAT_HISTORY[-MAX_HISTORY:])
    msgs.append({"role": "user", "content": request_text})
    body = {
        "model": GROQ_MODEL,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": msgs,
    }
    req = urllib.request.Request(
        GROQ_URL, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": "Bearer " + get_groq_key(),
                 "Content-Type": "application/json", "User-Agent": "dev-ai-bot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Groq HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}")
    raw_content = data["choices"][0]["message"]["content"]
    plan = json.loads(raw_content)
    plan.setdefault("reply", "")
    actions = plan.get("actions") or []
    plan["actions"] = [a for a in actions if isinstance(a, dict) and a.get("type") in ACTIONS][:15]
    if remember:
        CHAT_HISTORY.append({"role": "user", "content": request_text})
        CHAT_HISTORY.append({"role": "assistant", "content": raw_content})
        if len(CHAT_HISTORY) > MAX_HISTORY * 2:
            del CHAT_HISTORY[:-MAX_HISTORY * 2]
        _save_memory()
    return plan


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


def server_summary(guild):
    lines = [f"Server: {guild.name}", "Kategori & channel:"]
    for cat, chans in guild.by_category():
        lines.append(f"- [{cat.name if cat else 'tanpa kategori'}] " +
                     ", ".join(("#" if isinstance(c, discord.TextChannel) else "🔊") + c.name for c in chans))
    lines.append("Role: " + ", ".join(r.name for r in reversed(guild.roles)))
    members = [m.name for m in guild.members if not m.bot]
    if members:
        lines.append("Member: " + ", ".join(members[:30]))
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
        "send_embed": lambda: f"📨 Kirim embed **{g('title')}** ke #{g('channel')}",
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
        await asyncio.to_thread(groq_plan, "halo", "-", False)
        out.append(("groq-ai", True, f"{GROQ_MODEL} merespons"))
    except Exception as exc:
        out.append(("groq-ai", False, str(exc)[:200]))
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
HELP.set_footer(text=f"Otak AI: Groq • {GROQ_MODEL}")


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


async def background_qa_and_autorole():
    await client.wait_until_ready()
    last_failures = None
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
            results, _ = await asyncio.to_thread(qa.run_pass)
            fails = sorted(f"{n}: {d}" for n, ok, d in results if not ok)
            key = tuple(fails)
            if key != last_failures and (last_failures is not None or fails):
                ch = client.get_channel(QA_CHANNEL)
                if ch:
                    verdict = "🟢 QA PASS" if not fails else f"🔴 QA FAIL — {len(fails)} masalah"
                    body = verdict + ("\n" + "\n".join(f"• {f}" for f in fails[:12]) if fails else "")
                    await ch.send(body[:1900])
                last_failures = key
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
                        f"• Baca aturan di <#1572549308568571915>\n"
                        f"• Ambil role kamu di <#1572549312880189461>\n"
                        f"• Mulai ngobrol di <#1572541421779689474>",
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
    if msg.author.id != OWNER_ID or msg.channel.id != COMMANDS_CHANNEL:
        return
    text = msg.content.strip()
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
            plan = await asyncio.to_thread(groq_plan, text, server_summary(guild))
        except Exception as exc:
            log("groq error:", exc)
            await msg.reply(embed=discord.Embed(title="AI error", description=str(exc)[:1500], colour=RED),
                            mention_author=False)
            return
    e = discord.Embed(description=plan["reply"][:3000] or "​", colour=GOLD if plan["actions"] else BLURPLE)
    if not plan["actions"]:
        await msg.reply(embed=e, mention_author=False)
        return
    e.title = "📋 Rencana — tekan ✅ untuk menjalankan"
    e.add_field(name=f"{len(plan['actions'])} aksi", value="\n".join(describe(a) for a in plan["actions"])[:1024],
                inline=False)
    e.set_footer(text="Berlaku 5 menit • hanya owner yang bisa menekan tombol")
    await msg.reply(embed=e, view=PlanView(guild, plan["actions"]), mention_author=False)


if __name__ == "__main__":
    start_health_server()
    try:
        client.run(get_discord_token(), log_handler=None)
    except discord.PrivilegedIntentsRequired:
        sys.exit("MESSAGE_CONTENT intent belum aktif di Developer Portal")
