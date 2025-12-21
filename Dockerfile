FROM python:3.10-slim

# Instala ffmpeg e git
RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# O comando COPY . . vai copiar o cookies.txt para dentro do container
COPY . .

# Instala dependências e força yt-dlp atualizado
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --force-reinstall https://github.com/yt-dlp/yt-dlp/archive/master.zip

CMD ["python", "main.py"]
