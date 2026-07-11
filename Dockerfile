FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev git curl nodejs npm unzip && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --force-reinstall https://github.com/yt-dlp/yt-dlp/archive/master.zip

CMD ["python", "main.py"]
