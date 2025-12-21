import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
from discord.ui import Button, View
import logging
from collections import defaultdict

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('music_bot')

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

# --- CONFIGURAÇÃO DO YT-DLP COM COOKIES ---
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'source_address': '0.0.0.0',
    # AQUI ESTÁ O SEGREDO: Força o uso do arquivo de cookies
    'cookiefile': 'cookies.txt', 
    # Adiciona User Agent para parecer navegador real
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# --- CONFIGURAÇÃO DO FFMPEG ---
ffmpeg_opts = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# --- INICIALIZAÇÃO ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Estados globais
queues = {}
server_states = {}

def get_server_state(guild_id):
    if guild_id not in server_states:
        server_states[guild_id] = {'loop': False, 'current_title': None}
    return server_states[guild_id]

# --- PLAYER DE MÚSICA ---
async def play_song(ctx, url):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    
    if not os.path.exists('cookies.txt'):
        logger.warning("⚠️ ARQUIVO COOKIES.TXT NÃO ENCONTRADO! O BOT PODE FALHAR.")

    if not voice_client:
        if ctx.author.voice:
            voice_client = await ctx.author.voice.channel.connect()
        else:
            await ctx.send("Entre em um canal de voz!")
            return

    try:
        loop = asyncio.get_event_loop()
        # Baixa as informações usando os Cookies
        data = await loop.run_in_executor(None, lambda: youtube_dl.YoutubeDL(ydl_opts).extract_info(url, download=False))

        if 'entries' in data: data = data['entries'][0]
        
        stream_url = data['url']
        title = data.get('title', 'Música')
        
        # --- INJEÇÃO DE HEADERS NO FFMPEG (CRUCIAL PARA 403) ---
        # Mesmo com cookies, o ffmpeg precisa fingir ser o navegador
        http_headers = data.get('http_headers', {})
        header_args = ""
        for key, value in http_headers.items():
            header_args += f"{key}: {value}\r\n"
        
        current_ffmpeg_opts = ffmpeg_opts.copy()
        current_ffmpeg_opts['before_options'] += f" -headers \"{header_args}\""
        # -------------------------------------------------------

        source = discord.FFmpegPCMAudio(stream_url, **current_ffmpeg_opts)
        
        def after_play(e):
            if e: logger.error(f"Erro Player: {e}")
            # Lógica simples para checar fila ou loop
            coro = check_queue(ctx, voice_client)
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            try: fut.result()
            except: pass

        if voice_client.is_playing(): voice_client.stop()
        voice_client.play(source, after=after_play)
        
        await ctx.send(f"▶️ Tocando: **{title}**")
        get_server_state(ctx.guild.id)['current_title'] = title

    except Exception as e:
        logger.error(f"Erro: {e}")
        await ctx.send(f"❌ Erro: {e}")

async def check_queue(ctx, voice_client):
    # Simplificado para evitar erros de loop complexo por enquanto
    pass 

# --- COMANDOS ---
@bot.command()
async def play(ctx, *, query):
    if "http" not in query: query = f"ytsearch:{query}"
    await play_song(ctx, query)

@bot.command()
async def stop(ctx):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)
    if voice_client: await voice_client.disconnect()

@bot.event
async def on_ready():
    print(f"Logado como {bot.user}")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
