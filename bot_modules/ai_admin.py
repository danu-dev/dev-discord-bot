from bot_modules.core import *

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

Fitur bot yang BENAR-BENAR ADA: /ban, /unban, /kick, /timeout, /purge, /members, /userinfo, /poll, /suggestion, /rank, /leaderboard, /play, /lofi, /skip, /queue, /stop, !help, !qa, !stats, !panel-role, !panel-ticket, !reset, auto-role Member saat join, auto-role Legend (Lv.5) & VIP (Lv.10), welcome embed, mod-log otomatis, temp voice "➕ ╎ Buat Room".
Aturan ketat:
1. DILARANG mengarang slash command (/...) atau fitur otomatis yang tidak ada di daftar di atas. Jika owner minta fitur baru yang belum ada, jawab jujur di "reply" bahwa fitur itu butuh update kode bot dan beri actions [].
2. Jika owner hanya ngobrol, bertanya, atau minta saran/ide, jawab dengan gaya santai, cerdas, ramah, dan natural di "reply" dengan actions [].
3. "reply" bahasa Indonesia yang enak dibaca, jelas, dan langsung membantu (tidak kaku seperti robot).
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


