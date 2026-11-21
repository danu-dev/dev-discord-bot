from bot_modules.core import *
from bot_modules.economy_xp import _rng, _grant_bonus_xp

def _help_embed(member):
    e = discord.Embed(
        title="📖 ╎ Bantuan & Daftar Command",
        description="Berikut command yang bisa kamu pakai di server ini:",
        colour=BLURPLE,
    )
    e.add_field(
        name="🏅 Rank & Level ML",
        value="`/rank` • `/ranks` • `/leaderboard` • `/daily`",
        inline=False,
    )
    e.add_field(
        name="🎮 Mini-Games (Bonus XP)",
        value="`/suit` • `/coinflip` • `/tebakangka`",
        inline=False,
    )
    e.add_field(
        name="🛠️ Komunitas & Info",
        value="`/help` • `/koin` • `/shop` • `/invites` • `/snipe` • `/confess` • `/members` • `/userinfo` • `/poll` • `/suggestion`",
        inline=False,
    )
    e.add_field(
        name="🎵 Musik & Lofi",
        value="`/play <judul>` • `/lofi [stasiun]` • `/skip` • `/queue` • `/stop`",
        inline=False,
    )
    perms = member.guild_permissions
    if member.id == OWNER_ID or perms.administrator or perms.ban_members or perms.kick_members or perms.manage_messages:
        e.add_field(
            name="🛡️ Staff & Owner",
            value="`/ban` • `/unban` • `/kick` • `/timeout` • `/purge` • `/setlevel` • `/giveaway`\n"
                  "`!stats` • `!qa` • `!panel-role` • `!panel-ticket` • `!reset` • Mention bot untuk AI",
            inline=False,
        )
    e.set_footer(text=f"{member.guild.name} • Ketik / untuk melihat semua slash command")
    return e


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


async def _do_timeout(guild, actor, member, minutes, reason):
    if member.id in (OWNER_ID, guild.me.id):
        return "❌ Tidak bisa timeout owner atau bot sendiri."
    minutes = max(1, min(40320, int(minutes)))  # Discord max 28 days
    try:
        await member.timeout(datetime.timedelta(minutes=minutes), reason=f"{actor} | {reason}"[:512])
    except discord.Forbidden:
        return "❌ Bot tidak punya izin timeout atau role target lebih tinggi dari role bot."
    except discord.HTTPException as exc:
        return f"❌ Gagal timeout: `{exc}`"
    await _send_modlog(guild, "⏳ Member Di-timeout", f"Target: {member.mention} (`{member.id}`)\nDurasi: `{minutes} menit`\nOleh: {actor.mention}\nAlasan: {reason}", GOLD)
    return f"⏳ {member.mention} di-timeout selama **{minutes} menit**. Alasan: **{reason}**"


def _userinfo_embed(member):
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    e = discord.Embed(title=f"🪪 ╎ Info {member.display_name}", colour=member.colour if member.colour.value else BLURPLE)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Username", value=f"`{member.name}`", inline=True)
    e.add_field(name="User ID", value=f"`{member.id}`", inline=True)
    e.add_field(name="Akun Dibuat", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    if member.joined_at:
        e.add_field(name="Join Server", value=f"<t:{int(member.joined_at.timestamp())}:R>", inline=True)
    e.add_field(name=f"Role ({len(roles)})", value=" ".join(roles)[:1024] if roles else "-", inline=False)
    return e


@tree.command(name="timeout", description="Mute sementara (timeout) member", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(member="Member yang mau di-timeout", menit="Durasi dalam menit (1-40320)", alasan="Alasan timeout")
async def slash_timeout(interaction: discord.Interaction, member: discord.Member, menit: int = 10, alasan: str = "Tidak ada alasan"):
    if not _has_mod_perm(interaction.user, "moderate_members"):
        await interaction.response.send_message("❌ Kamu tidak punya izin timeout.", ephemeral=True)
        return
    await interaction.response.send_message(await _do_timeout(interaction.guild, interaction.user, member, menit, alasan))


@tree.command(name="purge", description="Hapus banyak pesan sekaligus di channel ini", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(jumlah="Jumlah pesan yang mau dihapus (1-100)")
async def slash_purge(interaction: discord.Interaction, jumlah: int):
    if not _has_mod_perm(interaction.user, "manage_messages"):
        await interaction.response.send_message("❌ Kamu tidak punya izin hapus pesan.", ephemeral=True)
        return
    count = max(1, min(100, jumlah))
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=count)
    await _send_modlog(interaction.guild, "🧹 Purge Pesan", f"Channel: {interaction.channel.mention}\nJumlah: `{len(deleted)}`\nOleh: {interaction.user.mention}", BLURPLE)
    await interaction.followup.send(f"🧹 Berhasil menghapus **{len(deleted)}** pesan.", ephemeral=True)


@tree.command(name="userinfo", description="Lihat info profil & role member", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(member="Member yang mau dicek (kosongkan untuk diri sendiri)")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.send_message(embed=_userinfo_embed(member or interaction.user))


POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]


@tree.command(name="poll", description="Buat voting / polling cepat", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(pertanyaan="Pertanyaan voting", opsi1="Pilihan 1", opsi2="Pilihan 2", opsi3="Pilihan 3 (opsional)", opsi4="Pilihan 4 (opsional)")
async def slash_poll(interaction: discord.Interaction, pertanyaan: str, opsi1: str, opsi2: str, opsi3: str = None, opsi4: str = None):
    opts = [o for o in (opsi1, opsi2, opsi3, opsi4) if o]
    desc = "\n".join(f"{POLL_EMOJIS[i]} {o}" for i, o in enumerate(opts))
    e = discord.Embed(title=f"📊 ╎ {pertanyaan[:200]}", description=desc, colour=BLURPLE)
    e.set_footer(text=f"Polling oleh {interaction.user.display_name}")
    await interaction.response.send_message(embed=e)
    msg = await interaction.original_response()
    for i in range(len(opts)):
        await msg.add_reaction(POLL_EMOJIS[i])




TEMP_VOICE_HUB_ID = 1573525468920160337
PERMANENT_VOICE_IDS = {
    1573525457016721492,  # 🎧 ╎ Lounge
    1573525468920160337,  # ➕ ╎ Buat Room
    1573529663622484068,  # 🎮 ╎ Mabar
    1573529666231345192,  # 💤 ╎ AFK
}


def _is_temp_voice(ch):
    return ch.id in TEMP_VOICE_IDS or (ch.name.startswith("🔊 ╎ ") and ch.id not in PERMANENT_VOICE_IDS)


TEMP_VOICE_OWNERS = {}


def _is_temp_room_owner(interaction):
    ch = interaction.channel
    if not isinstance(ch, discord.VoiceChannel):
        return False
    uid = interaction.user.id
    return (
        uid == OWNER_ID
        or TEMP_VOICE_OWNERS.get(ch.id) == uid
        or ch.permissions_for(interaction.user).manage_channels
    )


class TempVoiceLimitModal(discord.ui.Modal, title="Atur Batas Member Room"):
    limit = discord.ui.TextInput(
        label="Maksimal orang (0 = tanpa batas, 1-99)",
        placeholder="Contoh: 5",
        max_length=2,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.VoiceChannel) or not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        if not self.limit.value.strip().isdigit():
            await interaction.response.send_message("❌ Masukkan angka 0 sampai 99.", ephemeral=True)
            return
        val = max(0, min(99, int(self.limit.value.strip())))
        await ch.edit(user_limit=val, reason=f"Temp Voice Limit oleh {interaction.user}")
        msg = "tanpa batas" if val == 0 else f"maksimal **{val} orang**"
        await interaction.response.send_message(f"👥 Batas room diatur menjadi {msg}.", ephemeral=True)


class TempVoiceRenameModal(discord.ui.Modal, title="Ganti Nama Room"):
    nama = discord.ui.TextInput(label="Nama Room Baru", placeholder="Contoh: Mabar ML", max_length=90, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.VoiceChannel) or not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        raw = self.nama.value.strip()
        new_name = raw if raw.startswith("🔊 ╎ ") else f"🔊 ╎ {raw}"
        await ch.edit(name=new_name[:100], reason=f"Temp Voice Rename oleh {interaction.user}")
        await interaction.response.send_message(f"✏️ Nama room diubah menjadi **{new_name}**.", ephemeral=True)


class TempVoiceKickSelect(discord.ui.Select):
    def __init__(self, members):
        opts = [
            discord.SelectOption(label=m.display_name[:100], value=str(m.id), description=f"@{m.name}")
            for m in members[:25]
        ]
        super().__init__(placeholder="Pilih member yang ada di room...", min_values=1, max_values=1, options=opts)

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.channel
        if not isinstance(ch, discord.VoiceChannel) or not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        target = interaction.guild.get_member(int(self.values[0]))
        if not target or not target.voice or target.voice.channel != ch:
            await interaction.response.send_message("❌ Member tersebut sudah keluar dari room.", ephemeral=True)
            return
        if target.bot or target.id == interaction.user.id:
            await interaction.response.send_message("❌ Tidak bisa kick bot atau diri sendiri.", ephemeral=True)
            return
        await target.move_to(None, reason=f"Di-kick dari temp voice oleh {interaction.user}")
        await interaction.response.edit_message(content=f"👢 {target.mention} dikeluarkan dari room.", view=None)


class TempVoiceView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Lock / Unlock", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="tv_lock")
    async def lock_btn(self, interaction: discord.Interaction, _b):
        ch = interaction.channel
        if not isinstance(ch, discord.VoiceChannel) or not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        ow = ch.overwrites_for(interaction.guild.default_role)
        currently_locked = ow.connect is False
        if currently_locked:
            await ch.set_permissions(interaction.guild.default_role, connect=None, reason="Unlock temp voice")
            await interaction.response.send_message("🔓 Room dibuka kembali untuk semua member.", ephemeral=True)
        else:
            await ch.set_permissions(interaction.guild.default_role, connect=False, reason="Lock temp voice")
            await interaction.response.send_message("🔒 Room dikunci! Member lain tidak bisa masuk.", ephemeral=True)

    @discord.ui.button(label="Batas Orang", emoji="👥", style=discord.ButtonStyle.primary, custom_id="tv_limit")
    async def limit_btn(self, interaction: discord.Interaction, _b):
        if not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        await interaction.response.send_modal(TempVoiceLimitModal())

    @discord.ui.button(label="Ganti Nama", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="tv_rename")
    async def rename_btn(self, interaction: discord.Interaction, _b):
        if not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        await interaction.response.send_modal(TempVoiceRenameModal())

    @discord.ui.button(label="Kick Member", emoji="👢", style=discord.ButtonStyle.danger, custom_id="tv_kick")
    async def kick_btn(self, interaction: discord.Interaction, _b):
        ch = interaction.channel
        if not isinstance(ch, discord.VoiceChannel) or not _is_temp_room_owner(interaction):
            await interaction.response.send_message("❌ Khusus pemilik room.", ephemeral=True)
            return
        kickable = [m for m in ch.members if not m.bot and m.id != interaction.user.id]
        if not kickable:
            await interaction.response.send_message("ℹ️ Tidak ada member lain di room ini yang bisa di-kick.", ephemeral=True)
            return
        v = discord.ui.View(timeout=60)
        v.add_item(TempVoiceKickSelect(kickable))
        await interaction.response.send_message("Pilih member di room ini yang mau dikeluarkan:", view=v, ephemeral=True)



# --- Giveaway (/giveaway) -------------------------------------------------
GIVEAWAYS = {}


def _giveaway_embed(gw):
    ended = gw.get("ended", False)
    title = "🎉 ╎ GIVEAWAY SELESAI" if ended else "🎉 ╎ GIVEAWAY"
    status_line = (
        f"🏆 **Pemenang:** {gw.get('winners_text', '-')}"
        if ended
        else f"⏰ **Berakhir:** <t:{gw['end_ts']}:R>\n👥 **Peserta:** `{len(gw['participants'])}` orang"
    )
    e = discord.Embed(
        title=title,
        description=f"🎁 **Hadiah:** {gw['prize']}\n👑 **Pemenang:** `{gw['winners_count']}` orang\n{status_line}",
        colour=GREEN if not ended else GOLD,
    )
    e.set_footer(text=f"Diselenggarakan oleh {gw['host_name']}")
    return e


async def _end_giveaway(guild, channel, msg_id):
    gw = GIVEAWAYS.get(msg_id)
    if not gw or gw.get("ended"):
        return
    gw["ended"] = True
    pool = list(gw["participants"])
    count = min(gw["winners_count"], len(pool))
    winners = _rng.sample(pool, count) if count > 0 else []
    gw["winners_text"] = ", ".join(f"<@{uid}>" for uid in winners) if winners else "_Tidak ada peserta_"
    try:
        msg = await channel.fetch_message(msg_id)
        await msg.edit(embed=_giveaway_embed(gw), view=None)
    except Exception:
        pass
    if winners:
        await channel.send(f"🎊 Selamat {gw['winners_text']}! Kamu memenangkan **{gw['prize']}**!")
    else:
        await channel.send(f"😢 Giveaway **{gw['prize']}** berakhir tanpa peserta.")


async def _finish_giveaway_after(guild, channel, msg_id, delay):
    await asyncio.sleep(max(1, delay))
    await _end_giveaway(guild, channel, msg_id)


class GiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ikut Giveaway", emoji="🎉", style=discord.ButtonStyle.success, custom_id="gw_join")
    async def join_btn(self, interaction: discord.Interaction, _b):
        gw = GIVEAWAYS.get(interaction.message.id)
        if not gw or gw.get("ended"):
            await interaction.response.send_message("❌ Giveaway ini sudah berakhir atau data restart.", ephemeral=True)
            return
        uid = interaction.user.id
        if uid in gw["participants"]:
            gw["participants"].remove(uid)
            msg_txt = "ℹ️ Kamu keluar dari giveaway ini."
        else:
            gw["participants"].add(uid)
            msg_txt = f"✅ Kamu berhasil ikut giveaway **{gw['prize']}**! Total peserta: `{len(gw['participants'])}`."
        try:
            await interaction.message.edit(embed=_giveaway_embed(gw), view=self)
        except Exception:
            pass
        await interaction.response.send_message(msg_txt, ephemeral=True)

    @discord.ui.button(label="Akhiri Sekarang", emoji="🏁", style=discord.ButtonStyle.secondary, custom_id="gw_end")
    async def end_btn(self, interaction: discord.Interaction, _b):
        gw = GIVEAWAYS.get(interaction.message.id)
        if not gw or gw.get("ended"):
            await interaction.response.send_message("❌ Giveaway sudah berakhir.", ephemeral=True)
            return
        if interaction.user.id != gw["host_id"] and not _has_mod_perm(interaction.user, "manage_guild"):
            await interaction.response.send_message("❌ Hanya host/staff yang bisa mengakhiri giveaway.", ephemeral=True)
            return
        await interaction.response.send_message("🏁 Mengakhiri giveaway & mengundi pemenang...", ephemeral=True)
        await _end_giveaway(interaction.guild, interaction.channel, interaction.message.id)


@tree.command(name="giveaway", description="Buat giveaway dengan tombol ikut & undian otomatis", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(
    hadiah="Nama hadiah giveaway",
    durasi_menit="Durasi giveaway dalam menit (1 - 10080)",
    pemenang="Jumlah pemenang (default: 1)",
    channel="Channel tujuan (opsional, default channel ini)",
)
async def slash_giveaway(
    interaction: discord.Interaction,
    hadiah: str,
    durasi_menit: int,
    pemenang: int = 1,
    channel: discord.TextChannel = None,
):
    if not _has_mod_perm(interaction.user, "manage_guild"):
        await interaction.response.send_message("❌ Kamu tidak punya izin membuat giveaway.", ephemeral=True)
        return
    dur = max(1, min(10080, durasi_menit))
    win_cnt = max(1, min(20, pemenang))
    ch = channel or interaction.channel
    end_ts = int(time.time()) + dur * 60
    gw = {
        "prize": hadiah[:200],
        "winners_count": win_cnt,
        "end_ts": end_ts,
        "host_id": interaction.user.id,
        "host_name": interaction.user.display_name,
        "participants": set(),
        "ended": False,
    }
    msg = await ch.send(embed=_giveaway_embed(gw), view=GiveawayView())
    GIVEAWAYS[msg.id] = gw
    client.loop.create_task(_finish_giveaway_after(interaction.guild, ch, msg.id, dur * 60))
    await interaction.response.send_message(f"🎉 Giveaway **{hadiah}** dibuat di {ch.mention} (berakhir <t:{end_ts}:R>).", ephemeral=True)


SNIPE_CACHE = {}


@tree.command(name="snipe", description="Lihat pesan terakhir yang baru saja dihapus di channel ini", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(sembunyikan="Tampilkan hanya untuk kamu saja (default: Tidak)")
async def slash_snipe(interaction: discord.Interaction, sembunyikan: bool = False):
    data = SNIPE_CACHE.get(interaction.channel_id)
    if not data:
        await interaction.response.send_message("ℹ️ Belum ada pesan terhapus yang tercatat di channel ini.", ephemeral=True)
        return
    e = discord.Embed(
        description=data["content"] or "_(tanpa teks)_",
        colour=RED,
    )
    e.set_author(name=data["author_name"], icon_url=data["author_avatar"])
    if data.get("attachments"):
        e.add_field(name="📎 Lampiran", value="\n".join(data["attachments"][:5]), inline=False)
    e.set_footer(text=f"Dihapus • #{interaction.channel.name}")
    e.timestamp = data["deleted_at"]
    await interaction.response.send_message(embed=e, ephemeral=sembunyikan)


