import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
import logging
from discord.ui import Button, View
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# --- CONFIGURAÇÃO BÁSICA ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('music_bot')

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET")

# Configuração Spotify
sp = None
if SPOTIFY_CLIENT_ID:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET))
    except Exception as e:
        logger.error(f"Erro ao configurar Spotify: {e}")

# --- CONFIGURAÇÃO PARA BAIXAR (Evita erro 403) ---
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'outtmpl': '%(id)s.%(ext)s', 
    'quiet': True,
    'nocheckcertificate': True,
    'cookiefile': 'cookies.txt', 
}

queues = {}
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- BOTÕES (AGORA COM PAUSE) ---
def create_controls():
    view = View(timeout=None)
    
    # Botão Pause/Resume
    pause_btn = Button(style=discord.ButtonStyle.secondary, emoji="⏯️", custom_id="pause")
    # Botão Skip
    skip_btn = Button(style=discord.ButtonStyle.primary, emoji="⏭️", custom_id="skip")
    # Botão Stop
    stop_btn = Button(style=discord.ButtonStyle.danger, emoji="⏹️", custom_id="stop")
    
    view.add_item(pause_btn)
    view.add_item(skip_btn)
    view.add_item(stop_btn)
    return view

# --- LÓGICA DE FILA E PLAY ---
def check_queue(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and queues[guild_id]:
        query = queues[guild_id].pop(0)
        asyncio.run_coroutine_threadsafe(play_song(ctx, query), bot.loop)
    else:
        # Fila acabou
        pass

async def play_song(ctx, query):
    voice_client = ctx.voice_client
    if not voice_client: return

    try:
        # 1. Baixa o arquivo
        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(query, download=True))
        
        if 'entries' in data: data = data['entries'][0]
        filename = ydl_opts['outtmpl'] % {'id': data['id'], 'ext': data['ext']}
        
        # Garante que acha o arquivo mesmo se a extensão mudar
        if not os.path.exists(filename):
            for f in os.listdir('.'):
                if f.startswith(data['id']):
                    filename = f
                    break

        # 2. Toca
        source = discord.FFmpegPCMAudio(filename)
        
        def after_play(e):
            if e: logger.error(f"Erro: {e}")
            # 3. Apaga e chama a próxima
            try:
                os.remove(filename)
            except:
                pass
            check_queue(ctx)

        if voice_client.is_playing(): voice_client.stop()
        
        voice_client.play(source, after=after_play)
        
        # Envia a mensagem com os botões
        view = create_controls()
        await ctx.send(f"▶️ Tocando: **{data.get('title')}**", view=view)

    except Exception as e:
        logger.error(f"Erro: {e}")
        await ctx.send("❌ Erro ao processar música.")
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

    # Lógica Spotify
    if "spotify.com" in query and sp:
        await ctx.send("⏳ Processando Spotify...")
        try:
            if "track" in query:
                t = sp.track(query)
                queues[guild_id].append(f"ytsearch:{t['name']} {t['artists'][0]['name']} audio")
            elif "playlist" in query:
                results = sp.playlist_tracks(query, limit=50)
                for item in results['items']:
                    if item['track']:
                        t = item['track']
                        queues[guild_id].append(f"ytsearch:{t['name']} {t['artists'][0]['name']} audio")
        except Exception as e:
            await ctx.send(f"Erro no Spotify: {e}")
    else:
        if "http" not in query: query = f"ytsearch:{query}"
        queues[guild_id].append(query)

    if not voice_client.is_playing():
        check_queue(ctx)
    else:
        await ctx.send("✅ Adicionado à fila.")

# --- EVENTOS DE BOTÃO ---
@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component: return
    
    custom_id = interaction.data.get('custom_id')
    voice_client = interaction.guild.voice_client
    
    if not voice_client:
        return await interaction.response.send_message("Não estou conectado.", ephemeral=True)

    if custom_id == "pause":
        if voice_client.is_playing():
            voice_client.pause()
            await interaction.response.send_message("⏸️ Pausado", ephemeral=True)
        elif voice_client.is_paused():
            voice_client.resume()
            await interaction.response.send_message("▶️ Retomado", ephemeral=True)
            
    elif custom_id == "skip":
        if voice_client.is_playing() or voice_client.is_paused():
            voice_client.stop() # Isso aciona o after_play e vai pra próxima
            await interaction.response.send_message("⏭️ Pulado", ephemeral=True)
            
    elif custom_id == "stop":
        queues[interaction.guild.id] = [] # Limpa fila
        voice_client.stop()
        await voice_client.disconnect()
        await interaction.response.send_message("⏹️ Parado", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Logado como: {bot.user}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
