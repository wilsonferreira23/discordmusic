# Usa uma versão leve do Python
FROM python:3.10-slim

# Instala FFmpeg, Git, NODEJS (Novo) e dependências
# O curl é usado para baixar o instalador do Node
RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev git curl && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Configura a pasta de trabalho
WORKDIR /app

# Copia os arquivos
COPY . .

# Instala as bibliotecas
RUN pip install --no-cache-dir -r requirements.txt

# Força a instalação da versão master do yt-dlp (com correções recentes)
RUN pip install --force-reinstall https://github.com/yt-dlp/yt-dlp/archive/master.zip

# Roda o bot
CMD ["python", "main.py"]
