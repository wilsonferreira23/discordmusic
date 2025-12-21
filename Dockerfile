FROM python:3.10-slim

# Instala git além do ffmpeg (necessário para baixar o yt-dlp do github)
RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

# Instala dependências
# ATENÇÃO: Instalamos o yt-dlp direto do git para ter as correções de anti-bot mais recentes
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --force-reinstall https://github.com/yt-dlp/yt-dlp/archive/master.zip

CMD ["python", "main.py"]
