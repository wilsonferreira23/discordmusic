import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import logging
from discord.ui import Button, View
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# =========================
# CONFIGURAÇÃO GERAL
# =========================

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("music_bot")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

COOKIE_FILE = "cookies.txt"

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN não foi encontrado nas variáveis de ambiente.")

# =========================
# SPOTIFY
# =========================

sp = None

if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    try:
        sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
            )
        )
        logger.info("Spotify conectado com sucesso.")
    except Exception:
        logger.exception("Não foi possível conectar ao Spotify.")
else:
    logger.info("Spotify não configurado. Links do Spotify não serão processados.")


# =========================
# YT-DLP
# =========================

def get_ydl_opts():
    logger.info("Preparando opções do yt-dlp.")
    opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "outtmpl": "%(id)s.%(ext)s",
        "quiet": False,
        "no_warnings": False,
        "nocheckcertificate": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
        "concurrent_fragment_downloads": 1,
        "js_runtimes": {
            "deno": {},
        },
        "remote_components": {"ejs:github"},

        # Ajuda a parecer uma requisição normal de navegador.
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    }

    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
        logger.info("Usando cookies.txt no yt-dlp.")
    else:
        logger.warning("cookies.txt não encontrado. Rodando sem cookies.")

    return opts

# =========================
# ESTADOS
# =========================

queues = {}
server_states = {}
preloads = {}

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


def get_state(guild_id: int):
    if guild_id not in server_states:
        logger.debug("Criando estado do servidor %s.", guild_id)
        server_states[guild_id] = {
            "last_msg": None,
        }
    return server_states[guild_id]


def ensure_queue(guild_id: int):
    if guild_id not in queues:
        logger.debug("Criando fila do servidor %s.", guild_id)
        queues[guild_id] = []


# =========================
# UI
# =========================

def create_controls():
    view = View(timeout=None)

    view.add_item(
        Button(
            style=discord.ButtonStyle.primary,
            emoji="⏯️",
            custom_id="pause",
        )
    )

    view.add_item(
        Button(
            style=discord.ButtonStyle.secondary,
            emoji="⏭️",
            custom_id="skip",
        )
    )

    view.add_item(
        Button(
            style=discord.ButtonStyle.danger,
            emoji="⏹️",
            custom_id="stop",
        )
    )

    return view


async def safe_delete_message(message):
    if not message:
        return

    try:
        await message.delete()
        logger.debug("Mensagem %s removida.", getattr(message, "id", "desconhecida"))
    except Exception:
        logger.debug(
            "Não foi possível remover a mensagem %s.",
            getattr(message, "id", "desconhecida"),
            exc_info=True,
        )


async def update_player_message(ctx, data, new=False):
    state = get_state(ctx.guild.id)
    view = create_controls()

    duration = data.get("duration") or 0
    mins, secs = divmod(int(duration), 60)
    duration_str = f"{mins:02d}:{secs:02d}"

    title = data.get("title") or "Música desconhecida"
    url = data.get("webpage_url") or data.get("original_url") or ""

    embed = discord.Embed(color=0x1DB954)
    embed.set_author(
        name="Tocando agora",
        icon_url="https://i.imgur.com/7R8gM2W.png",
    )

    if url:
        embed.description = (
            f"**[{title}]({url})** `[{duration_str}]`\n\n"
            f"Use os botões abaixo para controlar a música."
        )
    else:
        embed.description = (
            f"**{title}** `[{duration_str}]`\n\n"
            f"Use os botões abaixo para controlar a música."
        )

    thumbnail = data.get("thumbnail")
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if new and state["last_msg"]:
        await safe_delete_message(state["last_msg"])
        state["last_msg"] = None

    msg = await ctx.send(embed=embed, view=view)
    state["last_msg"] = msg
    logger.info("Mensagem do player atualizada no servidor %s.", ctx.guild.id)


# =========================
# DOWNLOAD / PRELOAD
# =========================

def find_downloaded_filename(data):
    video_id = data.get("id")
    ext = data.get("ext")

    if video_id and ext:
        expected = f"{video_id}.{ext}"
        if os.path.exists(expected):
            return expected

    if video_id:
        for file in os.listdir("."):
            if file.startswith(video_id + "."):
                return file

    requested = data.get("_filename")
    if requested and os.path.exists(requested):
        return requested

    return None


async def download_track(query: str):
    """
    Baixa uma música e retorna os dados com filename.
    """

    logger.info("Iniciando download para: %s", query)
    loop = asyncio.get_running_loop()

    def run_download():
        ydl_opts = get_ydl_opts()

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(query, download=True)

    try:
        data = await loop.run_in_executor(None, run_download)

        if not data:
            raise RuntimeError("yt-dlp não retornou dados.")

        if "entries" in data:
            entries = data.get("entries") or []
            if not entries:
                raise RuntimeError("Nenhum resultado encontrado.")
            data = entries[0]

        filename = find_downloaded_filename(data)

        if not filename:
            raise RuntimeError("Arquivo de áudio não foi encontrado após o download.")

        data["filename"] = filename
        logger.info("Download concluído: %s", filename)
        return data

    except Exception:
        logger.exception("Erro no download para: %s", query)
        return None


async def preload_next_song(guild_id: int):
    """
    Pré-baixa a próxima música da fila.
    """

    ensure_queue(guild_id)

    if guild_id in preloads:
        logger.debug("Servidor %s já possui pré-download.", guild_id)
        return

    if not queues[guild_id]:
        return

    next_query = queues[guild_id][0]
    logger.info(f"Pré-baixando próxima música: {next_query}")

    data = await download_track(next_query)

    if data:
        preloads[guild_id] = data
        logger.info("Pré-download concluído.")
    else:
        logger.warning("Falha no pré-download.")


def check_queue(ctx):
    guild_id = ctx.guild.id
    ensure_queue(guild_id)

    if guild_id in preloads:
        logger.info("Usando pré-download no servidor %s.", guild_id)
        data = preloads.pop(guild_id)

        if queues[guild_id]:
            queues[guild_id].pop(0)

        asyncio.run_coroutine_threadsafe(play_song_file(ctx, data), bot.loop)
        return

    if queues[guild_id]:
        query = queues[guild_id].pop(0)
        logger.info("Baixando próxima faixa no servidor %s: %s", guild_id, query)
        asyncio.run_coroutine_threadsafe(play_song_fresh(ctx, query), bot.loop)
        return

    logger.info("Fila encerrada no servidor %s.", guild_id)

    state = get_state(guild_id)

    if state["last_msg"]:
        asyncio.run_coroutine_threadsafe(
            safe_delete_message(state["last_msg"]),
            bot.loop,
        )
        state["last_msg"] = None


async def play_song_fresh(ctx, query: str):
    logger.info("Tocando faixa nova no servidor %s: %s", ctx.guild.id, query)
    data = await download_track(query)

    if data:
        await play_song_file(ctx, data)
    else:
        await ctx.send("❌ Não consegui baixar essa música. Pulando...", delete_after=8)
        check_queue(ctx)


def cleanup_file(filename: str):
    if not filename:
        return

    try:
        if os.path.exists(filename):
            os.remove(filename)
            logger.info("Arquivo temporário removido: %s", filename)
    except Exception as e:
        logger.warning("Não foi possível apagar arquivo %s: %s", filename, e)


async def play_song_file(ctx, data):
    voice_client = ctx.voice_client

    if not voice_client:
        logger.warning("Sem cliente de voz no servidor %s.", ctx.guild.id)
        return

    filename = data.get("filename")

    if not filename or not os.path.exists(filename):
        logger.error("Arquivo de áudio ausente no servidor %s: %s", ctx.guild.id, filename)
        await ctx.send("❌ Arquivo de áudio não encontrado. Pulando...", delete_after=8)
        check_queue(ctx)
        return

    try:
        ffmpeg_options = {
            "before_options": "-nostdin",
            "options": "-vn",
        }

        source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)

        def after_play(error):
            if error:
                logger.error("Erro no player do servidor %s: %s", ctx.guild.id, error)
            else:
                logger.info("Reprodução concluída no servidor %s.", ctx.guild.id)

            cleanup_file(filename)
            check_queue(ctx)

        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()

        voice_client.play(source, after=after_play)
        logger.info("Reprodução iniciada no servidor %s: %s", ctx.guild.id, filename)

        await update_player_message(ctx, data, new=True)

        asyncio.create_task(preload_next_song(ctx.guild.id))

    except Exception:
        logger.exception("Erro ao tocar arquivo no servidor %s.", ctx.guild.id)
        cleanup_file(filename)
        check_queue(ctx)


# =========================
# SPOTIFY HELPERS
# =========================

def spotify_track_to_query(track):
    name = track["name"]
    artist = track["artists"][0]["name"]
    return f"ytsearch:{name} {artist} audio"


async def add_spotify_to_queue(ctx, query: str):
    guild_id = ctx.guild.id
    ensure_queue(guild_id)

    if not sp:
        await ctx.send("❌ Spotify não está configurado nesse bot.", delete_after=8)
        return 0

    msg = await ctx.send("⏳ Processando Spotify...")

    added_count = 0

    try:
        clean_url = query.split("?")[0]
        logger.info("Processando link Spotify no servidor %s: %s", guild_id, clean_url)

        if "track" in clean_url:
            track_id = clean_url.split("track/")[-1].split("/")[0]
            track = sp.track(track_id)

            queues[guild_id].append(spotify_track_to_query(track))
            added_count = 1
            logger.info("Faixa Spotify adicionada no servidor %s.", guild_id)

        elif "playlist" in clean_url:
            playlist_id = clean_url.split("playlist/")[-1].split("/")[0]

            offset = 0
            limit = 100

            while True:
                result = sp.playlist_tracks(
                    playlist_id,
                    limit=limit,
                    offset=offset,
                )

                items = result.get("items") or []

                for item in items:
                    track = item.get("track")
                    if track:
                        queues[guild_id].append(spotify_track_to_query(track))
                        added_count += 1

                if not result.get("next"):
                    break

                offset += limit

            logger.info(
                "Playlist Spotify adicionada no servidor %s: %s faixas.",
                guild_id,
                added_count,
            )

        elif "album" in clean_url:
            album_id = clean_url.split("album/")[-1].split("/")[0]

            offset = 0
            limit = 50

            while True:
                result = sp.album_tracks(
                    album_id,
                    limit=limit,
                    offset=offset,
                )

                items = result.get("items") or []

                for track in items:
                    queues[guild_id].append(spotify_track_to_query(track))
                    added_count += 1

                if not result.get("next"):
                    break

                offset += limit

            logger.info(
                "Álbum Spotify adicionado no servidor %s: %s faixas.",
                guild_id,
                added_count,
            )

        else:
            await msg.edit(content="❌ Link do Spotify não reconhecido.", delete_after=8)
            return 0

        await msg.delete()
        return added_count

    except Exception:
        logger.exception("Erro ao processar Spotify no servidor %s.", guild_id)

        try:
            await msg.edit(content="❌ Erro ao processar link do Spotify.", delete_after=8)
        except Exception:
            logger.debug("Não foi possível atualizar a mensagem de erro do Spotify.", exc_info=True)

        return 0


# =========================
# COMANDOS
# =========================

@bot.command()
async def play(ctx, *, query: str):
    guild_id = ctx.guild.id
    ensure_queue(guild_id)

    voice_client = ctx.voice_client
    logger.info("Comando play no servidor %s: %s", guild_id, query)

    if not voice_client:
        if ctx.author.voice and ctx.author.voice.channel:
            logger.info("Conectando ao canal de voz no servidor %s.", guild_id)
            voice_client = await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("❌ Entre em um canal de voz primeiro.")

    try:
        await ctx.message.delete()
    except Exception:
        logger.debug("Não foi possível remover a mensagem do comando play.", exc_info=True)

    added_count = 0

    if "spotify.com" in query.lower():
        added_count = await add_spotify_to_queue(ctx, query)
    else:
        final_query = query if query.startswith("http") else f"ytsearch:{query}"
        queues[guild_id].append(final_query)
        added_count = 1
        logger.info("Faixa adicionada à fila do servidor %s.", guild_id)

    if added_count <= 0:
        return

    if voice_client and not voice_client.is_playing() and not voice_client.is_paused():
        check_queue(ctx)
    else:
        if added_count == 1:
            await ctx.send("✅ Música adicionada à fila.", delete_after=5)
        else:
            await ctx.send(f"✅ {added_count} músicas adicionadas à fila.", delete_after=8)


@bot.command()
async def skip(ctx):
    voice_client = ctx.voice_client

    if not voice_client:
        return await ctx.send("❌ Não estou em um canal de voz.", delete_after=6)

    if voice_client.is_playing() or voice_client.is_paused():
        logger.info("Comando skip no servidor %s.", ctx.guild.id)
        voice_client.stop()
        await ctx.send("⏭️ Música pulada.", delete_after=5)
    else:
        await ctx.send("❌ Não tem música tocando.", delete_after=6)


@bot.command()
async def pause(ctx):
    voice_client = ctx.voice_client

    if not voice_client:
        return await ctx.send("❌ Não estou em um canal de voz.", delete_after=6)

    if voice_client.is_playing():
        logger.info("Pausando reprodução no servidor %s.", ctx.guild.id)
        voice_client.pause()
        await ctx.send("⏸️ Pausado.", delete_after=5)
    elif voice_client.is_paused():
        logger.info("Retomando reprodução no servidor %s.", ctx.guild.id)
        voice_client.resume()
        await ctx.send("▶️ Retomado.", delete_after=5)
    else:
        await ctx.send("❌ Não tem música tocando.", delete_after=6)


@bot.command()
async def stop(ctx):
    logger.info("Comando stop no servidor %s.", ctx.guild.id)
    await stop_player(ctx.guild, ctx.voice_client)
    await ctx.send("⏹️ Fila limpa e bot desconectado.", delete_after=6)


@bot.command()
async def fila(ctx):
    guild_id = ctx.guild.id
    ensure_queue(guild_id)

    if not queues[guild_id]:
        return await ctx.send("📭 A fila está vazia.", delete_after=8)

    lines = []

    for index, item in enumerate(queues[guild_id][:10], start=1):
        lines.append(f"`{index}.` {item}")

    extra = len(queues[guild_id]) - 10

    description = "\n".join(lines)

    if extra > 0:
        description += f"\n\n+ {extra} músicas na fila."

    embed = discord.Embed(
        title="🎵 Fila",
        description=description,
        color=0x1DB954,
    )

    await ctx.send(embed=embed, delete_after=20)


# =========================
# BOTÕES
# =========================

async def stop_player(guild, voice_client):
    guild_id = guild.id
    ensure_queue(guild_id)

    queues[guild_id] = []
    logger.info("Limpando player do servidor %s.", guild_id)

    if guild_id in preloads:
        data_to_clean = preloads.pop(guild_id)
        cleanup_file(data_to_clean.get("filename"))

    if voice_client:
        try:
            if voice_client.is_playing() or voice_client.is_paused():
                voice_client.stop()
        except Exception:
            logger.exception("Erro ao interromper a reprodução no servidor %s.", guild_id)

        try:
            await voice_client.disconnect()
            logger.info("Desconectado do canal de voz no servidor %s.", guild_id)
        except Exception:
            logger.exception("Erro ao desconectar do canal de voz no servidor %s.", guild_id)

    state = get_state(guild_id)

    if state["last_msg"]:
        await safe_delete_message(state["last_msg"])
        state["last_msg"] = None


@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")
    voice_client = interaction.guild.voice_client
    logger.info("Interação %s no servidor %s.", custom_id, interaction.guild.id)

    await interaction.response.defer(ephemeral=True)

    if not voice_client:
        await interaction.followup.send("❌ Não estou em um canal de voz.", ephemeral=True)
        return

    if custom_id == "pause":
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.followup.send("⏸️ Pausado.", ephemeral=True)
        elif voice_client.is_paused():
            voice_client.resume()
            await interaction.followup.send("▶️ Retomado.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Não tem música tocando.", ephemeral=True)

    elif custom_id == "skip":
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
            await interaction.followup.send("⏭️ Pulando música.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Não tem música tocando.", ephemeral=True)

    elif custom_id == "stop":
        await stop_player(interaction.guild, voice_client)
        await interaction.followup.send(
            "⏹️ **Fila limpa e bot desconectado.**",
            ephemeral=True,
        )


# =========================
# EVENTOS
# =========================

@bot.event
async def on_ready():
    logger.info(f"Aura Music Online: {bot.user}")
    print(f"Aura Music Online: {bot.user}")


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return

    original = getattr(error, "original", error)
    logger.error(
        "Erro no comando no servidor %s.",
        getattr(ctx.guild, "id", "DM"),
        exc_info=(type(original), original, original.__traceback__),
    )


@bot.event
async def on_error(event, *args, **kwargs):
    logger.exception("Erro não tratado no evento Discord: %s", event)


# =========================
# START
# =========================

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
