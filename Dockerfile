# Usa un'immagine base Python leggera
FROM python:3.11-slim

# Installa dipendenze di sistema (Tesseract + lingua italiana + strumenti PDF)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    libtesseract-dev \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Imposta la directory di lavoro
WORKDIR /app

# Copia requirements e installa dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il resto del codice nell'immagine
COPY . .

# Espone la porta (Render userà la variabile PORT)
EXPOSE 5000

# Avvia l’app Flask
CMD ["python", "app_docker.py"]
