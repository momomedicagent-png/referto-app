FROM python:3.11-slim

WORKDIR /app

# Installa Tesseract + lingue ITA/ENG + dipendenze
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-ita \
    tesseract-ocr-eng \
    libtesseract-dev \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

# Installa Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il progetto
COPY . .

CMD ["python", "app_docker.py"]
