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

# --- Configurazione Tesseract ---
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

# --- Preprocessing immagini ---
def preprocess_image_for_ocr(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
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
                    # PDF digitale → testo estratto
                    text += page_text + "\n"
                    logger.info(f"Pagina {page_num}: testo digitale estratto")
                else:
                    # PDF immagine → OCR
                    image_list = page.get_images(full=True)
                    if not image_list:
                        logger.warning(f"Pagina {page_num}: nessun testo o immagine trovata")
                        continue

                    for img_index, img in enumerate(image_list, start=1):
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        img_ext = base_image["ext"]
                        img_path = os.path.join(
                            UPLOAD_FOLDER, f"page{page_num}_img{img_index}.{img_ext}"
                        )
                        with open(img_path, "wb") as f:
                            f.write(image_bytes)

                        preprocessed = preprocess_image_for_ocr(img_path)
                        config = r"--oem 1 --psm 6 -l ita+eng"
                        if preprocessed is not None:
                            page_text = pytesseract.image_to_string(preprocessed, config=config)
                        else:
                            pil_img = Image.open(img_path)
                            page_text = pytesseract.image_to_string(pil_img, config=config)

                        text += page_text + "\n"
                        try:
                            os.remove(img_path)
                        except:
                            logger.warning(f"Impossibile eliminare file temporaneo {img_path}")

                    logger.info(f"Pagina {page_num}: OCR completato")
            doc.close()

        elif ext in [".png", ".jpg", ".jpeg"]:
            preprocessed = preprocess_image_for_ocr(file_path)
            config = r"--oem 1 --psm 6 -l ita+eng"
            if preprocessed is not None:
                text = pytesseract.image_to_string(preprocessed, config=config)
            else:
                image = Image.open(file_path)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                text = pytesseract.image_to_string(image, config=config)
            logger.info(f"OCR completato su immagine {file_path}")

        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()

        elif ext == ".docx":
            doc = Document(file_path)
            for para in doc.paragraphs:
                text += para.text + "\n"

        elif ext == ".xlsx":
            xls = pd.read_excel(file_path, sheet_name=None)
            for sheet_name, df in xls.items():
                text += f"\n--- Foglio: {sheet_name} ---\n"
                text += df.to_string(index=False)

        else:
            text = "Formato non supportato."
            logger.warning(f"Formato non supportato: {ext}")

    except Exception as e:
        text = f"Errore estrazione testo: {str(e)}"
        logger.error(traceback.format_exc())

    return text.strip() if text else "Nessun testo estratto"

# --- Prompt generator ---
def get_prompt(base_type, custom, text):
    if base_type == "simple":
        return f"Analizza questo referto medico e fornisci un riassunto chiaro e semplice per un paziente:\n\n{text}"
    elif base_type == "intermediate":
        return f"""Analizza questo referto medico e fornisci un riassunto strutturato.
        - Diagnosi principale
        - Parametri fuori norma con valori numerici
        - Terapie o raccomandazioni
        - Considerazioni cliniche sintetiche
        Testo referto:
        {text}"""
    elif base_type == "detailed":
        return f"""Analisi tecnica dettagliata del referto medico:
        - Diagnosi e classificazioni mediche specifiche
        - Valori di laboratorio con range normali
        - Correlazioni cliniche
        - Implicazioni prognostiche o terapeutiche
        Testo referto:
        {text}"""
    elif base_type == "custom" and custom:
        return f"{custom}\n\nTesto referto:\n{text}"
    return f"Riassumi il seguente referto medico:\n{text}"

# --- Summarization ---
def generate_summary(prompt):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(traceback.format_exc())
        return f"Errore generazione riassunto: {str(e)}"

# --- Word export ---
def create_word_doc(summary, full_text):
    doc = Document()
    doc.add_heading("Riassunto Referto Medico", 0)
    doc.add_paragraph(summary)
    doc.add_page_break()
    doc.add_heading("Testo Integrale", level=1)
    doc.add_paragraph(full_text)
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    doc.save(file_path)
    return file_path

# --- Routes ---
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        full_text = ""
        if "extracted_text" in request.form:
            full_text = request.form.get("extracted_text", "")
            logger.info("Ricevuto testo OCR lato client")
        elif "file" in request.files:
            texts = []
            for file in request.files.getlist("file"):
                filepath = os.path.join(UPLOAD_FOLDER, file.filename)
                file.save(filepath)
                texts.append(extract_text_from_file(filepath))
                try:
                    os.remove(filepath)
                except:
                    logger.warning(f"Impossibile eliminare file {filepath}")
            full_text = "\n\n".join(texts)
        else:
            return jsonify({"error": "Nessun file o testo inviato"}), 400

        prompt_type = request.form.get("prompt_type", "simple")
        custom_prompt = request.form.get("custom_prompt", "")
        prompt = get_prompt(prompt_type, custom_prompt, full_text)

        summary = generate_summary(prompt)
        create_word_doc(summary, full_text)

        return jsonify({"summary": summary, "full_text": full_text, "status": "success"})
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/download-summary")
def download_summary():
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File non disponibile"}), 404

@app.route("/test")
def test():
    return jsonify({"status": "OK", "timestamp": str(datetime.now())})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
