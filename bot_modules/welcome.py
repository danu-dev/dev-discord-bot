import io
from PIL import Image, ImageDraw, ImageFont

from bot_modules.core import *
from bot_modules.core import _has_mod_perm, _send_modlog

RULES_CHANNEL_ID = 1573525422585684185
VERIFY_CONFIG_FILE = os.environ.get("VERIFY_CONFIG_FILE", "dev-verify-config.json")

_FONT_BOLD = "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"
_FONT_REG = "/usr/share/fonts/TTF/DejaVuSans.ttf"


def _load_verify_config():
    if os.path.exists(VERIFY_CONFIG_FILE):
        try:
            data = json.loads(read(VERIFY_CONFIG_FILE))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"enabled": False}


VERIFY_CONFIG = _load_verify_config()


def _save_verify_config():
    try:
        with open(VERIFY_CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(VERIFY_CONFIG, fh)
    except Exception:
        pass


def is_verify_enabled():
    return bool(VERIFY_CONFIG.get("enabled", False))


def _font(size, bold=False):
    path = _FONT_BOLD if bold else _FONT_REG
    if os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _render_welcome_card(avatar_bytes, display_name, username, member_count, guild_name):
    w, h = 900, 320
    img = Image.new("RGBA", (w, h), (18, 22, 41, 255))
    draw = ImageDraw.Draw(img)

    # Vertical gradient background
    for y in range(h):
        t = y / (h - 1)
        r = int(18 + (32 - 18) * t)
        g = int(22 + (38 - 22) * t)
        b = int(41 + (74 - 41) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b, 255))

    # Decorative accent circles
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.ellipse((-60, -80, 260, 240), fill=(88, 101, 242, 45))
    od.ellipse((680, 120, 980, 420), fill=(235, 69, 158, 40))
    img = Image.alpha_composite(img, overlay)
    draw = ImageDraw.Draw(img)

    # Inner card frame
    draw.rounded_rectangle([(18, 18), (w - 18, h - 18)], radius=24, outline=(88, 101, 242, 160), width=3)

    # Circular avatar with ring border
    av_size = 180
    av_x, av_y = 58, (h - av_size) // 2
    draw.ellipse((av_x - 6, av_y - 6, av_x + av_size + 6, av_y + av_size + 6), fill=(235, 69, 158, 255))
    draw.ellipse((av_x - 3, av_y - 3, av_x + av_size + 3, av_y + av_size + 3), fill=(88, 101, 242, 255))

    if avatar_bytes:
        try:
            av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((av_size, av_size), Image.Resampling.LANCZOS)
        except Exception:
            av = Image.new("RGBA", (av_size, av_size), (88, 101, 242, 255))
    else:
        av = Image.new("RGBA", (av_size, av_size), (88, 101, 242, 255))

    mask = Image.new("L", (av_size, av_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, av_size, av_size), fill=255)
    img.paste(av, (av_x, av_y), mask)

    # Text section
    tx = 280
    f_badge = _font(20, bold=True)
    f_name = _font(42, bold=True)
    f_user = _font(24, bold=False)
    f_meta = _font(22, bold=True)

    draw.rounded_rectangle([(tx, 58), (tx + 225, 92)], radius=12, fill=(88, 101, 242, 230))
    draw.text((tx + 16, 63), "SELAMAT DATANG", font=f_badge, fill=(255, 255, 255, 255))

    clean_name = (display_name or "Member")[:22]
    draw.text((tx, 112), clean_name, font=f_name, fill=(255, 255, 255, 255))
    draw.text((tx, 168), f"@{username}"[:30], font=f_user, fill=(185, 195, 225, 255))

    draw.line([(tx, 212), (w - 58, 212)], fill=(255, 255, 255, 40), width=2)
    draw.text((tx, 230), f"Member ke-{member_count}  •  {guild_name}"[:45], font=f_meta, fill=(254, 231, 92, 255))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    buf.seek(0)
    return buf


async def build_welcome_card(member):
    avatar_bytes = None
    try:
        avatar_bytes = await member.display_avatar.with_size(256).read()
    except Exception:
        pass
    member_count = member.guild.member_count or len(member.guild.members)
    return await asyncio.to_thread(
        _render_welcome_card,
        avatar_bytes,
        member.display_name,
        member.name,
        member_count,
        member.guild.name,
    )


def welcome_embed(member):
    v_ch = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and "verify" in c.name.lower(), member.guild.channels)
    v_mention = v_ch.mention if v_ch else f"<#{RULES_CHANNEL_ID}>"
    e = discord.Embed(
        title="👋 ╎ Member Baru Bergabung!",
        description=(
            f"Selamat datang {member.mention} di **{member.guild.name}**! 🎉\n"
            f"• Baca aturan di <#{RULES_CHANNEL_ID}>\n"
            f"• Klik tombol verifikasi di {v_mention} untuk membuka semua channel\n"
            f"• Ambil role di <#1573524001240780820>"
        ),
        colour=0xEB459E,
    )
    e.set_image(url="attachment://welcome.png")
    e.set_footer(text=f"Member ke-{member.guild.member_count}")
    return e


class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Saya Setuju Rules & Verifikasi", style=discord.ButtonStyle.success, emoji="✅", custom_id="verify_member_btn")
    async def verify_btn(self, interaction: discord.Interaction, _btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        role = interaction.guild.get_role(MEMBER_ROLE_ID)
        if not role:
            await interaction.followup.send("❌ Role Member tidak ditemukan. Hubungi Staff.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.followup.send(f"ℹ️ Kamu sudah terverifikasi dan memiliki role {role.mention}.", ephemeral=True)
            return
        try:
            await interaction.user.add_roles(role, reason="Verifikasi Member via tombol rules")
        except Exception as exc:
            await interaction.followup.send(f"❌ Gagal memberikan role: {exc}", ephemeral=True)
            return
        await _send_modlog(interaction.guild, "✅ Member Terverifikasi", f"{interaction.user.mention} (`{interaction.user.name}`)", GREEN)
        await interaction.followup.send(
            f"✅ Verifikasi berhasil! Role {role.mention} sudah diberikan. Selamat bersenang-senang di **{interaction.guild.name}**!",
            ephemeral=True,
        )


def verify_panel_embed(guild):
    e = discord.Embed(
        title="📜 ╎ ATURAN & VERIFIKASI MEMBER",
        description=(
            f"Selamat datang di **{guild.name}**! Sebelum mulai berinteraksi, harap patuhi aturan berikut:\n\n"
            "1. **Saling menghormati** antar member & staff (no SARA, toxic berlebihan, atau pelecehan).\n"
            "2. **Dilarang spam**, flood mention, atau promosi/invite server lain tanpa izin.\n"
            "3. **Gunakan channel sesuai topiknya** masing-masing.\n"
            "4. **Patuhi Discord Terms of Service & Community Guidelines.**\n\n"
            "Klik tombol **✅ Saya Setuju Rules & Verifikasi** di bawah untuk mendapatkan akses penuh ke server."
        ),
        colour=GREEN,
    )
    e.set_footer(text=f"{guild.name} • Sistem Verifikasi Member")
    return e


@tree.command(name="panel-verify", description="Kirim panel verifikasi rules & aktifkan mode verifikasi (Staff/Owner)", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(channel="Channel tujuan panel verifikasi (default: #verify atau channel ini)")
async def slash_panel_verify(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not _has_mod_perm(interaction.user, "manage_guild"):
        await interaction.response.send_message("❌ Kamu tidak punya izin mengirim panel verifikasi.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    v_ch = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and "verify" in c.name.lower(), interaction.guild.channels)
    ch = channel or v_ch or interaction.guild.get_channel(RULES_CHANNEL_ID) or interaction.channel
    try:
        await ch.send(embed=verify_panel_embed(interaction.guild), view=VerifyView())
    except Exception as exc:
        await interaction.followup.send(f"❌ Gagal mengirim panel ke {ch.mention}: `{exc}`", ephemeral=True)
        return
    VERIFY_CONFIG["enabled"] = True
    _save_verify_config()
    await interaction.followup.send(
        f"✅ Panel verifikasi dikirim ke {ch.mention} dan mode verifikasi tombol sekarang **AKTIF**.",
        ephemeral=True,
    )


async def _ensure_verify_panel(guild):
    if not guild or not is_verify_enabled():
        return
    v_ch = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and "verify" in c.name.lower(), guild.channels)
    if not v_ch:
        return
    try:
        async for m in v_ch.history(limit=20):
            if m.author == client.user and m.embeds and "VERIFIKASI MEMBER" in (m.embeds[0].title or ""):
                return
        await v_ch.send(embed=verify_panel_embed(guild), view=VerifyView())
    except Exception as exc:
        log("ensure verify panel error:", exc)
