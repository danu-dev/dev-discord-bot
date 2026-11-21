#!/usr/bin/env python3
"""DEV server Discord bot — modular entry point."""

from bot_modules.core import *
from bot_modules.ai_admin import (
    CHAT_HISTORY, _save_memory, server_summary, ai_plan, describe, PlanView, qa_embed, live_tests,
)
from bot_modules.economy_xp import (
    XP_DATA, _award_xp, _sync_ml_role, _grant_bonus_xp, ECONOMY_DATA, _add_coins,
    ShopView, INVITE_CACHE, _refresh_invites, _update_server_counters,
)
from bot_modules.community import (
    _help_embed, RolePickerView, SuggestionView, TicketLauncherView, TicketCloseView,
    TEMP_VOICE_HUB_ID, TEMP_VOICE_HUB_NAME, TEMP_VOICE_IDS, TEMP_VOICE_OWNERS,
    _is_temp_voice, TempVoiceView, GiveawayView, SNIPE_CACHE,
    _members_page, MembersView, _userinfo_embed, _do_timeout, _do_ban, _do_unban, _do_kick,
)
from bot_modules.music import (
    MUSIC_QUEUE, NOW_PLAYING, VOICE_LOGS, LofiView, NextMusicView,
)
from bot_modules.founder_panel import FounderManageView, _ensure_founder_panel


@client.event
async def on_member_remove(member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    await _send_modlog(member.guild, "📤 Member Keluar", f"{member.mention} (`{member.name}`)", RED)


@client.event
async def on_message_delete(msg):
    if not msg.guild or msg.guild.id != GUILD_ID or msg.author.bot:
        return
    SNIPE_CACHE[msg.channel.id] = {
        "author_name": f"{msg.author.display_name} (@{msg.author.name})",
        "author_avatar": msg.author.display_avatar.url,
        "content": (msg.content or "")[:2000],
        "attachments": [a.url for a in msg.attachments],
        "deleted_at": datetime.datetime.now(datetime.timezone.utc),
    }
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
    if after.channel and before.channel != after.channel and (
        after.channel.id == TEMP_VOICE_HUB_ID or after.channel.name == TEMP_VOICE_HUB_NAME
    ):
        cat = after.channel.category
        ow = dict(cat.overwrites) if cat else {}
        ow[member] = discord.PermissionOverwrite(connect=True, manage_channels=True, move_members=True)
        room = await member.guild.create_voice_channel(
            f"🔊 ╎ {member.display_name}", category=cat, overwrites=ow, reason="Join-to-Create Voice"
        )
        TEMP_VOICE_IDS.add(room.id)
        TEMP_VOICE_OWNERS[room.id] = member.id
        await asyncio.sleep(0.25)
        moved = False
        for attempt in range(2):
            try:
                await member.move_to(room)
                moved = True
                break
            except Exception as exc:
                log(f"temp voice move attempt {attempt + 1} failed:", exc)
                await asyncio.sleep(0.5)
        if moved:
            try:
                e = discord.Embed(
                    title="🔊 ╎ Kontrol Room Voice",
                    description=f"Room milik {member.mention}. Gunakan tombol di bawah untuk mengatur room kamu:",
                    colour=BLURPLE,
                )
                await room.send(embed=e, view=TempVoiceView())
            except Exception as exc:
                log("temp voice panel send failed:", exc)
        elif len(room.members) == 0:
            TEMP_VOICE_IDS.discard(room.id)
            TEMP_VOICE_OWNERS.pop(room.id, None)
            try:
                await room.delete(reason="Gagal memindahkan member ke temp voice")
            except Exception as exc:
                log("temp voice cleanup failed:", exc)
    if before.channel and before.channel != after.channel and _is_temp_voice(before.channel) and len(before.channel.members) == 0:
        TEMP_VOICE_IDS.discard(before.channel.id)
        TEMP_VOICE_OWNERS.pop(before.channel.id, None)
        try:
            await before.channel.delete(reason="Temp voice kosong")
        except Exception as exc:
            log("temp voice delete failed:", exc)
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
                await _update_server_counters(guild)
        except Exception as exc:
            log("bg loop error:", exc)
        await asyncio.sleep(360)


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
async def on_invite_create(invite):
    if invite.guild and invite.guild.id == GUILD_ID:
        INVITE_CACHE.setdefault(invite.guild.id, {})[invite.code] = (invite.uses or 0, invite.inviter.id if invite.inviter else None)


@client.event
async def on_invite_delete(invite):
    if invite.guild and invite.guild.id == GUILD_ID:
        INVITE_CACHE.get(invite.guild.id, {}).pop(invite.code, None)



@client.event
async def on_ready():
    client.add_view(RolePickerView())
    client.add_view(SuggestionView())
    client.add_view(TicketLauncherView())
    client.add_view(TicketCloseView())
    client.add_view(LofiView())
    client.add_view(NextMusicView())
    client.add_view(FounderManageView())
    client.add_view(GiveawayView())
    client.add_view(TempVoiceView())
    client.add_view(ShopView())
    if not getattr(client, "_bg_started", False):
        client._bg_started = True
        try:
            await tree.sync(guild=discord.Object(id=GUILD_ID))
        except Exception as exc:
            log("tree sync error:", exc)
        guild = client.get_guild(GUILD_ID)
        if guild:
            await _refresh_invites(guild)
            await _update_server_counters(guild)
        await _ensure_founder_panel()
        client.loop.create_task(background_qa_and_autorole())
    log(f"online sebagai {client.user} — melayani owner {OWNER_ID} di #commands")


@client.event
async def on_member_join(member):
    if member.guild.id != GUILD_ID or member.bot:
        return
    inviter_id = None
    try:
        old_cache = INVITE_CACHE.get(member.guild.id, {})
        invs = await member.guild.invites()
        new_cache = {}
        for inv in invs:
            uses = inv.uses or 0
            iid = inv.inviter.id if inv.inviter else None
            new_cache[inv.code] = (uses, iid)
            prev_uses, _ = old_cache.get(inv.code, (0, iid))
            if uses > prev_uses and iid and iid != member.id:
                inviter_id = iid
        INVITE_CACHE[member.guild.id] = new_cache
    except Exception:
        pass
    if inviter_id:
        ECONOMY_DATA["invites"][str(inviter_id)] = int(ECONOMY_DATA["invites"].get(str(inviter_id), 0)) + 1
        _add_coins(inviter_id, 100)
        inviter_member = member.guild.get_member(inviter_id)
        if inviter_member:
            await _grant_bonus_xp(member.guild, inviter_member, 50)
    role = member.guild.get_role(MEMBER_ROLE_ID)
    if role and role not in member.roles:
        try:
            await member.add_roles(role, reason="Auto-role Member saat join")
        except Exception:
            pass
    inv_txt = f"\nDiundang oleh: <@{inviter_id}> (+100 Koin, +50 XP)" if inviter_id else ""
    await _send_modlog(member.guild, "📥 Member Bergabung", f"{member.mention} (`{member.name}`){inv_txt}", GREEN)
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
    if not _has_mod_perm(msg.author, "manage_messages"):
        low_msg = msg.content.lower()
        has_invite = any(d in low_msg for d in ("discord.gg/", "discord.com/invite/", "discordapp.com/invite/"))
        too_many_mentions = len(msg.mentions) >= 6 or msg.mention_everyone
        if has_invite or too_many_mentions:
            reason_mod = "Link invite server lain" if has_invite else "Spam mention berlebihan"
            try:
                await msg.delete()
                await msg.channel.send(f"🛡️ {msg.author.mention}, pesan dihapus otomatis (**{reason_mod}**).", delete_after=5)
            except Exception:
                pass
            await _send_modlog(msg.guild, "🛡️ Auto-Mod", f"Pelaku: {msg.author.mention}\nChannel: {msg.channel.mention}\nAlasan: {reason_mod}", RED)
            return
    new_lvl = _award_xp(msg.author.id)
    if new_lvl is not None:
        rank_label, target_rid = _ml_rank_for_level(new_lvl)
        reward_txt = ""
        if target_rid:
            ml_ids = {rid for _, _, rid in ML_RANK_TIERS}
            old_roles = [r for r in msg.author.roles if r.id in ml_ids and r.id != target_rid]
            new_role = msg.guild.get_role(target_rid)
            try:
                if old_roles:
                    await msg.author.remove_roles(*old_roles, reason=f"Naik ke {rank_label}")
                if new_role and new_role not in msg.author.roles:
                    await msg.author.add_roles(new_role, reason=f"Reward naik Level {new_lvl}")
                    reward_txt = f"\n🏅 Rank baru terbuka: **{rank_label}**!"
                elif new_lvl >= 30:
                    reward_txt = f"\n⭐ Rank kamu sekarang: **{rank_label}**!"
            except Exception:
                pass
        try:
            await msg.channel.send(f"🎉 Selamat {msg.author.mention}, kamu naik ke **Level {new_lvl}**!{reward_txt}")
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
    if cmd_word in ("!help", "!bantuan"):
        await msg.reply(embed=_help_embed(msg.author), mention_author=False)
        return
    if cmd_word in ("!members", "!member", "!listmember"):
        e, total = _members_page(msg.guild, 0)
        await msg.reply(embed=e, view=MembersView(msg.guild, 0) if total > 1 else None, mention_author=False)
        return
    if cmd_word in ("!userinfo", "!ui"):
        target = msg.mentions[0] if msg.mentions else msg.author
        await msg.reply(embed=_userinfo_embed(target), mention_author=False)
        return
    if cmd_word == "!timeout":
        if not _has_mod_perm(msg.author, "moderate_members"):
            await msg.reply("❌ Kamu tidak punya izin timeout.", mention_author=False)
            return
        parts = text.split(maxsplit=3)
        tid = msg.mentions[0].id if msg.mentions else (_parse_target_id(parts[1]) if len(parts) > 1 else None)
        member = msg.guild.get_member(tid) if tid else None
        mins = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 10
        reason = parts[3] if len(parts) > 3 else "Tidak ada alasan"
        if not member:
            await msg.reply("Pakai: `!timeout @user [menit] [alasan]`", mention_author=False)
            return
        await msg.reply(await _do_timeout(msg.guild, msg.author, member, mins, reason), mention_author=False)
        return
    if cmd_word == "!purge":
        if not _has_mod_perm(msg.author, "manage_messages"):
            await msg.reply("❌ Kamu tidak punya izin hapus pesan.", mention_author=False)
            return
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].isdigit():
            await msg.reply("Pakai: `!purge <jumlah 1-100>`", mention_author=False)
            return
        count = max(1, min(100, int(parts[1])))
        deleted = await msg.channel.purge(limit=count + 1)
        await _send_modlog(msg.guild, "🧹 Purge Pesan", f"Channel: {msg.channel.mention}\nJumlah: `{len(deleted)}`\nOleh: {msg.author.mention}", BLURPLE)
        await msg.channel.send(f"🧹 Berhasil menghapus **{len(deleted) - 1}** pesan.", delete_after=4)
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
    mentioned_bot = client.user in msg.mentions if client.user else False
    if msg.author.id != OWNER_ID or (msg.channel.id != COMMANDS_CHANNEL and not mentioned_bot):
        return
    if mentioned_bot and client.user:
        text = text.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if not text:
        return
    guild = msg.guild
    low = text.lower()

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
        g = guild.get_channel(1573532365970481263) or msg.channel
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
    if not plan["actions"]:
        reply_txt = (plan["reply"] or "Siap, ada yang bisa saya bantu?").strip()
        if len(reply_txt) <= 1900:
            await msg.reply(reply_txt, mention_author=False)
        else:
            e = discord.Embed(description=reply_txt[:3500], colour=BLURPLE)
            e.set_footer(text=footer_ai)
            await msg.reply(embed=e, mention_author=False)
        return
    e = discord.Embed(
        title="📋 Rencana — tekan ✅ untuk menjalankan",
        description=plan["reply"][:3000] or "\u200b",
        colour=GOLD,
    )
    e.add_field(name=f"{len(plan['actions'])} aksi", value="\n".join(describe(a) for a in plan["actions"])[:1024],
                inline=False)
    e.set_footer(text=f"{footer_ai} • Berlaku 5 menit")
    await msg.reply(embed=e, view=PlanView(guild, plan["actions"]), mention_author=False)


if __name__ == "__main__":
    start_health_server()
    try:
        client.run(get_discord_token(), log_handler=None)
    except discord.PrivilegedIntentsRequired:
        log("⚠️ PRESENCE intent belum aktif di Developer Portal, mencoba jalan tanpa presences...")
        intents.presences = False
        client._connection._intents.presences = False
        try:
            client.run(get_discord_token(), log_handler=None)
        except discord.PrivilegedIntentsRequired:
            sys.exit("MESSAGE_CONTENT / MEMBERS intent belum aktif di Developer Portal")
