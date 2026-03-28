import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import logging
from discord.ui import Button, View
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('music_bot')

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

sp = None
if SPOTIFY_CLIENT_ID:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
    except: pass

# --- CONFIGURAÇÃO DE DOWNLOAD ---
ydl_opts = {
    'format': 'bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'outtmpl': '%(id)s.%(ext)s',
    'quiet': True,
    'nocheckcertificate': True,
    'cookiefile': 'cookies.txt',
    # Otimização para download mais rápido
    'concurrent_fragment_downloads': 5, 
}

# --- ESTADOS GLOBAIS ---
queues = {} 
server_states = {} 
preloads = {} # Armazena a próxima música já baixada {guild_id: data_dict}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def get_state(guild_id):
    if guild_id not in server_states:
        server_states[guild_id] = {'last_msg': None}
    return server_states[guild_id]

# --- UI: CONTROLES E EMBED BONITO ---
def create_controls():
    view = View(timeout=None)
    # Botões solicitados: Pause, Skip, Stop
    view.add_item(Button(style=discord.ButtonStyle.primary, emoji="⏯️", custom_id="pause"))
    view.add_item(Button(style=discord.ButtonStyle.secondary, emoji="⏭️", custom_id="skip"))
    view.add_item(Button(style=discord.ButtonStyle.danger, emoji="⏹️", custom_id="stop"))
    return view

async def update_player_message(ctx, data, new=False):
    state = get_state(ctx.guild.id)
    view = create_controls()
    
    # Formata a duração (Ex: 03:20)
    duration = data.get('duration', 0)
    mins, secs = divmod(duration, 60)
    duration_str = f"{mins:02d}:{secs:02d}"
    
    # Cria o Embed estilo Spotify (Screenshot_452)
    embed = discord.Embed(color=0x1DB954) # Verde Spotify
    embed.set_author(name="Tocando Agora", icon_url="https://i.imgur.com/7R8gM2W.png") # Ícone de música opcional
    
    # Título clicável e duração
    title = data.get('title', 'Música Desconhecida')
    url = data.get('webpage_url', '')
    embed.description = f"**[{title}]({url})** `[{duration_str}]`\n\nUse os botões abaixo para controlar a música."
    
    # Adiciona Thumbnail se tiver
    if data.get('thumbnail'):
        embed.set_thumbnail(url=data.get('thumbnail'))

    # Limpeza da mensagem anterior
    if new and state['last_msg']:
        try: await state['last_msg'].delete()
        except: pass

    msg = await ctx.send(embed=embed, view=view)
    state['last_msg'] = msg

# --- SISTEMA DE PRE-DOWNLOAD (ZERO DELAY) ---
async def download_track(query):
    """Baixa a música e retorna os dados (sem tocar)."""
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(query, download=True))
        if 'entries' in data: data = data['entries'][0]
        
        # Garante o nome do arquivo correto
        filename = f"{data['id']}.{data['ext']}"
        if not os.path.exists(filename):
            for f in os.listdir('.'):
                if f.startswith(data['id']):
                    filename = f
                    break
        
        data['filename'] = filename
        return data
    except Exception as e:
        logger.error(f"Erro no download: {e}")
        return None

async def preload_next_song(guild_id):
    """Baixa a próxima música da fila em background."""
    if guild_id in queues and queues[guild_id]:
        next_query = queues[guild_id][0] # Olha a próxima (sem remover ainda)
        logger.info(f"Pré-baixando: {next_query}")
        
        data = await download_track(next_query)
        if data:
            preloads[guild_id] = data
            logger.info("Pré-download concluído!")

def check_queue(ctx):
    guild_id = ctx.guild.id
    
    # 1. Verifica se já temos a música pré-baixada
    if guild_id in preloads and preloads[guild_id]:
        data = preloads[guild_id]
        del preloads[guild_id]
        
        # Remove da fila de queries pois já baixamos
        if guild_id in queues and queues[guild_id]:
            queues[guild_id].pop(0)
            
        asyncio.run_coroutine_threadsafe(play_song_file(ctx, data), bot.loop)
        return

    # 2. Se não tem pré-baixada, tenta baixar a próxima normal
    if guild_id in queues and queues[guild_id]:
        query = queues[guild_id].pop(0)
        asyncio.run_coroutine_threadsafe(play_song_fresh(ctx, query), bot.loop)
    else:
        # Fila acabou
        state = get_state(guild_id)
        if state['last_msg']:
            asyncio.run_coroutine_threadsafe(state['last_msg'].delete(), bot.loop)
            state['last_msg'] = None

async def play_song_fresh(ctx, query):
    """Baixa e Toca (Modo Lento - Fallback)."""
    data = await download_track(query)
    if data:
        await play_song_file(ctx, data)
    else:
        await ctx.send("❌ Erro ao baixar música. Pulando...")
        check_queue(ctx)

async def play_song_file(ctx, data):
    """Toca um arquivo que JÁ existe."""
    voice_client = ctx.voice_client
    if not voice_client: return

    filename = data['filename']
    
    # Toca
    try:
        source = discord.FFmpegPCMAudio(filename)
        
        def after_play(e):
            # Apaga o arquivo atual
            try: 
                if os.path.exists(filename): os.remove(filename)
            except: pass
            # Chama a próxima
            check_queue(ctx)

        if voice_client.is_playing(): voice_client.stop()
        voice_client.play(source, after=after_play)
        
        # Atualiza Interface
        await update_player_message(ctx, data, new=True)
        
        # === A MÁGICA DO ZERO DELAY ===
        # Enquanto essa toca, já dispara o download da PRÓXIMA
        asyncio.create_task(preload_next_song(ctx.guild.id))
        
    except Exception as e:
        logger.error(f"Erro ao tocar arquivo: {e}")
        check_queue(ctx)

# --- COMANDOS ---
@bot.command()
async def play(ctx, *, query):
    guild_id = ctx.guild.id
    if guild_id not in queues: queues[guild_id] = []
    
    voice_client = ctx.voice_client
    if not voice_client:
        if ctx.author.voice:
            voice_client = await ctx.author.voice.channel.connect()
        else:
            return await ctx.send("Entre na voz!")

    try: await ctx.message.delete()
    except: pass

    # Spotify Logic
    added_count = 0
    if "spotify.com" in query.lower() and sp:
        msg = await ctx.send("⏳ Processando Spotify")
        try:
            clean_url = query.split('?')[0]
            if "track" in clean_url:
                t = sp.track(clean_url.split("track/")[-1])
                queues[guild_id].append(f"ytsearch:{t['name']} {t['artists'][0]['name']} audio")
            elif "playlist" in clean_url:
                res = sp.playlist_tracks(clean_url.split("playlist/")[-1], limit=100)
                for i in res['items']:
                    if i['track']:
                        queues[guild_id].append(f"ytsearch:{i['track']['name']} {i['track']['artists'][0]['name']} audio")
            await msg.delete()
        except: await msg.edit(content="❌ Erro no link Spotify.", delete_after=5)
    else:
        queues[guild_id].append(query if "http" in query else f"ytsearch:{query}")

    if ctx.voice_client and not ctx.voice_client.is_playing():
        check_queue(ctx)
    else: await ctx.send("✅ Adicionado à fila.", delete_after=5)

@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component: return
    custom_id = interaction.data.get('custom_id')
    voice_client = interaction.guild.voice_client
    await interaction.response.defer()

    if not voice_client: return

    if custom_id == "pause":
        if voice_client.is_playing(): voice_client.pause()
        elif voice_client.is_paused(): voice_client.resume()
    elif custom_id == "skip":
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop()
    elif custom_id == "stop":
        guild_id = interaction.guild.id
        
        # 1. Limpa a fila de links
        queues[guild_id] = []
        
        # 2. LIMPA A GAVETA DE PRÉ-DOWNLOAD (Isso corrige o bug)
        if guild_id in preloads:
            # Tenta apagar o arquivo físico que estava baixado para não sobrar lixo
            data_to_clean = preloads[guild_id]
            try:
                filename = data_to_clean.get('filename')
                if filename and os.path.exists(filename):
                    os.remove(filename)
            except:
                pass
            # Remove a entrada do dicionário
            del preloads[guild_id]
        
        # 3. Para a música atual e desconecta
        voice_client.stop()
        await voice_client.disconnect()
        
        # 4. Limpa a interface visual
        state = get_state(guild_id)
        if state['last_msg']:
            try:
                await state['last_msg'].delete()
                state['last_msg'] = None
            except:
                pass
        
        await interaction.response.send_message("⏹️ **Fila limpa e bot desconectado.**", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Aura Music Online: {bot.user}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
