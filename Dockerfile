# Usa un'immagine Python ufficiale
FROM python:3.9-slim

# Installa le dipendenze di sistema necessarie
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    tesseract-ocr-eng \
    libtesseract-dev \
    poppler-utils \
    libgl1-mesa-dri \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Imposta la directory di lavoro
WORKDIR /app

# Copia i file dei requisiti
COPY requirements.txt .

# Installa le dipendenze Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia il codice dell'applicazione
COPY . .

# Crea le directory necessarie
RUN mkdir -p uploads archive templates

# Esponi la porta
EXPOSE 5000

# Comando per avviare l'applicazione
CMD ["python", "app_docker.py"]