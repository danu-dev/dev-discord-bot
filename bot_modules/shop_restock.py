import asyncio
import datetime
import os
import sqlite3
import time
import aiohttp
import discord

from bot_modules.core import *
from bot_modules.core import _has_mod_perm

# --- Env Loader (without requiring external python-dotenv) -----------------
def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_dotenv()

SHOP_RESTOCK_DB = os.environ.get("SHOP_RESTOCK_DB", "shop_restock.db").strip() or "shop_restock.db"

RARITIES = {
    "premium":   {"label": "Premium",   "emoji": "🟡", "percent": 5.0,  "color": 0xF1C40F},
    "elite":     {"label": "Elite",     "emoji": "💎", "percent": 3.0,  "color": 0x8E44AD},
    "mythic":    {"label": "Mythic",    "emoji": "🔮", "percent": 1.0,  "color": 0x9B59B6},
    "legendary": {"label": "Legendary", "emoji": "🌌", "percent": 0.5,  "color": 0xD4AF37},
    "divine":    {"label": "Divine",    "emoji": "👑", "percent": 0.1,  "color": 0xFFD700},
    "special":   {"label": "Special",   "emoji": "💠", "percent": 0.01, "color": 0x111216},
}

RARITY_CHOICES = [
    discord.app_commands.Choice(name=f"{v['emoji']} {v['label']} ({v['percent']}%)", value=k)
    for k, v in RARITIES.items()
]

EVENT_TITLES = {
    "NEW_PRODUCT":   "✨ PRODUK BARU",
    "BACK_IN_STOCK": "🔔 KEMBALI TERSEDIA",
    "RESTOCK":       "📦 RESTOCK TERSEDIA",
    "OUT_OF_STOCK":  "🚫 STOK HABIS",
}

_DB_LOCK = asyncio.Lock()
_DEDUP_LOCK = asyncio.Lock()
_LAST_NOTIFIED = {}


def _db_connect():
    conn = sqlite3.connect(SHOP_RESTOCK_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db_sync():
    with _db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                price INTEGER NOT NULL CHECK(price >= 0),
                stock INTEGER NOT NULL DEFAULT 0 CHECK(stock >= 0),
                rarity TEXT NOT NULL,
                rarity_percent REAL NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS restock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                old_stock INTEGER NOT NULL,
                new_stock INTEGER NOT NULL,
                delta INTEGER NOT NULL,
                rarity TEXT NOT NULL,
                rarity_percent REAL NOT NULL,
                actor_id TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS shop_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.execute("INSERT OR IGNORE INTO shop_config (key, value) VALUES ('webhook_enabled', '1')")
        conn.commit()


_init_db_sync()


async def _db_run(fn, *args):
    async with _DB_LOCK:
        return await asyncio.to_thread(fn, *args)


def _get_config_sync(key, default=""):
    with _db_connect() as conn:
        row = conn.execute("SELECT value FROM shop_config WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def _set_config_sync(key, value):
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO shop_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def _add_product_sync(name, price, stock, rarity, rarity_percent, description, image_url):
    with _db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO products (name, price, stock, rarity, rarity_percent, description, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, price, stock, rarity, rarity_percent, description, image_url),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else None


def _get_product_sync(product_id):
    with _db_connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return dict(row) if row else None


def _get_product_by_name_sync(name):
    with _db_connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None


def _list_products_sync(rarity=None):
    with _db_connect() as conn:
        if rarity:
            rows = conn.execute(
                "SELECT * FROM products WHERE rarity = ? ORDER BY rarity_percent ASC, id ASC", (rarity,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products ORDER BY rarity_percent ASC, id ASC").fetchall()
        return [dict(r) for r in rows]


def _update_product_sync(product_id, fields):
    with _db_connect() as conn:
        if fields:
            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [product_id]
            conn.execute(f"UPDATE products SET {cols}, updated_at = datetime('now') WHERE id = ?", vals)
            conn.commit()
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        return dict(row) if row else None


def _delete_product_sync(product_id):
    with _db_connect() as conn:
        cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return cur.rowcount > 0


def _log_history_sync(product_id, product_name, event_type, old_stock, new_stock, rarity, rarity_percent, actor_id):
    with _db_connect() as conn:
        conn.execute(
            """
            INSERT INTO restock_history
                (product_id, product_name, event_type, old_stock, new_stock, delta, rarity, rarity_percent, actor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, product_name, event_type, old_stock, new_stock, new_stock - old_stock, rarity, rarity_percent, actor_id),
        )
        conn.commit()


def _get_history_sync(product_id=None, limit=15):
    with _db_connect() as conn:
        if product_id is not None:
            rows = conn.execute(
                "SELECT * FROM restock_history WHERE product_id = ? ORDER BY id DESC LIMIT ?", (product_id, limit)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM restock_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def _build_restock_embed(product, event_type, old_stock, new_stock):
    meta = RARITIES[product["rarity"]]
    is_special = product["rarity"] == "special"

    if is_special and event_type != "OUT_OF_STOCK":
        title = f"💠 SPECIAL RESTOCK — {product['name']}"
        color = 0x111216
        prefix = "⚡ **KELANGKAAN TERTINGGI (0.01%)** ⚡\n"
    else:
        title = f"{meta['emoji']} {EVENT_TITLES.get(event_type, '📦 UPDATE STOK')} — {product['name']}"
        color = 0x8E44AD if event_type == "OUT_OF_STOCK" else meta["color"]
        prefix = ""

    desc = prefix + (product["description"] or "_Tidak ada deskripsi._")
    embed = discord.Embed(
        title=title,
        description=desc,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc),
    )
    embed.add_field(name="Rarity", value=f"{meta['emoji']} **{meta['label']}** (`{meta['percent']}%`)", inline=True)
    embed.add_field(name="Harga", value=f"🪙 **{product['price']:,} Koin**", inline=True)
    embed.add_field(name="Stok", value=f"`{old_stock}` → **{new_stock}** unit", inline=True)
    if product.get("image_url"):
        embed.set_image(url=product["image_url"])
    embed.set_footer(text="SHOP RESTOCK SYSTEM")
    return embed


async def _send_restock_webhook(product, event_type, old_stock, new_stock):
    enabled = await _db_run(_get_config_sync, "webhook_enabled", "1")
    if enabled != "1":
        return False

    sig = (product["id"], event_type, old_stock, new_stock)
    now = time.monotonic()
    async with _DEDUP_LOCK:
        if now - _LAST_NOTIFIED.get(sig, 0) < 3.0:
            return False
        _LAST_NOTIFIED[sig] = now

    is_special = product["rarity"] == "special"
    special_url = os.environ.get("WEBHOOK_SPECIAL_URL", "").strip()
    premium_url = os.environ.get("WEBHOOK_PREMIUM_URL", "").strip()
    url = special_url if (is_special and special_url) else (premium_url or special_url)
    if not url:
        return False

    embed = _build_restock_embed(product, event_type, old_stock, new_stock)
    content = "@here 💠 **SPECIAL RESTOCK TELAH TIBA!**" if (is_special and event_type != "OUT_OF_STOCK") else None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            wh = discord.Webhook.from_url(url, session=session)
            await wh.send(content=content, embed=embed, username="SHOP RESTOCK SYSTEM")
        return True
    except Exception as exc:
        log("restock webhook error:", type(exc).__name__)
        return False


def _is_admin(member):
    return member.id == OWNER_ID or _has_mod_perm(member, "administrator")


def _valid_image_url(url):
    return not url or url.startswith(("https://", "http://"))


@tree.command(name="product_add", description="Tambah produk baru ke Shop Restock System (Admin)", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.choices(rarity=RARITY_CHOICES)
@discord.app_commands.describe(
    nama="Nama produk",
    harga="Harga dalam Koin",
    stok="Jumlah stok awal",
    rarity="Tingkat kelangkaan produk",
    deskripsi="Deskripsi produk (opsional)",
    gambar_url="URL gambar produk (opsional)",
)
async def slash_product_add(
    interaction: discord.Interaction,
    nama: str,
    harga: discord.app_commands.Range[int, 0, 1_000_000_000],
    stok: discord.app_commands.Range[int, 0, 1_000_000],
    rarity: discord.app_commands.Choice[str],
    deskripsi: str = "",
    gambar_url: str = "",
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    nama = nama.strip()
    gambar_url = gambar_url.strip()
    if not nama or not _valid_image_url(gambar_url):
        await interaction.response.send_message("❌ Nama wajib diisi dan URL gambar harus `http://` atau `https://`.", ephemeral=True)
        return
    if await _db_run(_get_product_by_name_sync, nama):
        await interaction.response.send_message(f"❌ Produk **{nama}** sudah ada.", ephemeral=True)
        return
    meta = RARITIES[rarity.value]
    prod = await _db_run(_add_product_sync, nama, harga, stok, rarity.value, meta["percent"], deskripsi.strip(), gambar_url)
    await _db_run(_log_history_sync, prod["id"], prod["name"], "NEW_PRODUCT", 0, stok, rarity.value, meta["percent"], str(interaction.user.id))
    sent = await _send_restock_webhook(prod, "NEW_PRODUCT", 0, stok)
    await interaction.response.send_message(
        f"✅ Produk **#{prod['id']} {prod['name']}** ({meta['emoji']} {meta['label']} `{meta['percent']}%`) ditambahkan. Webhook: `{'Terkirim' if sent else 'Belum diset / Nonaktif'}`.",
        ephemeral=True,
    )


@tree.command(name="product_edit", description="Edit produk di Shop Restock System (Admin)", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.choices(rarity=RARITY_CHOICES)
async def slash_product_edit(
    interaction: discord.Interaction,
    product_id: int,
    nama: str = None,
    harga: discord.app_commands.Range[int, 0, 1_000_000_000] = None,
    rarity: discord.app_commands.Choice[str] = None,
    deskripsi: str = None,
    gambar_url: str = None,
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    if not await _db_run(_get_product_sync, product_id):
        await interaction.response.send_message("❌ Product ID tidak ditemukan.", ephemeral=True)
        return
    fields = {}
    if nama is not None and nama.strip():
        fields["name"] = nama.strip()
    if harga is not None:
        fields["price"] = harga
    if rarity is not None:
        fields["rarity"] = rarity.value
        fields["rarity_percent"] = RARITIES[rarity.value]["percent"]
    if deskripsi is not None:
        fields["description"] = deskripsi.strip()
    if gambar_url is not None:
        g = gambar_url.strip()
        if not _valid_image_url(g):
            await interaction.response.send_message("❌ URL gambar tidak valid.", ephemeral=True)
            return
        fields["image_url"] = g
    updated = await _db_run(_update_product_sync, product_id, fields)
    await interaction.response.send_message(f"✅ Produk **#{updated['id']} {updated['name']}** diperbarui.", ephemeral=True)


@tree.command(name="product_delete", description="Hapus produk dari database (Admin)", guild=discord.Object(id=GUILD_ID))
async def slash_product_delete(interaction: discord.Interaction, product_id: int):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    prod = await _db_run(_get_product_sync, product_id)
    if not prod:
        await interaction.response.send_message("❌ Product ID tidak ditemukan.", ephemeral=True)
        return
    await _db_run(_delete_product_sync, product_id)
    await interaction.response.send_message(f"🗑️ Produk **#{product_id} {prod['name']}** dihapus.", ephemeral=True)


@tree.command(name="restock", description="Tambah stok produk dan kirim webhook otomatis (Admin)", guild=discord.Object(id=GUILD_ID))
async def slash_restock(
    interaction: discord.Interaction,
    product_id: int,
    jumlah: discord.app_commands.Range[int, 1, 1_000_000],
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    prod = await _db_run(_get_product_sync, product_id)
    if not prod:
        await interaction.response.send_message("❌ Product ID tidak ditemukan.", ephemeral=True)
        return
    old_stock = prod["stock"]
    new_stock = old_stock + jumlah
    event_type = "BACK_IN_STOCK" if old_stock == 0 else "RESTOCK"
    updated = await _db_run(_update_product_sync, product_id, {"stock": new_stock})
    await _db_run(_log_history_sync, product_id, updated["name"], event_type, old_stock, new_stock, updated["rarity"], updated["rarity_percent"], str(interaction.user.id))
    sent = await _send_restock_webhook(updated, event_type, old_stock, new_stock)
    await interaction.response.send_message(
        f"📦 **{updated['name']}** direstock: `{old_stock}` → **{new_stock}**. Webhook: `{'Terkirim' if sent else 'Belum diset / Nonaktif'}`.",
        ephemeral=True,
    )


@tree.command(name="stock_set", description="Setel jumlah stok produk secara langsung (Admin)", guild=discord.Object(id=GUILD_ID))
async def slash_stock_set(
    interaction: discord.Interaction,
    product_id: int,
    stok_baru: discord.app_commands.Range[int, 0, 1_000_000],
):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    prod = await _db_run(_get_product_sync, product_id)
    if not prod:
        await interaction.response.send_message("❌ Product ID tidak ditemukan.", ephemeral=True)
        return
    old_stock = prod["stock"]
    if stok_baru == old_stock:
        await interaction.response.send_message("ℹ️ Stok tidak berubah, notifikasi tidak dikirim.", ephemeral=True)
        return
    if stok_baru == 0:
        event_type = "OUT_OF_STOCK"
    elif old_stock == 0:
        event_type = "BACK_IN_STOCK"
    elif stok_baru > old_stock:
        event_type = "RESTOCK"
    else:
        event_type = "ADJUST"
    updated = await _db_run(_update_product_sync, product_id, {"stock": stok_baru})
    await _db_run(_log_history_sync, product_id, updated["name"], event_type, old_stock, stok_baru, updated["rarity"], updated["rarity_percent"], str(interaction.user.id))
    sent = False
    if event_type in ("OUT_OF_STOCK", "BACK_IN_STOCK", "RESTOCK"):
        sent = await _send_restock_webhook(updated, event_type, old_stock, stok_baru)
    await interaction.response.send_message(
        f"🔧 Stok **{updated['name']}** diubah: `{old_stock}` → **{stok_baru}**. Webhook: `{'Terkirim' if sent else 'Tidak dikirim'}`.",
        ephemeral=True,
    )


@tree.command(name="shop_list", description="Lihat daftar produk di database Shop Restock (Admin)", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.choices(rarity=RARITY_CHOICES)
async def slash_shop_list(interaction: discord.Interaction, rarity: discord.app_commands.Choice[str] = None):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    rows = await _db_run(_list_products_sync, rarity.value if rarity else None)
    if not rows:
        await interaction.response.send_message("📭 Belum ada produk di database.", ephemeral=True)
        return
    lines = [
        f"`#{r['id']}` {RARITIES[r['rarity']]['emoji']} **{r['name']}** (`{r['rarity_percent']}%`) — 🪙 `{r['price']:,}` • Stok: **{r['stock']}**"
        for r in rows[:25]
    ]
    e = discord.Embed(title="🛒 ╎ Daftar Produk Restock", description="\n".join(lines), colour=0xD4AF37)
    e.set_footer(text="SHOP RESTOCK SYSTEM")
    await interaction.response.send_message(embed=e, ephemeral=True)


@tree.command(name="stock_history", description="Lihat riwayat restock produk (Admin)", guild=discord.Object(id=GUILD_ID))
async def slash_stock_history(interaction: discord.Interaction, product_id: int = None):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    rows = await _db_run(_get_history_sync, product_id, 15)
    if not rows:
        await interaction.response.send_message("📭 Belum ada riwayat restock.", ephemeral=True)
        return
    lines = [
        f"`{r['created_at']}` • **{r['product_name']}** [{r['event_type']}] `{r['old_stock']} → {r['new_stock']}`"
        for r in rows
    ]
    e = discord.Embed(title="📜 ╎ Riwayat Restock", description="\n".join(lines), colour=0x8E44AD)
    e.set_footer(text="SHOP RESTOCK SYSTEM")
    await interaction.response.send_message(embed=e, ephemeral=True)


@tree.command(name="shop_config", description="Aktifkan atau matikan webhook restock otomatis (Admin)", guild=discord.Object(id=GUILD_ID))
async def slash_shop_config(interaction: discord.Interaction, webhook_aktif: bool):
    if not _is_admin(interaction.user):
        await interaction.response.send_message("❌ Khusus Administrator.", ephemeral=True)
        return
    await _db_run(_set_config_sync, "webhook_enabled", "1" if webhook_aktif else "0")
    status = "AKTIF" if webhook_aktif else "NONAKTIF"
    await interaction.response.send_message(f"⚙️ Webhook SHOP RESTOCK SYSTEM sekarang **{status}**.", ephemeral=True)
