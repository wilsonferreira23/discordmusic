# Usa uma imagem leve do Python
FROM python:3.10-slim

# Instala o FFmpeg e dependências de sistema (O PULO DO GATO ESTÁ AQUI)
RUN apt-get update && \
    apt-get install -y ffmpeg libopus-dev && \
    rm -rf /var/lib/apt/lists/*

# Configura a pasta de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . .

# Instala as dependências do Python
RUN pip install --no-cache-dir -r requirements.txt

# Comando para iniciar o bot
CMD ["python", "main.py"]
