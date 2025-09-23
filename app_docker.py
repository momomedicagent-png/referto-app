import os
import logging
import pytesseract
import google.generativeai as genai
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_file
from docx import Document
import fitz  # PyMuPDF
from datetime import datetime
import cv2
import numpy as np
import traceback
import pandas as pd

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Gemini API
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Flask setup
app = Flask(__name__, template_folder="templates")
UPLOAD_FOLDER = "uploads"
ARCHIVE_FOLDER = "archive"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ARCHIVE_FOLDER, exist_ok=True)

# --- Configura Tesseract ---
def configure_tesseract():
    try:
        pytesseract.get_tesseract_version()
        logger.info("Tesseract configurato correttamente")
    except Exception as e:
        logger.error(f"Errore Tesseract: {e}")
        for path in ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Configurato Tesseract da path: {path}")
                break
configure_tesseract()

# --- Preprocessing ---
def preprocess_image_for_ocr(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None: return None
    img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 2)
    img = cv2.medianBlur(img, 3)
    return img

# --- Estrazione testo ---
def extract_text_from_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    text = ""
    try:
        if ext == ".pdf":
            doc = fitz.open(file_path)
            for page_num, page in enumerate(doc, start=1):
                page_text = page.get_text().strip()
                if page_text and len(page_text) > 10:
                    text += page_text + "\n"
                else:
                    for img_index, img in enumerate(page.get_images(full=True), start=1):
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        img_path = os.path.join(UPLOAD_FOLDER, f"page{page_num}_img{img_index}.{base_image['ext']}")
                        with open(img_path, "wb") as f: f.write(base_image["image"])
                        preprocessed = preprocess_image_for_ocr(img_path)
                        config = r"--oem 1 --psm 6 -l ita+eng"
                        if preprocessed is not None:
                            page_text = pytesseract.image_to_string(preprocessed, config=config)
                        else:
                            page_text = pytesseract.image_to_string(Image.open(img_path), config=config)
                        text += page_text + "\n"
                        os.remove(img_path)
            doc.close()

        elif ext in [".png", ".jpg", ".jpeg"]:
            preprocessed = preprocess_image_for_ocr(file_path)
            config = r"--oem 1 --psm 6 -l ita+eng"
            if preprocessed is not None:
                text = pytesseract.image_to_string(preprocessed, config=config)
            else:
                img = Image.open(file_path)
                if img.mode != "RGB": img = img.convert("RGB")
                text = pytesseract.image_to_string(img, config=config)

        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f: text = f.read()

        elif ext == ".docx":
            doc = Document(file_path)
            text = "\n".join([p.text for p in doc.paragraphs])

        elif ext == ".xlsx":
            xls = pd.read_excel(file_path, sheet_name=None)
            for sheet, df in xls.items():
                text += f"\n--- Foglio: {sheet} ---\n{df.to_string(index=False)}"

        else:
            text = "Formato non supportato."
    except Exception:
        logger.error(traceback.format_exc())
        text = "Errore estrazione testo."
    return text.strip()

# --- Prompt generator ---
def get_prompt(base_type, custom, text):
    if base_type == "simple":
        return f"Riassunto semplice e comprensibile:\n\n{text}"
    elif base_type == "intermediate":
        return f"Analisi intermedia strutturata con diagnosi, parametri, terapie:\n\n{text}"
    elif base_type == "detailed":
        return f"Analisi medica dettagliata per specialisti:\n\n{text}"
    elif base_type == "custom" and custom:
        return f"{custom}\n\nTesto referto:\n{text}"
    return f"Riassumi il seguente referto medico:\n{text}"

# --- Summarization ---
def generate_summary(prompt):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        resp = model.generate_content(prompt)
        return resp.text
    except Exception:
        logger.error(traceback.format_exc())
        return "Errore generazione riassunto."

# --- Word export ---
def create_word_doc(summary, full_text):
    doc = Document()
    doc.add_heading("Riassunto Referto Medico", 0)
    doc.add_paragraph(summary)
    doc.add_page_break()
    doc.add_heading("Testo Integrale", level=1)
    doc.add_paragraph(full_text)
    path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    doc.save(path)
    return path

# --- Routes ---
@app.route("/")
def home(): return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        if "file" not in request.files:
            return jsonify({"error": "Nessun file inviato"}), 400
        texts = []
        for f in request.files.getlist("file"):
            path = os.path.join(UPLOAD_FOLDER, f.filename)
            f.save(path)
            texts.append(extract_text_from_file(path))
            os.remove(path)
        return jsonify({"full_text": "\n\n".join(texts)})
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"error": "Errore durante upload"}), 500

@app.route("/analyze", methods=["POST"])
def analyze():
    try:
        text = request.form.get("extracted_text", "")
        if not text: return jsonify({"error": "Nessun testo ricevuto"}), 400
        prompt = get_prompt(request.form.get("prompt_type","simple"),
                            request.form.get("custom_prompt",""), text)
        summary = generate_summary(prompt)
        create_word_doc(summary, text)
        return jsonify({"summary": summary})
    except Exception:
        logger.error(traceback.format_exc())
        return jsonify({"error": "Errore analisi"}), 500

@app.route("/download-summary")
def download_summary():
    path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    return send_file(path, as_attachment=True) if os.path.exists(path) else jsonify({"error":"File non disponibile"}),404

@app.route("/reset", methods=["POST"])
def reset():
    try:
        for f in os.listdir(UPLOAD_FOLDER):
            os.remove(os.path.join(UPLOAD_FOLDER, f))
        for f in os.listdir(ARCHIVE_FOLDER):
            os.remove(os.path.join(ARCHIVE_FOLDER, f))
        return jsonify({"status":"reset ok"})
    except Exception:
        return jsonify({"error":"Errore reset"}),500

@app.route("/test")
def test(): return jsonify({"status": "OK", "timestamp": str(datetime.now())})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
