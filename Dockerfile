# Usa uma versão leve do Python
FROM python:3.10-slim

# 1. Instala FFmpeg, Git e dependências de áudio do sistema
# O 'git' é necessário caso o pip precise clonar algo, o 'ffmpeg' é para o áudio
RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev git && \
    rm -rf /var/lib/apt/lists/*

# Configura a pasta de trabalho
WORKDIR /app

# 2. Copia TODOS os arquivos (main.py, cookies.txt, requirements.txt) para dentro do container
COPY . .

# 3. Instala as bibliotecas do requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 4. TRUQUE DE MESTRE: Reinstala o yt-dlp direto da versão de desenvolvimento (Master)
# Isso garante que você tenha as correções de anti-bloqueio mais recentes (que ainda não saíram no pip normal)
RUN pip install --force-reinstall https://github.com/yt-dlp/yt-dlp/archive/master.zip

# 5. Roda o bot
CMD ["python", "main.py"]
