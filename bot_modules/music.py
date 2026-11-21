from bot_modules.core import *

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
        last_title = (NOW_PLAYING.get("track") or {}).get("title")
        NOW_PLAYING["track"] = None
        vc = guild.voice_client
        if text_channel and vc and vc.is_connected():
            desc = f"Lagu **{last_title}** sudah selesai.\nPilih musik selanjutnya dari dropdown di bawah, klik **🔎 Cari Lagu**, atau ketik `/play`:" if last_title else "Pilih musik selanjutnya dari dropdown di bawah:"
            e = discord.Embed(title="🎵 ╎ Musik Selanjutnya", description=desc, colour=0x9B59B6)
            await text_channel.send(embed=e, view=NextMusicView())


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
    e.set_footer(text=f"Diminta oleh {track.get('requester', 'member')} • /play <judul> | /skip | /stop")
    return e


def _lofi_embed(title, channel_name):
    e = discord.Embed(
        title="🎧 ╎ Lofi Radio 24/7",
        description=f"Sedang memutar **{title}** di **{channel_name}**\n"
                    f"Perintah: `/lofi` • `/skip` • `/play <judul>` • `/stop`",
        colour=0x9B59B6,
    )
    e.set_footer(text="Otomatis keluar jika voice kosong")
    return e


class LofiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Tambah Antrian", emoji="➕", style=discord.ButtonStyle.success, custom_id="lofi_add_queue")
    async def add_queue_btn(self, interaction: discord.Interaction, _b):
        await interaction.response.send_modal(PlaySongModal())

    @discord.ui.button(label="Antrian", emoji="🎶", style=discord.ButtonStyle.secondary, custom_id="lofi_show_queue")
    async def show_queue_btn(self, interaction: discord.Interaction, _b):
        cur = NOW_PLAYING.get("track")
        lines = [f"**Sedang diputar:** {cur['title']}" if cur else "**Sedang diputar:** tidak ada"]
        if MUSIC_QUEUE:
            for i, t in enumerate(MUSIC_QUEUE[:15], 1):
                lines.append(f"`{i}.` {t['title']}")
            if len(MUSIC_QUEUE) > 15:
                lines.append(f"_...dan {len(MUSIC_QUEUE) - 15} lagu lainnya_")
        else:
            lines.append("_Antrian kosong — klik **➕ Tambah Antrian** untuk menambah lagu._")
        await interaction.response.send_message(
            embed=discord.Embed(title="🎶 Antrian Lagu", description="\n".join(lines), colour=0x9B59B6),
            ephemeral=True,
        )

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


NEXT_MUSIC_PRESETS = [
    ("station:lofi", "☕ Lofi Beats Radio", "Radio lofi santai 24/7"),
    ("station:chillsynth", "🌃 Nightride Chillsynth", "Synthwave & chillwave"),
    ("station:groove", "🌿 SomaFM Groove Salad", "Ambient & downtempo"),
    ("station:lush", "🌙 SomaFM Lush", "Vocal chill & mellow"),
    ("search:lofi hip hop chill beats", "🎧 Lofi Hip Hop Mix", "Putar campuran lofi hip hop"),
    ("search:lagu pop indonesia hits", "🇮🇩 Lagu Indo Pop Hits", "Putar lagu pop Indonesia"),
    ("search:gaming phonk music mix", "🎮 Gaming Phonk / EDM", "Putar musik gaming"),
    ("search:piano relaxing study music", "🎹 Piano Santai", "Putar musik piano santai"),
]


class PlaySongModal(discord.ui.Modal, title="Putar / Tambah Antrian Lagu"):
    judul = discord.ui.TextInput(
        label="Judul Lagu / Link (1 per baris = antrian)",
        style=discord.TextStyle.paragraph,
        placeholder="Bisa tulis 1 lagu atau banyak lagu sekaligus:\nlofi hip hop\njudul lagu 2\njudul lagu 3",
        required=True,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        queries = [ln.strip() for ln in self.judul.value.splitlines() if ln.strip()][:10]
        if not queries:
            await interaction.followup.send("❌ Tulis minimal 1 judul lagu.", ephemeral=True)
            return
        try:
            vc = await _ensure_voice(interaction.guild, interaction.user, interaction.channel, interaction.followup.send)
            if not vc:
                return
            added = []
            started_now = None
            for q in queries:
                track = await asyncio.to_thread(_search_track, q)
                track["requester"] = interaction.user.display_name
                if vc.is_playing() or vc.is_paused() or started_now is not None:
                    MUSIC_QUEUE.append(track)
                    added.append(track["title"])
                else:
                    MUSIC_QUEUE.append(track)
                    _play_next_in_queue(interaction.guild, interaction.channel)
                    started_now = track
            if started_now and not added:
                await interaction.followup.send(embed=_music_embed(started_now, vc.channel.name), view=LofiView())
            elif started_now and added:
                q_txt = "\n".join(f"`{i}.` {t}" for i, t in enumerate(added, 1))
                await interaction.followup.send(
                    content=f"➕ **{len(added)} lagu** masuk antrian:\n{q_txt}",
                    embed=_music_embed(started_now, vc.channel.name),
                    view=LofiView(),
                )
            else:
                start_idx = len(MUSIC_QUEUE) - len(added) + 1
                q_txt = "\n".join(f"`{start_idx + i}.` {t}" for i, t in enumerate(added))
                await interaction.followup.send(f"➕ Ditambahkan ke antrian:\n{q_txt}")
        except Exception as exc:
            log("modal music error:", exc)
            await interaction.followup.send(f"❌ Gagal memutar audio: `{str(exc)[:180]}`")


class NextMusicSelect(discord.ui.Select):
    def __init__(self):
        opts = [
            discord.SelectOption(label=label, value=val, description=desc)
            for val, label, desc in NEXT_MUSIC_PRESETS
        ]
        super().__init__(
            placeholder="🎵 Pilih musik atau radio selanjutnya...",
            min_values=1,
            max_values=1,
            options=opts,
            custom_id="next_music_select",
        )

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        await interaction.response.defer()
        vc = await _ensure_voice(interaction.guild, interaction.user, interaction.channel, interaction.followup.send)
        if not vc:
            return
        if choice.startswith("station:"):
            key = choice.split(":", 1)[1]
            station_keys = {k: i for i, (k, _t, _u) in enumerate(LOFI_STATIONS)}
            idx = station_keys.get(key, 0)
            title = _play_station(vc, idx)
            await interaction.followup.send(embed=_lofi_embed(title, vc.channel.name), view=LofiView())
        else:
            query = choice.split(":", 1)[1]
            try:
                track = await asyncio.to_thread(_search_track, query)
                track["requester"] = interaction.user.display_name
                if vc.is_playing() or vc.is_paused():
                    MUSIC_QUEUE.append(track)
                    await interaction.followup.send(f"➕ Ditambahkan ke antrian (#{len(MUSIC_QUEUE)}): **{track['title']}**")
                else:
                    MUSIC_QUEUE.append(track)
                    _play_next_in_queue(interaction.guild, interaction.channel)
                    await interaction.followup.send(embed=_music_embed(track, vc.channel.name), view=LofiView())
            except Exception as exc:
                await interaction.followup.send(f"❌ Gagal memutar audio: `{str(exc)[:180]}`")


class NextMusicView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(NextMusicSelect())

    @discord.ui.button(label="Cari Lagu Lain", emoji="🔎", style=discord.ButtonStyle.primary, custom_id="next_music_search")
    async def search_btn(self, interaction: discord.Interaction, _b):
        await interaction.response.send_modal(PlaySongModal())

    @discord.ui.button(label="Stop", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="next_music_stop")
    async def stop_btn(self, interaction: discord.Interaction, _b):
        MUSIC_QUEUE.clear()
        NOW_PLAYING["track"] = None
        vc = interaction.guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()
        await interaction.response.edit_message(
            embed=discord.Embed(title="⏹️ ╎ Musik Dimatikan", colour=0x99AAB5), view=None
        )


async def _ensure_voice(guild, member, channel, reply_fn):
    if not member.voice or not member.voice.channel:
        await reply_fn("🎧 Masuk ke voice channel dulu!")
        return None
    voice_ch = member.voice.channel
    vc = guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != voice_ch.id:
            await vc.move_to(voice_ch)
            await _wait_dave_ready(vc)
    else:
        vc = await voice_ch.connect(timeout=30, self_deaf=True)
        await _wait_dave_ready(vc)
    me_voice = guild.me.voice if guild.me else None
    if me_voice and me_voice.suppress:
        try:
            await guild.me.edit(suppress=False)
        except Exception:
            pass
    if me_voice and me_voice.mute:
        await channel.send("⚠️ Bot sedang di-**Server Mute** di voice channel, unmute bot agar suaranya terdengar.")
    return vc


@tree.command(name="play", description="Putar lagu dari judul atau URL di voice channel", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(judul="Judul lagu atau link yang mau diputar")
async def slash_play(interaction: discord.Interaction, judul: str):
    await interaction.response.defer()
    try:
        vc = await _ensure_voice(interaction.guild, interaction.user, interaction.channel, interaction.followup.send)
        if not vc:
            return
        track = await asyncio.to_thread(_search_track, judul)
        track["requester"] = interaction.user.display_name
        if vc.is_playing() or vc.is_paused():
            MUSIC_QUEUE.append(track)
            await interaction.followup.send(f"➕ Ditambahkan ke antrian (#{len(MUSIC_QUEUE)}): **{track['title']}**")
        else:
            MUSIC_QUEUE.append(track)
            _play_next_in_queue(interaction.guild, interaction.channel)
            await interaction.followup.send(embed=_music_embed(track, vc.channel.name), view=LofiView())
    except Exception as exc:
        log("music error:", exc)
        await interaction.followup.send(f"❌ Gagal memutar audio: `{str(exc)[:180]}`")


@tree.command(name="lofi", description="Putar radio lofi 24/7 di voice channel", guild=discord.Object(id=GUILD_ID))
@discord.app_commands.describe(stasiun="Pilih stasiun lofi (opsional)")
@discord.app_commands.choices(stasiun=[
    discord.app_commands.Choice(name="☕ Lofi Beats Radio", value="lofi"),
    discord.app_commands.Choice(name="🌃 Nightride Chillsynth", value="chillsynth"),
    discord.app_commands.Choice(name="🌿 SomaFM Groove Salad", value="groove"),
    discord.app_commands.Choice(name="🌙 SomaFM Lush", value="lush"),
    discord.app_commands.Choice(name="📻 Lihat Daftar Stasiun", value="list"),
])
async def slash_lofi(interaction: discord.Interaction, stasiun: str = "lofi"):
    import shutil
    if stasiun == "list":
        lines = "\n".join(f"• `{k}` — {t}" for k, t, _ in LOFI_STATIONS)
        await interaction.response.send_message(embed=discord.Embed(title="📻 Daftar Stasiun Lofi", description=lines, colour=0x9B59B6))
        return
    await interaction.response.defer()
    try:
        vc = await _ensure_voice(interaction.guild, interaction.user, interaction.channel, interaction.followup.send)
        if not vc:
            return
        station_keys = {k: i for i, (k, _t, _u) in enumerate(LOFI_STATIONS)}
        idx = station_keys.get(stasiun, LOFI_STATE["idx"])
        title = _play_station(vc, idx)
        await interaction.followup.send(embed=_lofi_embed(title, vc.channel.name), view=LofiView())
        await asyncio.sleep(2.5)
        if vc.is_connected() and not vc.is_playing():
            await interaction.channel.send(
                f"⚠️ Stream `{title}` langsung berhenti (`opus={discord.opus.is_loaded()}`, `ffmpeg={bool(shutil.which('ffmpeg'))}`). "
                f"Coba `/skip` untuk ganti stasiun."
            )
    except Exception as exc:
        log("lofi error:", exc)
        await interaction.followup.send(f"❌ Gagal memutar lofi: `{str(exc)[:180]}`")


@tree.command(name="skip", description="Skip lagu saat ini atau ganti stasiun lofi", guild=discord.Object(id=GUILD_ID))
async def slash_skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await interaction.response.send_message("🔇 Bot tidak sedang di voice.", ephemeral=True)
        return
    if MUSIC_QUEUE:
        vc.stop()
        await interaction.response.send_message("⏭️ Skip ke lagu berikutnya.")
    else:
        title = _play_station(vc, LOFI_STATE["idx"] + 1)
        await interaction.response.send_message(embed=_lofi_embed(title, vc.channel.name), view=LofiView())


@tree.command(name="queue", description="Lihat daftar antrian lagu saat ini", guild=discord.Object(id=GUILD_ID))
async def slash_queue(interaction: discord.Interaction):
    cur = NOW_PLAYING.get("track")
    lines = [f"**Sedang diputar:** {cur['title']}" if cur else "**Sedang diputar:** tidak ada"]
    for i, t in enumerate(MUSIC_QUEUE[:10], 1):
        lines.append(f"`{i}.` {t['title']}")
    await interaction.response.send_message(embed=discord.Embed(title="🎶 Antrian Lagu", description="\n".join(lines), colour=0x9B59B6))


@tree.command(name="stop", description="Matikan musik dan keluarkan bot dari voice channel", guild=discord.Object(id=GUILD_ID))
async def slash_stop(interaction: discord.Interaction):
    MUSIC_QUEUE.clear()
    NOW_PLAYING["track"] = None
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("🔇 Bot tidak sedang di voice channel.", ephemeral=True)
        return
    vc.stop()
    await vc.disconnect()
    await interaction.response.send_message("⏹️ Musik dimatikan & keluar dari voice.")


