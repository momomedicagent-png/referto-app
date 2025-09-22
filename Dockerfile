FROM python:3.10-slim

# Installa Tesseract e dipendenze
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    libtesseract-dev \
    poppler-utils \
    build-essential \
    && apt-get clean

# Crea cartella app
WORKDIR /app

# Copia i file
COPY . /app

# Installa le librerie Python
RUN pip install --no-cache-dir -r requirements.txt

# Espone la porta
ENV PORT=5000
EXPOSE $PORT

# Avvia l'app
CMD ["python", "app_docker.py"]
