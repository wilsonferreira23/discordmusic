import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from discord.ui import Button, View
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import logging
from collections import defaultdict

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()] # Apenas StreamHandler é melhor para logs do Railway
)
logger = logging.getLogger('music_bot')

# --- CONFIGURAÇÃO DE CREDENCIAIS (VIA VARIÁVEIS DE AMBIENTE) ---
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

if not all([SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, DISCORD_TOKEN]):
    logger.error("ERRO: Variáveis de ambiente (SPOTIFY ou DISCORD) não configuradas!")
    # O bot continuará, mas comandos Spotify falharão. Idealmente, configure no Railway.

sp = None
try:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ))
except Exception as e:
    logger.error(f"Erro ao configurar Spotify: {e}")

# --- CONFIGURAÇÃO DO BOT ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# No Railway/Linux moderno, o discord.py geralmente encontra o libopus do sistema sozinho.
# Se der erro, o Railway precisa instalar o pacote 'libopus-dev' ou 'libopus0'.

# --- CONFIGURAÇÕES DE ÁUDIO (STREAMING) ---
# Opções otimizadas para NÃO baixar o arquivo e rodar leve
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'source_address': '0.0.0.0', # Evita problemas de IPv6
}

# Se o arquivo cookies.txt existir (subido no repo), usa ele. Se não, ignora.
if os.path.exists('cookies.txt'):
    ydl_opts['cookiefile'] = 'cookies.txt'

# Opções críticas para o FFMPEG não cair a conexão do stream
ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Variáveis Globais de Estado
queues = {}
now_playing_messages = {}
queue_tasks = {}
server_states = {}

# Controle de batch Spotify
playlist_offsets = defaultdict(int)
playlist_totals = defaultdict(int)
playlist_ids = {}

def get_server_state(guild_id):
    if guild_id not in server_states:
        server_states[guild_id] = {
            'loop': False,
            'shuffle': False,
            'autoplay': False,
            'current_song': None,
            'current_url': None,
            'duration': None,
            'skip_requested': False,
            'processing_playlist': False
        }
    return server_states[guild_id]

async def create_now_playing_embed(title, url, duration=None, loop_enabled=False, autoplay_enabled=False):
    embed = discord.Embed(
        title="Tocando Agora",
        description=f"[{title}]({url})" + (f" - [`{duration}`]" if duration else ""),
        color=0x1DB954 # Verde Spotify/Bot
    )
    modes_info = []
    if loop_enabled: modes_info.append("🔁 Loop: Ativado")
    if autoplay_enabled: modes_info.append("📻 Autoplay: Ativado")
    
    if modes_info:
        embed.add_field(name="Modos", value="\n".join(modes_info), inline=False)
    
    embed.set_footer(text="Use os botões abaixo para controlar a música.")
    return embed

def create_music_controls(loop_enabled=False, autoplay_enabled=False):
    view = View(timeout=None)
    view.add_item(Button(style=discord.ButtonStyle.blurple, emoji="⏯️", custom_id="pause_resume"))
    view.add_item(Button(style=discord.ButtonStyle.blurple, emoji="⏭️", custom_id="skip"))
    view.add_item(Button(style=discord.ButtonStyle.green if loop_enabled else discord.ButtonStyle.gray, emoji="🔁", custom_id="loop"))
    view.add_item(Button(style=discord.ButtonStyle.green if autoplay_enabled else discord.ButtonStyle.gray, emoji="📻", custom_id="autoplay"))
    view.add_item(Button(style=discord.ButtonStyle.red, emoji="⏹️", custom_id="stop"))
    return view

async def update_now_playing(ctx, title, url, duration=None, loop_enabled=False, autoplay_enabled=False):
    guild_id = ctx.guild.id
    embed = await create_now_playing_embed(title, url, duration, loop_enabled, autoplay_enabled)
    view = create_music_controls(loop_enabled, autoplay_enabled)
    
    if guild_id in now_playing_messages and now_playing_messages[guild_id]:
        try:
            await now_playing_messages[guild_id].edit(embed=embed, view=view)
        except:
            now_playing_messages[guild_id] = await ctx.send(embed=embed, view=view)
    else:
        now_playing_messages[guild_id] = await ctx.send(embed=embed, view=view)

def cancel_queue_task(guild_id):
    if guild_id in queue_tasks and queue_tasks[guild_id]:
        task = queue_tasks[guild_id]
        if not task.done() and not task.cancelled():
            task.cancel()
        queue_tasks[guild_id] = None

# --- LÓGICA PRINCIPAL DE PLAYBACK (STREAMING) ---
async def play_song(ctx, url):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    guild_id = ctx.guild.id
    state = get_server_state(guild_id)
    state['skip_requested'] = False

    if not voice_client:
        if ctx.author.voice:
            voice_client = await ctx.author.voice.channel.connect()
        else:
            await ctx.send("Entre em um canal de voz primeiro!")
            return

    try:
        # Extração de info SEM DOWNLOAD (roda em thread separada para não travar o bot)
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: youtube_dl.YoutubeDL(ydl_opts).extract_info(url, download=False))

        if 'entries' in data:
            data = data['entries'][0]

        # URL real do stream de áudio
        stream_url = data['url']
        title = data.get('title', 'Desconhecido')
        
        # Formata duração
        duration_seconds = data.get('duration', 0)
        minutes, seconds = divmod(int(duration_seconds), 60)
        formatted_duration = f"{minutes:02d}:{seconds:02d}"

        # Cria o player FFMPEG conectado direto na URL
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_opts)
        
        def _after_play(error):
            if error: logger.error(f"Erro no player: {error}")
            # A checagem de fila é feita pelo loop externo check_queue, 
            # então não chamamos play_next aqui para não duplicar

        if voice_client.is_playing():
            voice_client.stop()
            
        voice_client.play(source, after=_after_play)

        state['current_song'] = title
        state['current_url'] = data.get('webpage_url', url)
        state['duration'] = formatted_duration

        await update_now_playing(ctx, title, state['current_url'], formatted_duration, state['loop'], state['autoplay'])

        cancel_queue_task(guild_id)
        queue_tasks[guild_id] = bot.loop.create_task(check_queue(ctx, voice_client))

    except Exception as e:
        logger.error(f"Erro Play Song: {e}")
        await ctx.send(f"❌ Erro ao tocar: {e}")

async def process_queue(guild_id, ctx):
    if guild_id in queues and queues[guild_id]:
        next_url = queues[guild_id].pop(0)
        await play_song(ctx, next_url)
    else:
        # Fila vazia
        state = get_server_state(guild_id)
        if state['autoplay'] and state['current_url']:
             # Lógica simples de autoplay: busca músicas relacionadas ao título atual
             search_query = f"ytsearch:related to {state['current_song']} audio"
             await play_song(ctx, search_query)
        else:
            await asyncio.sleep(60) # Espera 1 minuto antes de sair
            voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
            if voice_client and not voice_client.is_playing() and not state['processing_playlist']:
                await voice_client.disconnect()
                await ctx.send("💤 Fila vazia e inativo. Desconectando.")

async def check_queue(ctx, voice_client):
    guild_id = ctx.guild.id
    state = get_server_state(guild_id)

    try:
        # Loop de espera enquanto toca
        while voice_client.is_connected() and (voice_client.is_playing() or voice_client.is_paused()):
            await asyncio.sleep(1)

        if not voice_client.is_connected(): return

        # Se foi skip, process_queue já foi chamado ou será chamado
        if state['skip_requested']:
            state['skip_requested'] = False
            await process_queue(guild_id, ctx)
            return

        # Loop mode
        if state['loop'] and state['current_url']:
            await play_song(ctx, state['current_url'])
        else:
            await process_queue(guild_id, ctx)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Erro Check Queue: {e}")

# --- COMANDOS E INTERAÇÃO ---

@bot.event
async def on_interaction(interaction):
    if not interaction.type == discord.InteractionType.component: return

    custom_id = interaction.data.get('custom_id')
    guild_id = interaction.guild_id
    voice_client = discord.utils.get(bot.voice_clients, guild=interaction.guild)
    state = get_server_state(guild_id)
    ctx = await bot.get_context(interaction.message)

    if not voice_client:
        await interaction.response.send_message("Não estou conectado.", ephemeral=True)
        return

    if custom_id == "pause_resume":
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸️ Pausado", ephemeral=True)
        else:
            voice_client.resume()
            await interaction.response.send_message("▶️ Retomado", ephemeral=True)
    
    elif custom_id == "skip":
        state['loop'] = False # Skip quebra o loop
        state['skip_requested'] = True
        voice_client.stop()
        await interaction.response.send_message("⏭️ Pular", ephemeral=True)

    elif custom_id == "stop":
        queues[guild_id] = []
        cancel_queue_task(guild_id)
        voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Parado e desconectado.", ephemeral=True)
        # Limpa mensagem
        if guild_id in now_playing_messages:
            try: await now_playing_messages[guild_id].delete()
            except: pass

    elif custom_id == "loop":
        state['loop'] = not state['loop']
        await update_now_playing(ctx, state['current_song'], state['current_url'], state['duration'], state['loop'], state['autoplay'])
        await interaction.response.send_message(f"Loop {'Ligado' if state['loop'] else 'Desligado'}", ephemeral=True)

    elif custom_id == "autoplay":
        state['autoplay'] = not state['autoplay']
        await update_now_playing(ctx, state['current_song'], state['current_url'], state['duration'], state['loop'], state['autoplay'])
        await interaction.response.send_message(f"Autoplay {'Ligado' if state['autoplay'] else 'Desligado'}", ephemeral=True)

@bot.command()
async def play(ctx, *, query):
    guild_id = ctx.guild.id
    if guild_id not in queues: queues[guild_id] = []
    
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    is_playing = voice_client and voice_client.is_playing()

    # Lógica simplificada de Spotify
    if "spotify.com" in query:
        if not sp:
            await ctx.send("❌ Spotify não configurado no servidor.")
            return
        
        await ctx.send("🔎 Processando link do Spotify...")
        try:
            if "track" in query:
                track_id = query.split("track/")[1].split("?")[0]
                track = sp.track(track_id)
                search = f"{track['name']} {track['artists'][0]['name']} audio"
                yt_query = f"ytsearch:{search}"
                
                if is_playing:
                    queues[guild_id].append(yt_query)
                    await ctx.send(f"➕ **{track['name']}** adicionada à fila.")
                else:
                    await play_song(ctx, yt_query)

            elif "playlist" in query:
                 # Aqui entraria a lógica de playlist (simplificada para o exemplo caber)
                 # Recomendo usar a lógica que você já tinha de iterar e adicionar na fila
                 await ctx.send("⚠️ Playlists do Spotify podem demorar um pouco para carregar.")
                 playlist_id = query.split("playlist/")[1].split("?")[0]
                 results = sp.playlist_tracks(playlist_id, limit=20)
                 
                 added = 0
                 for item in results['items']:
                     if item['track']:
                        track = item['track']
                        search = f"ytsearch:{track['name']} {track['artists'][0]['name']} audio"
                        if not is_playing and added == 0:
                            await play_song(ctx, search)
                            is_playing = True
                        else:
                            queues[guild_id].append(search)
                        added += 1
                 await ctx.send(f"✅ {added} músicas da playlist adicionadas.")
        
        except Exception as e:
            await ctx.send(f"Erro Spotify: {e}")
        return

    # Lógica YouTube normal
    query = query if query.startswith("http") else f"ytsearch:{query}"
    
    if is_playing:
        queues[guild_id].append(query)
        await ctx.send(f"➕ Adicionado à fila.")
    else:
        await play_song(ctx, query)

@bot.command()
async def clear(ctx):
    queues[ctx.guild.id] = []
    await ctx.send("🗑️ Fila limpa.")

@bot.event
async def on_ready():
    logger.info(f'Bot Online: {bot.user.name}')

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ Erro: DISCORD_TOKEN não encontrado.")
    else:
        bot.run(DISCORD_TOKEN)
