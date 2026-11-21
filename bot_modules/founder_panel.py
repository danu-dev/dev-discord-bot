from bot_modules.core import *

# --- Founder-Only Channel & Category Management Panel ---------------------
FOUNDER_CATEGORY_NAME = "👑 FOUNDER"
FOUNDER_CHANNEL_NAME = "👑╎founder-panel"
FOUNDER_PANEL_TITLE = "👑 ╎ Panel Manajemen Channel & Kategori"


def _is_founder(member):
    return member.id == OWNER_ID or any(r.id == FOUNDER_ROLE_ID for r in getattr(member, "roles", []))


def _founder_overwrites(guild):
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    founder_role = guild.get_role(FOUNDER_ROLE_ID)
    if founder_role:
        ow[founder_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
    return ow


class FounderCreateCategoryModal(discord.ui.Modal, title="Buat Kategori Baru"):
    nama = discord.ui.TextInput(label="Nama Kategori", placeholder="Contoh: 🎮 GAME", max_length=100, required=True)
    privat = discord.ui.TextInput(
        label="Privat? (ya / tidak)",
        default="tidak",
        max_length=10,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        is_priv = (self.privat.value or "").strip().lower() in ("ya", "y", "yes", "true", "1")
        ow = _founder_overwrites(interaction.guild) if is_priv else discord.utils.MISSING
        cat = await interaction.guild.create_category(
            self.nama.value.strip()[:100], overwrites=ow, reason=f"Founder Panel oleh {interaction.user}"
        )
        await interaction.response.send_message(
            f"✅ Kategori **{cat.name}** berhasil dibuat" + (" (privat)." if is_priv else "."), ephemeral=True
        )


class FounderCreateChannelModal(discord.ui.Modal, title="Buat Channel Baru"):
    nama = discord.ui.TextInput(label="Nama Channel", placeholder="Contoh: 💬╎diskusi", max_length=100, required=True)
    tipe = discord.ui.TextInput(label="Tipe (teks / voice)", default="teks", max_length=10, required=True)
    kategori = discord.ui.TextInput(label="Nama Kategori (opsional)", required=False, max_length=100)
    kunci = discord.ui.TextInput(label="Kunci / Read-Only? (ya / tidak)", default="tidak", max_length=10, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        guild = interaction.guild
        cat = find_category(guild, self.kategori.value) if self.kategori.value.strip() else None
        is_locked = (self.kunci.value or "").strip().lower() in ("ya", "y", "yes", "true", "1")
        is_voice = (self.tipe.value or "").strip().lower() in ("voice", "vc", "v")
        if is_voice:
            ow = {guild.default_role: discord.PermissionOverwrite(connect=False)} if is_locked else discord.utils.MISSING
            ch = await guild.create_voice_channel(
                self.nama.value.strip()[:100], category=cat, overwrites=ow, reason=f"Founder Panel oleh {interaction.user}"
            )
        else:
            ow = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            } if is_locked else discord.utils.MISSING
            ch = await guild.create_text_channel(
                self.nama.value.strip()[:100], category=cat, overwrites=ow, reason=f"Founder Panel oleh {interaction.user}"
            )
        cat_txt = f" di **{cat.name}**" if cat else ""
        await interaction.response.send_message(f"✅ Channel {ch.mention} berhasil dibuat{cat_txt}.", ephemeral=True)


class FounderRenameModal(discord.ui.Modal, title="Ganti Nama Channel / Kategori"):
    nama_baru = discord.ui.TextInput(label="Nama Baru", max_length=100, required=True)

    def __init__(self, target_channel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        old_name = self.target_channel.name
        new_name = self.nama_baru.value.strip()[:100]
        await self.target_channel.edit(name=new_name, reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.send_message(f"✅ **{old_name}** berhasil diganti menjadi **{new_name}**.", ephemeral=True)


class FounderMoveCategorySelect(discord.ui.ChannelSelect):
    def __init__(self, target_channel):
        self.target_channel = target_channel
        super().__init__(
            placeholder="📦 Pilih kategori tujuan...",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        cat = interaction.guild.get_channel(self.values[0].id)
        if not isinstance(cat, discord.CategoryChannel):
            await interaction.response.send_message("❌ Kategori tidak valid.", ephemeral=True)
            return
        await self.target_channel.edit(category=cat, reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.send_message(
            f"✅ **{self.target_channel.name}** dipindahkan ke kategori **{cat.name}**.", ephemeral=True
        )


class FounderPermActionView(discord.ui.View):
    def __init__(self, target_channel):
        super().__init__(timeout=180)
        self.target_channel = target_channel
        if not isinstance(target_channel, discord.CategoryChannel):
            self.add_item(FounderMoveCategorySelect(target_channel))

    @discord.ui.button(label="Lock (Read-Only)", emoji="🔒", style=discord.ButtonStyle.secondary)
    async def lock_btn(self, interaction: discord.Interaction, _b):
        await self.target_channel.set_permissions(
            interaction.guild.default_role, send_messages=False, reason=f"Founder Panel oleh {interaction.user}"
        )
        await interaction.response.send_message(f"🔒 **{self.target_channel.name}** dikunci (read-only).", ephemeral=True)

    @discord.ui.button(label="Unlock", emoji="🔓", style=discord.ButtonStyle.success)
    async def unlock_btn(self, interaction: discord.Interaction, _b):
        await self.target_channel.set_permissions(
            interaction.guild.default_role, send_messages=None, reason=f"Founder Panel oleh {interaction.user}"
        )
        await interaction.response.send_message(f"🔓 **{self.target_channel.name}** dibuka kembali.", ephemeral=True)

    @discord.ui.button(label="Private (Founder)", emoji="🙈", style=discord.ButtonStyle.primary)
    async def private_btn(self, interaction: discord.Interaction, _b):
        await self.target_channel.set_permissions(
            interaction.guild.default_role, view_channel=False, reason=f"Founder Panel oleh {interaction.user}"
        )
        founder_role = interaction.guild.get_role(FOUNDER_ROLE_ID)
        if founder_role:
            await self.target_channel.set_permissions(
                founder_role, view_channel=True, send_messages=True, reason=f"Founder Panel oleh {interaction.user}"
            )
        await interaction.response.send_message(f"🙈 **{self.target_channel.name}** diubah menjadi privat (khusus Founder).", ephemeral=True)

    @discord.ui.button(label="Public", emoji="👁️", style=discord.ButtonStyle.secondary)
    async def public_btn(self, interaction: discord.Interaction, _b):
        await self.target_channel.set_permissions(
            interaction.guild.default_role, view_channel=None, reason=f"Founder Panel oleh {interaction.user}"
        )
        await interaction.response.send_message(f"👁️ **{self.target_channel.name}** dibuka untuk publik.", ephemeral=True)


class FounderDeleteConfirmView(discord.ui.View):
    def __init__(self, target_channel):
        super().__init__(timeout=60)
        self.target_channel = target_channel

    @discord.ui.button(label="Ya, Hapus Permanen", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm_btn(self, interaction: discord.Interaction, _b):
        name = self.target_channel.name
        await self.target_channel.delete(reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.edit_message(content=f"🗑️ **{name}** berhasil dihapus.", view=None)

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel_btn(self, interaction: discord.Interaction, _b):
        await interaction.response.edit_message(content="❎ Penghapusan dibatalkan.", view=None)


class FounderTargetSelect(discord.ui.ChannelSelect):
    def __init__(self, action):
        self.action = action
        labels = {"rename": "ganti nama", "perm": "atur permission", "delete": "hapus"}
        super().__init__(
            placeholder=f"Pilih channel atau kategori yang mau di-{labels[action]}...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.voice, discord.ChannelType.category],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.guild.get_channel(self.values[0].id)
        if not ch:
            await interaction.response.send_message("❌ Channel/kategori tidak ditemukan.", ephemeral=True)
            return
        if self.action == "rename":
            await interaction.response.send_modal(FounderRenameModal(ch))
        elif self.action == "perm":
            await interaction.response.send_message(
                f"⚙️ Atur permission untuk **{ch.name}**:", view=FounderPermActionView(ch), ephemeral=True
            )
        elif self.action == "delete":
            await interaction.response.send_message(
                f"⚠️ Yakin ingin menghapus **{ch.name}** secara permanen?",
                view=FounderDeleteConfirmView(ch),
                ephemeral=True,
            )


class FounderManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buat Kategori", emoji="📁", style=discord.ButtonStyle.primary, custom_id="fnd_mk_cat")
    async def mk_cat(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        await interaction.response.send_modal(FounderCreateCategoryModal())

    @discord.ui.button(label="Buat Channel", emoji="💬", style=discord.ButtonStyle.success, custom_id="fnd_mk_ch")
    async def mk_ch(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        await interaction.response.send_modal(FounderCreateChannelModal())

    @discord.ui.button(label="Ganti Nama", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="fnd_rename")
    async def rename_ch(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        v = discord.ui.View(timeout=120)
        v.add_item(FounderTargetSelect("rename"))
        await interaction.response.send_message("Pilih channel/kategori yang mau diganti namanya:", view=v, ephemeral=True)

    @discord.ui.button(label="Ubah Permission", emoji="🔒", style=discord.ButtonStyle.secondary, custom_id="fnd_perm")
    async def perm_ch(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        v = discord.ui.View(timeout=120)
        v.add_item(FounderTargetSelect("perm"))
        await interaction.response.send_message("Pilih channel/kategori yang mau diatur permission-nya:", view=v, ephemeral=True)

    @discord.ui.button(label="Hapus", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="fnd_delete", row=0)
    async def del_ch(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        v = discord.ui.View(timeout=120)
        v.add_item(FounderTargetSelect("delete"))
        await interaction.response.send_message("Pilih channel/kategori yang mau dihapus:", view=v, ephemeral=True)

    @discord.ui.button(label="Kelola Role", emoji="🏷️", style=discord.ButtonStyle.primary, custom_id="fnd_role", row=1)
    async def manage_role_btn(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        await interaction.response.send_message("Pilih atau buat role baru:", view=FounderRoleHomeView(), ephemeral=True)

    @discord.ui.button(label="Kirim Pesan", emoji="📢", style=discord.ButtonStyle.success, custom_id="fnd_announce", row=1)
    async def announce_btn(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        v = discord.ui.View(timeout=120)
        v.add_item(FounderAnnounceChannelSelect())
        await interaction.response.send_message("Pilih channel tujuan pengiriman pesan:", view=v, ephemeral=True)

    @discord.ui.button(label="Slowmode & Topik", emoji="🐢", style=discord.ButtonStyle.secondary, custom_id="fnd_ch_cfg", row=1)
    async def ch_cfg_btn(self, interaction: discord.Interaction, _b):
        if not _is_founder(interaction.user):
            await interaction.response.send_message("❌ Khusus Founder.", ephemeral=True)
            return
        v = discord.ui.View(timeout=120)
        v.add_item(FounderConfigChannelSelect())
        await interaction.response.send_message("Pilih channel teks yang mau diatur slowmode/topiknya:", view=v, ephemeral=True)


class FounderCreateRoleModal(discord.ui.Modal, title="Buat Role Baru"):
    nama = discord.ui.TextInput(label="Nama Role", placeholder="Contoh: ⭐ VIP", max_length=100, required=True)
    warna = discord.ui.TextInput(label="Warna Hex (opsional)", default="#5865F2", max_length=10, required=False)
    hoist = discord.ui.TextInput(label="Tampilkan Terpisah di Member List? (ya/tidak)", default="ya", max_length=10, required=False)

    async def on_submit(self, interaction: discord.Interaction):
        is_hoist = (self.hoist.value or "").strip().lower() in ("ya", "y", "yes", "true", "1")
        r = await interaction.guild.create_role(
            name=self.nama.value.strip()[:100],
            colour=discord.Colour(color(self.warna.value)),
            hoist=is_hoist,
            mentionable=True,
            reason=f"Founder Panel oleh {interaction.user}",
        )
        await interaction.response.send_message(f"✅ Role {r.mention} berhasil dibuat.", ephemeral=True)


class FounderEditRoleModal(discord.ui.Modal, title="Edit Role"):
    nama = discord.ui.TextInput(label="Nama Baru (kosongkan jika tetap)", required=False, max_length=100)
    warna = discord.ui.TextInput(label="Warna Hex Baru (misal #FF5555, kosongkan jika tetap)", required=False, max_length=10)

    def __init__(self, role):
        super().__init__()
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        kw = {}
        if self.nama.value.strip():
            kw["name"] = self.nama.value.strip()[:100]
        if self.warna.value.strip():
            kw["colour"] = discord.Colour(color(self.warna.value))
        if not kw:
            await interaction.response.send_message("ℹ️ Tidak ada perubahan diisi.", ephemeral=True)
            return
        await self.role.edit(**kw, reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.send_message(f"✅ Role {self.role.mention} diperbarui.", ephemeral=True)


class FounderRoleMemberSelect(discord.ui.UserSelect):
    def __init__(self, role, give=True):
        self.role = role
        self.give = give
        super().__init__(placeholder="Pilih member...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.values[0].id)
        if not member:
            await interaction.response.send_message("❌ Member tidak ditemukan.", ephemeral=True)
            return
        if self.give:
            await member.add_roles(self.role, reason=f"Founder Panel oleh {interaction.user}")
            await interaction.response.send_message(f"✅ Role {self.role.mention} diberikan ke {member.mention}.", ephemeral=True)
        else:
            await member.remove_roles(self.role, reason=f"Founder Panel oleh {interaction.user}")
            await interaction.response.send_message(f"✅ Role {self.role.mention} dicabut dari {member.mention}.", ephemeral=True)


class FounderRoleActionView(discord.ui.View):
    def __init__(self, role):
        super().__init__(timeout=180)
        self.role = role

    @discord.ui.button(label="Edit Nama/Warna", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit_btn(self, interaction: discord.Interaction, _b):
        await interaction.response.send_modal(FounderEditRoleModal(self.role))

    @discord.ui.button(label="Pasang ke Member", emoji="➕", style=discord.ButtonStyle.success)
    async def give_btn(self, interaction: discord.Interaction, _b):
        v = discord.ui.View(timeout=120)
        v.add_item(FounderRoleMemberSelect(self.role, give=True))
        await interaction.response.send_message(f"Pilih member untuk diberi role {self.role.mention}:", view=v, ephemeral=True)

    @discord.ui.button(label="Cabut dari Member", emoji="➖", style=discord.ButtonStyle.secondary)
    async def remove_btn(self, interaction: discord.Interaction, _b):
        v = discord.ui.View(timeout=120)
        v.add_item(FounderRoleMemberSelect(self.role, give=False))
        await interaction.response.send_message(f"Pilih member untuk dicabut role {self.role.mention}:", view=v, ephemeral=True)

    @discord.ui.button(label="Hapus Role", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def del_btn(self, interaction: discord.Interaction, _b):
        name = self.role.name
        await self.role.delete(reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.edit_message(content=f"🗑️ Role **{name}** berhasil dihapus.", view=None)


class FounderRoleSelect(discord.ui.RoleSelect):
    def __init__(self):
        super().__init__(placeholder="Pilih role yang mau dikelola...", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.values[0].id)
        if not role or role.is_default():
            await interaction.response.send_message("❌ Role tidak bisa dikelola.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"⚙️ Kelola role {role.mention}:", view=FounderRoleActionView(role), ephemeral=True
        )


class FounderRoleHomeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(FounderRoleSelect())

    @discord.ui.button(label="Buat Role Baru", emoji="➕", style=discord.ButtonStyle.success)
    async def create_role_btn(self, interaction: discord.Interaction, _b):
        await interaction.response.send_modal(FounderCreateRoleModal())


class FounderAnnounceModal(discord.ui.Modal, title="Kirim Pesan / Pengumuman"):
    judul = discord.ui.TextInput(label="Judul Embed (kosongkan = teks biasa)", required=False, max_length=200)
    isi = discord.ui.TextInput(label="Isi Pesan", style=discord.TextStyle.paragraph, required=True, max_length=2000)
    tag_role = discord.ui.TextInput(label="Tag Role (opsional, misal: Member / everyone)", required=False, max_length=60)

    def __init__(self, target_channel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        mention_txt = ""
        raw_tag = self.tag_role.value.strip()
        if raw_tag:
            if raw_tag.lower() in ("everyone", "@everyone"):
                mention_txt = "@everyone"
            else:
                r = find_role(interaction.guild, raw_tag.lstrip("@"))
                if r:
                    mention_txt = r.mention
        allowed = discord.AllowedMentions(everyone=True, roles=True)
        if self.judul.value.strip():
            e = discord.Embed(title=self.judul.value.strip(), description=self.isi.value, colour=BLURPLE)
            e.set_footer(text=f"{interaction.guild.name}")
            await self.target_channel.send(content=mention_txt or None, embed=e, allowed_mentions=allowed)
        else:
            text = f"{mention_txt}\n{self.isi.value}" if mention_txt else self.isi.value
            await self.target_channel.send(text[:2000], allowed_mentions=allowed)
        await interaction.response.send_message(f"✅ Pesan terkirim ke {self.target_channel.mention}.", ephemeral=True)


class FounderAnnounceChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Pilih channel tujuan...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.guild.get_channel(self.values[0].id)
        if not ch:
            await interaction.response.send_message("❌ Channel tidak ditemukan.", ephemeral=True)
            return
        await interaction.response.send_modal(FounderAnnounceModal(ch))


class FounderChannelConfigModal(discord.ui.Modal, title="Atur Slowmode & Topik Channel"):
    slowmode = discord.ui.TextInput(label="Slowmode dalam detik (0 = mati, maks 21600)", required=False, max_length=6)
    topik = discord.ui.TextInput(label="Topik / Deskripsi Channel (opsional)", style=discord.TextStyle.paragraph, required=False, max_length=500)

    def __init__(self, target_channel):
        super().__init__()
        self.target_channel = target_channel

    async def on_submit(self, interaction: discord.Interaction):
        kw = {}
        if self.slowmode.value.strip().isdigit():
            kw["slowmode_delay"] = max(0, min(21600, int(self.slowmode.value.strip())))
        if self.topik.value.strip():
            kw["topic"] = self.topik.value.strip()
        if not kw:
            await interaction.response.send_message("ℹ️ Tidak ada perubahan diisi.", ephemeral=True)
            return
        await self.target_channel.edit(**kw, reason=f"Founder Panel oleh {interaction.user}")
        await interaction.response.send_message(f"✅ Pengaturan {self.target_channel.mention} diperbarui.", ephemeral=True)


class FounderConfigChannelSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            placeholder="Pilih channel teks...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        ch = interaction.guild.get_channel(self.values[0].id)
        if not ch:
            await interaction.response.send_message("❌ Channel tidak ditemukan.", ephemeral=True)
            return
        await interaction.response.send_modal(FounderChannelConfigModal(ch))


async def _ensure_founder_panel():
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    try:
        cat = find_category(guild, FOUNDER_CATEGORY_NAME)
        if not cat:
            cat = await guild.create_category(
                FOUNDER_CATEGORY_NAME,
                overwrites=_founder_overwrites(guild),
                reason="Setup kategori khusus Founder",
            )
        ch = discord.utils.find(lambda c: c.name == FOUNDER_CHANNEL_NAME, cat.text_channels)
        if not ch:
            ch = await guild.create_text_channel(
                FOUNDER_CHANNEL_NAME,
                category=cat,
                overwrites=_founder_overwrites(guild),
                topic="Panel manajemen channel & kategori khusus Founder",
                reason="Setup channel khusus Founder",
            )
        panel_embed = discord.Embed(
            title=FOUNDER_PANEL_TITLE,
            description=(
                "Gunakan tombol di bawah untuk mengelola server secara langsung:\n"
                "• **📁 Buat Kategori** — Tambah kategori baru (publik/privat)\n"
                "• **💬 Buat Channel** — Tambah channel teks/voice baru\n"
                "• **✏️ Ganti Nama** — Ubah nama channel atau kategori\n"
                "• **🔒 Ubah Permission** — Lock, Unlock, Private, Public, atau Pindah Kategori\n"
                "• **🗑️ Hapus** — Hapus channel atau kategori (dengan konfirmasi)\n"
                "• **🏷️ Kelola Role** — Buat, edit, hapus, atau pasang/cabut role member\n"
                "• **📢 Kirim Pesan** — Kirim pengumuman/embed/teks custom ke channel\n"
                "• **🐢 Slowmode & Topik** — Atur jeda chat & deskripsi channel"
            ),
            colour=GOLD,
        )
        panel_embed.set_footer(text="Akses terbatas: khusus Owner & Role Founder")
        panel_msg = None
        async for m in ch.history(limit=10):
            if m.author == client.user and m.embeds and m.embeds[0].title == FOUNDER_PANEL_TITLE:
                panel_msg = m
                break
        if panel_msg:
            await panel_msg.edit(embed=panel_embed, view=FounderManageView())
        else:
            await ch.send(embed=panel_embed, view=FounderManageView())
    except Exception as exc:
        log("ensure founder panel error:", exc)


