import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import os
import logging

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('music_bot')

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

# --- CONFIGURAÇÃO YT-DLP (ANTI-BLOQUEIO) ---
ydl_opts = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'default_search': 'ytsearch',
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    # CRÍTICO: Força IPv4 (YouTube bloqueia IPv6 do Railway)
    'force_ipv4': True,
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt',
    # User Agent genérico para parecer um PC comum
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'nocheckcertificate': True,
}

# --- CONFIGURAÇÃO FFMPEG ---
# Adicionei user_agent aqui também para garantir
ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
    'options': '-vn'
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    logger.info(f'Bot Online como {bot.user}')

async def play_song(ctx, url):
    voice_client = discord.utils.get(bot.voice_clients, guild=ctx.guild)

    if not voice_client:
        if ctx.author.voice:
            voice_client = await ctx.author.voice.channel.connect()
        else:
            await ctx.send("Entre em um canal de voz!")
            return

    try:
        await ctx.send(f"🔎 Buscando: `{url}` ...")
        
        loop = asyncio.get_event_loop()
        # Baixa info sem download
        data = await loop.run_in_executor(None, lambda: youtube_dl.YoutubeDL(ydl_opts).extract_info(url, download=False))

        if 'entries' in data:
            data = data['entries'][0]

        stream_url = data['url']
        title = data.get('title', 'Desconhecido')
        
        # --- A MÁGICA DO HEADER (SOLUÇÃO DO ERRO 403) ---
        # Pegamos os headers que o yt-dlp usou e forçamos no ffmpeg
        http_headers = data.get('http_headers', {})
        header_args = ""
        
        # Constrói a string de headers manualmente
        for key, value in http_headers.items():
            header_args += f"{key}: {value}\r\n"
            
        # Força o FFmpeg a usar esses headers e o User-Agent correto
        current_opts = ffmpeg_options.copy()
        current_opts['before_options'] += f" -headers \"{header_args}\" -user_agent \"{ydl_opts['user_agent']}\""
        
        # Cria o player
        source = discord.FFmpegPCMAudio(stream_url, **current_opts)
        
        def after_play(e):
            if e: logger.error(f"Erro Player: {e}")

        if voice_client.is_playing():
            voice_client.stop()
            
        voice_client.play(source, after=after_play)
        await ctx.send(f"▶️ Tocando: **{title}**")

    except Exception as e:
        logger.error(f"Erro Play: {e}")
        await ctx.send(f"❌ Erro: {e}")

@bot.command()
async def play(ctx, *, query):
    if "http" not in query: query = f"ytsearch:{query}"
    await play_song(ctx, query)

@bot.command()
async def stop(ctx):
    if ctx.voice_client: await ctx.voice_client.disconnect()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
