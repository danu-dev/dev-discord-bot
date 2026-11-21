from bot_modules.core import *
from bot_modules.core import _send_modlog

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


ML_RANK_TIERS = [
    (2,  "⚔️ ╎ Warrior",           1573531049366003732),
    (4,  "🛡️ ╎ Elite",             1573531055519043734),
    (6,  "🎖️ ╎ Master",            1573531059151175680),
    (8,  "💠 ╎ Grandmaster",       1573531062955540528),
    (10, "🔮 ╎ Epic",              1573531068903194747),
    (13, "🦁 ╎ Legend",            1573531073646960720),
    (16, "🐉 ╎ Mythic",            1573531076872110123),
    (20, "👑 ╎ Mythical Honor",    1573531080152186931),
    (25, "🔥 ╎ Mythical Glory",    1573531084359204894),
    (30, "⚡ ╎ Mythical Immortal", 1573531088284819506),
]


def _ml_rank_for_level(lvl):
    tier = None
    for req, name, rid in ML_RANK_TIERS:
        if lvl >= req:
            tier = (req, name, rid)
    if not tier:
        return "Unranked", None
    req, name, rid = tier
    if lvl >= 30:
        stars = lvl - 29
        return f"{name} ★{stars}", rid
    return name, rid


def _rank_embed(member):
    xp = XP_DATA.get(str(member.id), 0)
    lvl = _xp_level(xp)
    next_xp = (lvl + 1) * (lvl + 1) * 100
    rank_label, _ = _ml_rank_for_level(lvl)
    e = discord.Embed(title=f"🏅 ╎ Rank {member.display_name}", colour=GOLD)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Rank ML", value=f"**{rank_label}**", inline=True)
    e.add_field(name="Level", value=f"**{lvl}**", inline=True)
    e.add_field(name="XP", value=f"**{xp}** / {next_xp}", inline=True)
    return e


def _leaderboard_embed(guild):
    top = sorted(XP_DATA.items(), key=lambda kv: -kv[1])[:10]
    lines = []
    for i, (uid, xp) in enumerate(top, 1):
        m = guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        lvl = _xp_level(xp)
        rank_label, _ = _ml_rank_for_level(lvl)
        lines.append(f"`{i}.` **{name}** — {rank_label} (Lv.**{lvl}** • {xp} XP)")
    return discord.Embed(
        title=f"🏆 ╎ Leaderboard {guild.name}",
        description="\n".join(lines) or "Belum ada data XP.",
        colour=GOLD,
    )


rank_group = discord.app_commands.Group(name="rank", description="Level, rank ML, dan leaderboard")
game_group = discord.app_commands.Group(name="game", description="Mini-games dan kasino")
ekonomi_group = discord.app_commands.Group(name="ekonomi", description="Koin, kerja, dan toko role")


@rank_group.command(name="cek", description="Cek level & XP kamu")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    await interaction.response.send_message(embed=_rank_embed(member or interaction.user))


@rank_group.command(name="leaderboard", description="Lihat top 10 member paling aktif")
async def slash_leaderboard(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_leaderboard_embed(interaction.guild))


async def _sync_ml_role(guild, member, lvl):
    rank_label, target_rid = _ml_rank_for_level(lvl)
    if not target_rid:
        return rank_label
    ml_ids = {rid for _, _, rid in ML_RANK_TIERS}
    old_roles = [r for r in member.roles if r.id in ml_ids and r.id != target_rid]
    new_role = guild.get_role(target_rid)
    try:
        if old_roles:
            await member.remove_roles(*old_roles, reason=f"Sync rank {rank_label}")
        if new_role and new_role not in member.roles:
            await member.add_roles(new_role, reason=f"Sync rank {rank_label}")
    except Exception:
        pass
    return rank_label


@tree.command(name="help", description="Lihat daftar command untuk member & staff", guild=discord.Object(id=GUILD_ID))
async def slash_help(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_help_embed(interaction.user), ephemeral=True)


@rank_group.command(name="daftar", description="Lihat daftar semua tier Rank ML berdasarkan level")
async def slash_ranks(interaction: discord.Interaction):
    lines = [f"• **Level {req}** → **{name}**" for req, name, _ in ML_RANK_TIERS]
    lines.append("• **Level 30+** → **⚡ ╎ Mythical Immortal ★1, ★2, ★3, ...** *(terus naik setiap level!)*")
    e = discord.Embed(title="🏅 ╎ Daftar Rank ML Server", description="\n".join(lines), colour=GOLD)
    e.set_footer(text="Rajin ngobrol atau main mini-game buat naik level!")
    await interaction.response.send_message(embed=e)


@rank_group.command(name="setlevel", description="Atur level member & sync rank ML (khusus Owner)")
@discord.app_commands.describe(member="Member target", level="Level target (0-200)")
async def slash_setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Command ini khusus Owner.", ephemeral=True)
        return
    lvl = max(0, min(200, level))
    XP_DATA[str(member.id)] = lvl * lvl * 100
    _save_xp()
    rank_label = await _sync_ml_role(interaction.guild, member, lvl)
    await interaction.response.send_message(f"✅ Level {member.mention} diatur ke **Level {lvl}** ({rank_label}).")


_rng = random.SystemRandom()
GAME_COOLDOWN = {}  # (user_id, game) -> last_timestamp
GAME_CD_SECONDS = 15


def _check_game_cd(user_id, game):
    now = time.time()
    key = (user_id, game)
    left = GAME_CD_SECONDS - (now - GAME_COOLDOWN.get(key, 0))
    if left > 0:
        return int(left) + 1
    GAME_COOLDOWN[key] = now
    return 0


async def _grant_bonus_xp(guild, member, amount):
    uid = str(member.id)
    old_xp = XP_DATA.get(uid, 0)
    new_xp = old_xp + amount
    XP_DATA[uid] = new_xp
    _save_xp()
    _add_coins(member.id, max(10, amount * 2))
    old_lvl, new_lvl = _xp_level(old_xp), _xp_level(new_xp)
    if new_lvl > old_lvl:
        rank_label = await _sync_ml_role(guild, member, new_lvl)
        return f"\n🎉 Naik ke **Level {new_lvl}** ({rank_label})!"
    return ""


@game_group.command(name="suit", description="Main Batu Gunting Kertas lawan bot")
@discord.app_commands.choices(pilihan=[
    discord.app_commands.Choice(name="🪨 Batu", value="batu"),
    discord.app_commands.Choice(name="✂️ Gunting", value="gunting"),
    discord.app_commands.Choice(name="📄 Kertas", value="kertas"),
])
async def slash_suit(interaction: discord.Interaction, pilihan: discord.app_commands.Choice[str]):
    wait = _check_game_cd(interaction.user.id, "suit")
    if wait:
        await interaction.response.send_message(f"⏳ Jangan spam! Tunggu **{wait} detik** lagi buat `/suit`.", ephemeral=True)
        return
    bot_pick = _rng.choice(["batu", "gunting", "kertas"])
    icons = {"batu": "🪨 Batu", "gunting": "✂️ Gunting", "kertas": "📄 Kertas"}
    wins = {("batu", "gunting"), ("gunting", "kertas"), ("kertas", "batu")}
    user_val = pilihan.value
    if user_val == bot_pick:
        res = "🤝 **Seri!**"
    elif (user_val, bot_pick) in wins:
        bonus = _rng.randint(8, 25)
        lvl_msg = await _grant_bonus_xp(interaction.guild, interaction.user, bonus)
        res = f"🎉 **Kamu Menang!** (+{bonus} XP){lvl_msg}"
    else:
        res = "😈 **Bot Menang!** Coba lagi!"
    await interaction.response.send_message(f"Kamu: {icons[user_val]} vs Bot: {icons[bot_pick]}\n{res}")


@game_group.command(name="coinflip", description="Lempar koin (Kepala / Ekor)")
@discord.app_commands.choices(tebak=[
    discord.app_commands.Choice(name="🪙 Kepala", value="kepala"),
    discord.app_commands.Choice(name="🦅 Ekor", value="ekor"),
])
async def slash_coinflip(interaction: discord.Interaction, tebak: discord.app_commands.Choice[str] = None):
    wait = _check_game_cd(interaction.user.id, "coinflip")
    if wait:
        await interaction.response.send_message(f"⏳ Jangan spam! Tunggu **{wait} detik** lagi buat `/coinflip`.", ephemeral=True)
        return
    hasil = _rng.choice(["kepala", "ekor"])
    label = "🪙 Kepala" if hasil == "kepala" else "🦅 Ekor"
    if not tebak:
        await interaction.response.send_message(f"Hasil lempar koin: **{label}**!")
        return
    if tebak.value == hasil:
        bonus = _rng.randint(8, 25)
        lvl_msg = await _grant_bonus_xp(interaction.guild, interaction.user, bonus)
        await interaction.response.send_message(f"Hasil: **{label}** — 🎉 **Tebakanmu benar!** (+{bonus} XP){lvl_msg}")
    else:
        await interaction.response.send_message(f"Hasil: **{label}** — 😅 Meleset!")


CONFESS_CHANNEL = 1573535779546734624
DAILY_FILE = os.environ.get("DAILY_FILE", "dev-daily-data.json")
DAILY_DATA = {}
if os.path.exists(DAILY_FILE):
    try:
        DAILY_DATA = json.loads(read(DAILY_FILE))
    except Exception:
        DAILY_DATA = {}


def _save_daily():
    try:
        with open(DAILY_FILE, "w", encoding="utf-8") as fh:
            json.dump(DAILY_DATA, fh)
    except Exception:
        pass


@ekonomi_group.command(name="daily", description="Klaim bonus XP & koin harian")
async def slash_daily(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    now = time.time()
    rec = DAILY_DATA.get(uid, {"last": 0, "streak": 0})
    elapsed = now - rec.get("last", 0)
    if elapsed < 86400:
        left = int(86400 - elapsed)
        hrs, rem = divmod(left, 3600)
        mins = rem // 60
        await interaction.response.send_message(f"⏳ Kamu sudah klaim `/daily` hari ini! Coba lagi dalam **{hrs}j {mins}m**.", ephemeral=True)
        return
    streak = rec.get("streak", 0) + 1 if elapsed < 172800 else 1
    DAILY_DATA[uid] = {"last": now, "streak": streak}
    _save_daily()
    bonus = 50 + min((streak - 1) * 15, 150)
    lvl_msg = await _grant_bonus_xp(interaction.guild, interaction.user, bonus)
    await interaction.response.send_message(f"🎁 **Daily Claim!** +**{bonus} XP** (🔥 Streak: **{streak} hari**){lvl_msg}")


@tree.command(name="confess", description="Kirim pesan anonim ke #🤫╎confess", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(pesan="Pesan atau curhatan anonim kamu")
async def slash_confess(interaction: discord.Interaction, pesan: str):
    await interaction.response.defer(ephemeral=True)
    ch = interaction.guild.get_channel(CONFESS_CHANNEL) or discord.utils.find(
        lambda c: isinstance(c, discord.TextChannel) and "confess" in c.name.lower(),
        interaction.guild.channels,
    )
    if not ch:
        await interaction.followup.send("❌ Channel confess tidak ditemukan.", ephemeral=True)
        return
    e = discord.Embed(title="🤫 ╎ Confess Anonim", description=pesan[:2000], colour=0x9B59B6)
    e.set_footer(text="Gunakan /confess untuk mengirim pesan anonim")
    await ch.send(embed=e)
    await _send_modlog(interaction.guild, "🤫 Confess Masuk", f"Pengirim: {interaction.user.mention} (`{interaction.user.id}`)\nIsi: {pesan[:500]}", 0x9B59B6)
    await interaction.followup.send(f"✅ Confess kamu sudah dikirim secara anonim ke {ch.mention}!", ephemeral=True)


@game_group.command(name="tebakangka", description="Tebak angka 1 sampai 10")
@discord.app_commands.describe(angka="Pilih angka 1 - 10")
async def slash_tebakangka(interaction: discord.Interaction, angka: int):
    if not 1 <= angka <= 10:
        await interaction.response.send_message("❌ Pilih angka antara **1 sampai 10**!", ephemeral=True)
        return
    wait = _check_game_cd(interaction.user.id, "tebakangka")
    if wait:
        await interaction.response.send_message(f"⏳ Jangan spam! Tunggu **{wait} detik** lagi buat `/tebakangka`.", ephemeral=True)
        return
    rahasia = _rng.randint(1, 10)
    if angka == rahasia:
        bonus = _rng.randint(20, 50)
        lvl_msg = await _grant_bonus_xp(interaction.guild, interaction.user, bonus)
        await interaction.response.send_message(f"🎯 Tepat banget! Angkanya **{rahasia}**! 🎉 (+{bonus} XP){lvl_msg}")
    else:
        await interaction.response.send_message(f"❌ Kamu tebak **{angka}**, padahal angkanya **{rahasia}**!")


# --- Economy (Coins & Role Shop), Invite Tracker & Server Counters --------
ECONOMY_FILE = os.environ.get("ECONOMY_FILE", "dev-economy-data.json")
ECONOMY_DATA = {"coins": {}, "invites": {}}
if os.path.exists(ECONOMY_FILE):
    try:
        _loaded_eco = json.loads(read(ECONOMY_FILE))
        if isinstance(_loaded_eco, dict):
            ECONOMY_DATA["coins"] = _loaded_eco.get("coins", {})
            ECONOMY_DATA["invites"] = _loaded_eco.get("invites", {})
            ECONOMY_DATA["work_cd"] = _loaded_eco.get("work_cd", {})
            ECONOMY_DATA["monthly_cd"] = _loaded_eco.get("monthly_cd", {})
            ECONOMY_DATA["inventory"] = _loaded_eco.get("inventory", {})
    except Exception:
        pass


def _save_economy():
    try:
        with open(ECONOMY_FILE, "w", encoding="utf-8") as fh:
            json.dump(ECONOMY_DATA, fh)
    except Exception:
        pass


def _get_coins(uid):
    return int(ECONOMY_DATA["coins"].get(str(uid), 0))


def _add_coins(uid, amount):
    key = str(uid)
    ECONOMY_DATA["coins"][key] = max(0, _get_coins(uid) + int(amount))
    _save_economy()
    return ECONOMY_DATA["coins"][key]


SHOP_COLOR_ROLES = [
    ("ruby", "🎨 ╎ Ruby Red", 0xE74C3C, 250, "Warna nama merah eksklusif"),
    ("cyan", "💎 ╎ Cyan Diamond", 0x1ABC9C, 400, "Warna cyan + akses #premium-chat"),
    ("gold", "⚡ ╎ Gold Rush", 0xF1C40F, 600, "Warna emas + akses #VIP Lounge"),
    ("sultan", "👑 ╎ Sultan DEV", 0x9B59B6, 1000, "Warna ungu + #sultan-lounge + bonus bulanan"),
]
SHOP_PREMIUM_ROLES = [
    ("bronze", "🥉 ╎ Bronze Member", 0xCD7F32, 350, "Badge Bronze + warna khusus"),
    ("silver", "🥈 ╎ Silver Member", 0xBDC3C7, 650, "Badge Silver + akses #premium-chat"),
    ("gold_m", "🥇 ╎ Gold Member", 0xF1C40F, 1000, "Badge Gold + akses VIP Lounge"),
    ("diamond", "💠 ╎ Diamond Member", 0x00D2D3, 1500, "Badge Diamond + VIP & bonus bulanan"),
    ("mythic", "🔮 ╎ Mythic Member", 0x8E44AD, 2200, "Badge Mythic + VIP & bonus bulanan"),
    ("galaxy", "🌌 ╎ Galaxy Member", 0x5B2C6F, 3000, "Badge Galaxy + #sultan-lounge"),
    ("dragon", "🐉 ╎ Dragon Lord", 0xE67E22, 4000, "Badge Dragon + prioritas event"),
    ("cosmic", "☄️ ╎ Cosmic Legend", 0xFF6B81, 5500, "Badge Legend + semua lounge premium"),
]
SHOP_SPECIAL_ROLES = [
    ("dev", "💻 ╎ Developer", 0x2ECC71, 1800, "Akses Developer / Sultan Lounge"),
    ("top_sup", "🏆 ╎ Top Supporter", 0xF39C12, 2000, "Badge Top Supporter + bonus bulanan"),
    ("early_sup", "🌟 ╎ Early Supporter", 0xFDCB6E, 1200, "Badge Early Supporter"),
    ("og", "🔥 ╎ OG Member", 0xE17055, 1600, "Badge OG + akses VIP & #premium-chat"),
    ("guardian", "🛡️ ╎ Guardian", 0x3498DB, 2800, "Badge Guardian + #sultan-lounge"),
    ("legendary", "💫 ╎ Legendary", 0xFF9FF3, 4500, "Badge Legendary + semua lounge"),
]
SHOP_ROLES = SHOP_COLOR_ROLES + SHOP_PREMIUM_ROLES + SHOP_SPECIAL_ROLES

MONTHLY_BONUS_BY_ROLE = {
    "☄️ ╎ Cosmic Legend": 2000,
    "💫 ╎ Legendary": 1800,
    "🐉 ╎ Dragon Lord": 1500,
    "🌌 ╎ Galaxy Member": 1200,
    "🛡️ ╎ Guardian": 1000,
    "🔮 ╎ Mythic Member": 900,
    "🏆 ╎ Top Supporter": 800,
    "💻 ╎ Developer": 750,
    "🔥 ╎ OG Member": 700,
    "💠 ╎ Diamond Member": 650,
    "👑 ╎ Sultan DEV": 600,
    "🌟 ╎ Early Supporter": 500,
    "🥇 ╎ Gold Member": 450,
    "🥈 ╎ Silver Member": 300,
    "🥉 ╎ Bronze Member": 200,
}


class ShopRoleSelect(discord.ui.Select):
    def __init__(self, items, placeholder, custom_id):
        opts = [
            discord.SelectOption(label=f"{name} ({price} Koin)", value=key, description=desc)
            for key, name, _col, price, desc in items
        ]
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, options=opts, custom_id=custom_id)

    async def callback(self, interaction: discord.Interaction):
        key = self.values[0]
        item = next((x for x in SHOP_ROLES if x[0] == key), None)
        if not item:
            await interaction.response.send_message("❌ Item tidak ditemukan.", ephemeral=True)
            return
        _k, role_name, role_color, price, _desc = item
        bal = _get_coins(interaction.user.id)
        if bal < price:
            await interaction.response.send_message(
                f"❌ Koin kamu kurang! Saldo: **{bal}** Koin, harga **{role_name}**: **{price}** Koin.",
                ephemeral=True,
            )
            return
        role = find_role(interaction.guild, role_name)
        if not role:
            role = await interaction.guild.create_role(
                name=role_name,
                colour=discord.Colour(role_color),
                hoist=False,
                mentionable=False,
                reason="Shop Role otomatis",
            )
        if role in interaction.user.roles:
            await interaction.response.send_message(f"ℹ️ Kamu sudah memiliki role {role.mention}.", ephemeral=True)
            return
        _add_coins(interaction.user.id, -price)
        await interaction.user.add_roles(role, reason=f"Dibeli di /shop seharga {price} Koin")
        await interaction.response.send_message(
            f"🎉 Berhasil membeli & memasang {role.mention}! Sisa koin: **{_get_coins(interaction.user.id)}**.",
            ephemeral=True,
        )


class ShopProductSelect(discord.ui.Select):
    def __init__(self, products=None):
        opts = []
        if products:
            from bot_modules.shop_restock import RARITIES
            for p in products[:25]:
                meta = RARITIES.get(p["rarity"], {"emoji": "📦", "label": p["rarity"]})
                opts.append(
                    discord.SelectOption(
                        label=f"{meta['emoji']} {p['name']} ({p['price']} Koin)",
                        value=str(p["id"]),
                        description=f"Stok: {p['stock']} • {meta['label']} ({p['rarity_percent']}%)"[:100],
                    )
                )
        if not opts:
            opts = [discord.SelectOption(label="Belum ada produk tersedia", value="__empty__", description="Tunggu admin restock produk")]
        super().__init__(
            placeholder="📦 Pilih Produk Spesial / Premium...",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id="shop_product_select",
        )

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "__empty__" or not val.isdigit():
            await interaction.response.send_message("📭 Belum ada produk yang tersedia saat ini.", ephemeral=True)
            return
        from bot_modules.shop_restock import (
            _db_run, _get_product_sync, _update_product_sync,
            _log_history_sync, _send_restock_webhook,
        )
        pid = int(val)
        prod = await _db_run(_get_product_sync, pid)
        if not prod or prod["stock"] <= 0:
            await interaction.response.send_message("❌ Maaf, stok produk ini baru saja habis.", ephemeral=True)
            return
        bal = _get_coins(interaction.user.id)
        price = int(prod["price"])
        if bal < price:
            await interaction.response.send_message(
                f"❌ Koin kamu kurang! Saldo: **{bal}** Koin, harga **{prod['name']}**: **{price}** Koin.",
                ephemeral=True,
            )
            return
        old_stock = int(prod["stock"])
        new_stock = old_stock - 1
        role = find_role(interaction.guild, prod["name"])
        if role and role in interaction.user.roles:
            await interaction.response.send_message(f"ℹ️ Kamu sudah memiliki role {role.mention}.", ephemeral=True)
            return
        _add_coins(interaction.user.id, -price)
        if role:
            await interaction.user.add_roles(role, reason=f"Dibeli di /ekonomi shop seharga {price} Koin")
        inv = ECONOMY_DATA.setdefault("inventory", {}).setdefault(str(interaction.user.id), [])
        inv.append(prod["name"])
        _save_economy()
        updated = await _db_run(_update_product_sync, pid, {"stock": new_stock})
        await _db_run(
            _log_history_sync, pid, updated["name"], "PURCHASE",
            old_stock, new_stock, updated["rarity"], updated["rarity_percent"], str(interaction.user.id),
        )
        if new_stock == 0:
            await _send_restock_webhook(updated, "OUT_OF_STOCK", old_stock, 0)
        extra = f" & role {role.mention} dipasang" if role else ""
        await interaction.response.send_message(
            f"🎉 Berhasil membeli **{updated['name']}**{extra} seharga **{price:,} Koin**!\nSisa stok: **{new_stock}** • Sisa koin: **{_get_coins(interaction.user.id):,}**.",
            ephemeral=True,
        )


class ShopView(discord.ui.View):
    def __init__(self, products=None):
        super().__init__(timeout=None)
        self.add_item(ShopProductSelect(products))


@ekonomi_group.command(name="koin", description="Cek saldo koin & XP kamu atau member lain")
async def slash_koin(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    coins = _get_coins(m.id)
    xp = XP_DATA.get(str(m.id), 0)
    invs = int(ECONOMY_DATA["invites"].get(str(m.id), 0))
    e = discord.Embed(title=f"🪙 ╎ Dompet {m.display_name}", colour=GOLD)
    e.set_thumbnail(url=m.display_avatar.url)
    e.add_field(name="🪙 Koin", value=f"**{coins}**", inline=True)
    e.add_field(name="⭐ XP", value=f"**{xp}**", inline=True)
    e.add_field(name="🤝 Invite", value=f"**{invs}** orang", inline=True)
    e.set_footer(text="Dapatkan koin dari chat, /daily, mini-game, dan ajak teman!")
    await interaction.response.send_message(embed=e)


@ekonomi_group.command(name="shop", description="Buka toko server menggunakan koin")
async def slash_shop(interaction: discord.Interaction):
    from bot_modules.shop_restock import _db_run, _list_products_sync, RARITIES
    bal = _get_coins(interaction.user.id)
    all_prods = await _db_run(_list_products_sync)
    in_stock = [p for p in all_prods if p["stock"] > 0]

    e = discord.Embed(
        title="🛒 ╎ DEV SERVER SHOP",
        description=f"💰 **Saldo Kamu:** `{bal:,} Koin`\nPilih item pada menu di bawah untuk langsung membeli & memasang role.",
        colour=0xD4AF37,
    )
    if interaction.guild and interaction.guild.icon:
        e.set_thumbnail(url=interaction.guild.icon.url)

    color_names = {"🎨 ╎ Ruby Red", "💎 ╎ Cyan Diamond", "⚡ ╎ Gold Rush", "👑 ╎ Sultan DEV"}
    groups = [
        ("🎨 Color Roles", lambda p: p["name"] in color_names),
        ("💠 Special & Divine (0.01% – 0.1%)", lambda p: p["name"] not in color_names and p["rarity"] in ("special", "divine")),
        ("🌌 Legendary & Mythic (0.5% – 1%)", lambda p: p["name"] not in color_names and p["rarity"] in ("legendary", "mythic")),
        ("💎 Premium & Special Collection (3% – 5%)", lambda p: p["name"] not in color_names and p["rarity"] in ("elite", "premium")),
    ]
    for group_title, pred in groups:
        items = [p for p in all_prods if pred(p)]
        if not items:
            continue
        lines = []
        for p in items[:10]:
            meta = RARITIES[p["rarity"]]
            stok_txt = f"**`{p['stock']}` stok**" if p["stock"] > 0 else "~~`HABIS`~~"
            lines.append(f"{meta['emoji']} **{p['name']}** — 🪙 `{p['price']:,}` • {stok_txt}\n┗ _{p['description'] or '-'}_")
        e.add_field(name=group_title, value="\n".join(lines)[:1024], inline=False)

    if not all_prods:
        e.add_field(name="📦 Katalog", value="_Belum ada produk di toko._", inline=False)

    e.set_footer(text="Pilih item di dropdown bawah • Bonus pemilik role: /ekonomi bulanan")
    await interaction.response.send_message(embed=e, view=ShopView(in_stock), ephemeral=True)


@ekonomi_group.command(name="bulanan", description="Klaim bonus koin bulanan untuk pemilik Role Shop")
async def slash_monthly(interaction: discord.Interaction):
    member_role_names = {r.name for r in interaction.user.roles}
    best_name, best_bonus = None, 0
    for rname, bonus in MONTHLY_BONUS_BY_ROLE.items():
        if rname in member_role_names and bonus > best_bonus:
            best_name, best_bonus = rname, bonus
    if not best_name:
        await interaction.response.send_message("❌ Kamu belum punya role Premium/Special/Sultan dari `/shop`.", ephemeral=True)
        return
    uid = str(interaction.user.id)
    monthly_map = ECONOMY_DATA.setdefault("monthly_cd", {})
    now = time.time()
    elapsed = now - float(monthly_map.get(uid, 0))
    if elapsed < 2592000:
        days_left = int((2592000 - elapsed) // 86400) + 1
        await interaction.response.send_message(f"⏳ Bonus bulanan sudah diklaim! Coba lagi dalam **{days_left} hari**.", ephemeral=True)
        return
    monthly_map[uid] = now
    new_bal = _add_coins(interaction.user.id, best_bonus)
    await interaction.response.send_message(
        f"🎁 **Bonus Bulanan ({best_name})!** Kamu dapat **+{best_bonus} Koin**.\nSaldo sekarang: **{new_bal} Koin**."
    )


@ekonomi_group.command(name="give", description="Tambah koin ke member (khusus Owner)")
@discord.app_commands.describe(member="Member target", jumlah="Jumlah koin yang ditambahkan")
async def slash_givekoin(interaction: discord.Interaction, member: discord.Member, jumlah: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Command ini khusus Owner.", ephemeral=True)
        return
    new_bal = _add_coins(member.id, jumlah)
    await interaction.response.send_message(f"🪙 Saldo {member.mention} sekarang **{new_bal} Koin**.")


# --- Casino Mini-Games (Blackjack & Roulette) -------------------------------
_SUITS = ("♠", "♥", "♦", "♣")
_RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
_ROULETTE_RED = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def _bj_value(hand):
    total = 0
    aces = 0
    for rank, _suit in hand:
        if rank == "A":
            total += 11
            aces += 1
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _bj_fmt(hand, hide_second=False):
    if hide_second and len(hand) >= 2:
        return f"`{hand[0][0]}{hand[0][1]}` `??`"
    return " ".join(f"`{r}{s}`" for r, s in hand)


class BlackjackView(discord.ui.View):
    def __init__(self, user_id, bet, deck, player_hand, dealer_hand):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.bet = bet
        self.deck = deck
        self.player_hand = player_hand
        self.dealer_hand = dealer_hand
        self.finished = False

    def _embed(self, status_text=None, reveal=False, colour=GOLD):
        p_val = _bj_value(self.player_hand)
        d_val = _bj_value(self.dealer_hand) if reveal else "?"
        e = discord.Embed(title=f"🃏 ╎ Blackjack (Taruhan: {self.bet} Koin)", colour=colour)
        e.add_field(name=f"Kartu Kamu ({p_val})", value=_bj_fmt(self.player_hand), inline=False)
        e.add_field(name=f"Kartu Dealer ({d_val})", value=_bj_fmt(self.dealer_hand, hide_second=not reveal), inline=False)
        if status_text:
            e.description = status_text
        e.set_footer(text=f"Saldo sekarang: {_get_coins(self.user_id)} Koin")
        return e

    def _finish_stand(self):
        self.finished = True
        for item in self.children:
            item.disabled = True
        while _bj_value(self.dealer_hand) < 17 and self.deck:
            self.dealer_hand.append(self.deck.pop())
        p_val = _bj_value(self.player_hand)
        d_val = _bj_value(self.dealer_hand)
        if p_val > 21:
            return self._embed(f"💥 **Bust!** Kamu kalah **{self.bet} Koin**.", reveal=True, colour=RED)
        if d_val > 21 or p_val > d_val:
            payout = self.bet * 2
            _add_coins(self.user_id, payout)
            return self._embed(f"🎉 **Menang!** Kamu dapat **+{payout} Koin** (untung +{self.bet})!", reveal=True, colour=GREEN)
        if p_val == d_val:
            _add_coins(self.user_id, self.bet)
            return self._embed(f"🤝 **Seri (Push)!** Taruhan **{self.bet} Koin** dikembalikan.", reveal=True, colour=BLURPLE)
        return self._embed(f"😈 **Dealer Menang!** Kamu kalah **{self.bet} Koin**.", reveal=True, colour=RED)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Ini meja Blackjack milik orang lain!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit_btn(self, interaction: discord.Interaction, _b):
        if self.finished:
            return
        self.double_btn.disabled = True
        self.player_hand.append(self.deck.pop())
        p_val = _bj_value(self.player_hand)
        if p_val > 21:
            self.finished = True
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(
                embed=self._embed(f"💥 **Bust ({p_val})!** Kamu kalah **{self.bet} Koin**.", reveal=True, colour=RED),
                view=self,
            )
        elif p_val == 21:
            embed = self._finish_stand()
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=self._embed(), view=self)

    @discord.ui.button(label="Stand", emoji="🛑", style=discord.ButtonStyle.secondary)
    async def stand_btn(self, interaction: discord.Interaction, _b):
        if self.finished:
            return
        embed = self._finish_stand()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Double Down", emoji="💰", style=discord.ButtonStyle.success)
    async def double_btn(self, interaction: discord.Interaction, _b):
        if self.finished or len(self.player_hand) != 2:
            return
        if _get_coins(self.user_id) < self.bet:
            await interaction.response.send_message(f"❌ Koin kurang untuk Double Down (butuh **{self.bet}** lagi).", ephemeral=True)
            return
        _add_coins(self.user_id, -self.bet)
        self.bet *= 2
        self.player_hand.append(self.deck.pop())
        embed = self._finish_stand()
        await interaction.response.edit_message(embed=embed, view=self)


@game_group.command(name="blackjack", description="Main Blackjack pakai koin")
@discord.app_commands.describe(taruhan="Jumlah taruhan (10 - 5000 koin)")
async def slash_blackjack(interaction: discord.Interaction, taruhan: int):
    if not 10 <= taruhan <= 5000:
        await interaction.response.send_message("❌ Taruhan harus antara **10 sampai 5000 Koin**.", ephemeral=True)
        return
    bal = _get_coins(interaction.user.id)
    if bal < taruhan:
        await interaction.response.send_message(f"❌ Koin kamu kurang! Saldo saat ini: **{bal} Koin**.", ephemeral=True)
        return
    wait = _check_game_cd(interaction.user.id, "blackjack")
    if wait:
        await interaction.response.send_message(f"⏳ Jangan spam! Tunggu **{wait} detik** lagi sebelum main `/blackjack`.", ephemeral=True)
        return
    _add_coins(interaction.user.id, -taruhan)
    deck = [(r, s) for s in _SUITS for r in _RANKS]
    _rng.shuffle(deck)
    p_hand = [deck.pop(), deck.pop()]
    d_hand = [deck.pop(), deck.pop()]
    view = BlackjackView(interaction.user.id, taruhan, deck, p_hand, d_hand)
    p_val, d_val = _bj_value(p_hand), _bj_value(d_hand)
    if p_val == 21 or d_val == 21:
        view.finished = True
        for item in view.children:
            item.disabled = True
        if p_val == 21 and d_val == 21:
            _add_coins(interaction.user.id, taruhan)
            emb = view._embed(f"🤝 **Seri (Sama-sama Blackjack)!** Taruhan **{taruhan} Koin** dikembalikan.", reveal=True, colour=BLURPLE)
        elif p_val == 21:
            payout = int(taruhan * 2.5)
            _add_coins(interaction.user.id, payout)
            emb = view._embed(f"👑 **BLACKJACK!** Kamu menang **+{payout} Koin**!", reveal=True, colour=GREEN)
        else:
            emb = view._embed(f"😈 **Dealer Blackjack!** Kamu kalah **{taruhan} Koin**.", reveal=True, colour=RED)
        await interaction.response.send_message(embed=emb, view=view)
        return
    await interaction.response.send_message(embed=view._embed("Pilih **Hit**, **Stand**, atau **Double Down**:"), view=view)


@game_group.command(name="roulette", description="Main Roulette pakai koin")
@discord.app_commands.describe(
    taruhan="Jumlah taruhan (10 - 5000 koin)",
    pilihan="Pilih warna, genap/ganjil, atau besar/kecil",
    angka="Atau tebak angka persis 0 - 36 (hadiah 36x)",
)
@discord.app_commands.choices(pilihan=[
    discord.app_commands.Choice(name="🔴 Merah (menang 2x)", value="merah"),
    discord.app_commands.Choice(name="⚫ Hitam (menang 2x)", value="hitam"),
    discord.app_commands.Choice(name="🔢 Genap (menang 2x)", value="genap"),
    discord.app_commands.Choice(name="🔢 Ganjil (menang 2x)", value="ganjil"),
    discord.app_commands.Choice(name="⬇️ Kecil 1-18 (menang 2x)", value="1-18"),
    discord.app_commands.Choice(name="⬆️ Besar 19-36 (menang 2x)", value="19-36"),
    discord.app_commands.Choice(name="🟢 Hijau / 0 (menang 36x)", value="hijau"),
])
async def slash_roulette(
    interaction: discord.Interaction,
    taruhan: int,
    pilihan: discord.app_commands.Choice[str] = None,
    angka: int = None,
):
    if not 10 <= taruhan <= 5000:
        await interaction.response.send_message("❌ Taruhan harus antara **10 sampai 5000 Koin**.", ephemeral=True)
        return
    if angka is not None:
        if not 0 <= angka <= 36:
            await interaction.response.send_message("❌ Angka harus antara **0 sampai 36**.", ephemeral=True)
            return
        pick = str(angka)
        pick_label = f"Angka {angka}"
        is_num = True
    elif pilihan is not None:
        pick = pilihan.value
        pick_label = pilihan.name
        is_num = False
    else:
        await interaction.response.send_message("❌ Pilih `pilihan` dari menu atau isi `angka` (0-36).", ephemeral=True)
        return
    bal = _get_coins(interaction.user.id)
    if bal < taruhan:
        await interaction.response.send_message(f"❌ Koin kamu kurang! Saldo saat ini: **{bal} Koin**.", ephemeral=True)
        return
    wait = _check_game_cd(interaction.user.id, "roulette")
    if wait:
        await interaction.response.send_message(f"⏳ Tunggu **{wait} detik** lagi sebelum memutar Roulette.", ephemeral=True)
        return
    _add_coins(interaction.user.id, -taruhan)
    num = _rng.randint(0, 36)
    color_emoji = "🟢" if num == 0 else ("🔴" if num in _ROULETTE_RED else "⚫")
    color_name = "Hijau" if num == 0 else ("Merah" if num in _ROULETTE_RED else "Hitam")
    won = False
    mult = 2
    if is_num:
        won = (num == int(pick))
        mult = 36
    elif pick == "hijau":
        won = (num == 0)
        mult = 36
    elif pick == "merah":
        won = (num in _ROULETTE_RED)
    elif pick == "hitam":
        won = (num != 0 and num not in _ROULETTE_RED)
    elif pick == "genap":
        won = (num != 0 and num % 2 == 0)
    elif pick == "ganjil":
        won = (num % 2 == 1)
    elif pick == "1-18":
        won = (1 <= num <= 18)
    elif pick == "19-36":
        won = (19 <= num <= 36)

    if won:
        payout = taruhan * mult
        _add_coins(interaction.user.id, payout)
        res_txt = f"🎉 **MENANG!** Kamu dapat **+{payout} Koin** (`{mult}x`)!"
        col = GREEN
    else:
        res_txt = f"💸 **Kalah!** Kamu kehilangan **{taruhan} Koin**."
        col = RED

    e = discord.Embed(
        title="🎡 ╎ Roulette",
        description=f"Bola berhenti di: {color_emoji} **{num} ({color_name})**\nPilihanmu: **{pick_label}** • Taruhan: **{taruhan} Koin**\n\n{res_txt}",
        colour=col,
    )
    e.set_footer(text=f"Saldo: {_get_coins(interaction.user.id)} Koin")
    await interaction.response.send_message(embed=e)


WORK_CD_SECONDS = 300  # 5 menit cooldown
_WORK_JOBS = [
    ("Joki Rank ML", 35, 85),
    ("Streamer Gaming", 30, 95),
    ("Editor Clip Mabar", 30, 80),
    ("Rakit PC", 45, 95),
    ("Caster Turnamen", 50, 105),
    ("Benerin Bug Bot", 35, 85),
    ("DJ Lounge", 30, 75),
]
_WORK_JACKPOTS = [
    ("Juara Mini Turnamen", 120, 180),
    ("Dapet Donasi pas Live", 110, 165),
]


@ekonomi_group.command(name="kerja", description="Cari koin lewat kerja sampingan")
async def slash_kerja(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    work_cd_map = ECONOMY_DATA.setdefault("work_cd", {})
    now = time.time()
    last = float(work_cd_map.get(uid, 0))
    elapsed = now - last
    if elapsed < WORK_CD_SECONDS:
        left = int(WORK_CD_SECONDS - elapsed) + 1
        mins, secs = divmod(left, 60)
        await interaction.response.send_message(
            f"⏳ Masih capek, tunggu **{mins}m {secs}d** lagi baru bisa kerja.",
            ephemeral=True,
        )
        return
    work_cd_map[uid] = now
    if _rng.random() < 0.15:
        job_name, lo, hi = _rng.choice(_WORK_JACKPOTS)
        title = "🎉 Bonus Kerja"
        col = GOLD
    else:
        job_name, lo, hi = _rng.choice(_WORK_JOBS)
        title = "💼 Kerja Sampingan"
        col = GREEN
    earned = _rng.randint(lo, hi)
    desc = f"**{job_name}** — dapet **+{earned} Koin**"
    new_bal = _add_coins(interaction.user.id, earned)
    e = discord.Embed(title=title, description=desc, colour=col)
    e.set_footer(text=f"Saldo: {new_bal} Koin")
    await interaction.response.send_message(embed=e)


INVITE_CACHE = {}


async def _refresh_invites(guild):
    try:
        invs = await guild.invites()
        INVITE_CACHE[guild.id] = {inv.code: (inv.uses or 0, inv.inviter.id if inv.inviter else None) for inv in invs}
    except Exception:
        pass



@tree.command(name="invites", description="Lihat jumlah undangan & top pengundang server", guild=discord.Object(id=GUILD_ID))
async def slash_invites(interaction: discord.Interaction, member: discord.Member = None):
    m = member or interaction.user
    cnt = int(ECONOMY_DATA["invites"].get(str(m.id), 0))
    top = sorted(ECONOMY_DATA["invites"].items(), key=lambda kv: -int(kv[1]))[:10]
    lines = []
    for i, (uid, icnt) in enumerate(top, 1):
        u = interaction.guild.get_member(int(uid))
        uname = u.display_name if u else f"User {uid}"
        lines.append(f"`{i}.` **{uname}** — `{icnt}` undangan")
    e = discord.Embed(
        title="🤝 ╎ Invite Tracker",
        description=f"Undangan {m.mention}: **{cnt} orang**\n\n**🏆 Top Pengundang:**\n" + ("\n".join(lines) or "_Belum ada data_"),
        colour=GREEN,
    )
    await interaction.response.send_message(embed=e)


_LAST_COUNTER_UPDATE = 0.0


async def _update_server_counters(guild):
    global _LAST_COUNTER_UPDATE
    now = time.time()
    if now - _LAST_COUNTER_UPDATE < 300:
        return
    _LAST_COUNTER_UPDATE = now
    total_members = guild.member_count or len(guild.members)
    online_members = sum(1 for m in guild.members if m.status != discord.Status.offline)
    targets = [
        ("👥 Member:", f"👥 Member: {total_members}", 0),
        ("🟢 Online:", f"🟢 Online: {online_members}", 1),
    ]
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
    }
    for prefix, desired_name, pos in targets:
        ch = discord.utils.find(lambda c: isinstance(c, discord.VoiceChannel) and c.name.startswith(prefix), guild.channels)
        try:
            if not ch:
                await guild.create_voice_channel(desired_name, position=pos, overwrites=ow, reason="Live Server Counter")
            elif ch.name != desired_name:
                await ch.edit(name=desired_name, reason="Update Live Server Counter")
        except Exception as exc:
            log("counter update error:", exc)


for _grp in (rank_group, game_group, ekonomi_group):
    tree.add_command(_grp, guild=discord.Object(id=GUILD_ID))


