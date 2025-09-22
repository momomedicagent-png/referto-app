import os
import logging
import pytesseract
import google.generativeai as genai
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_file
from docx import Document
import fitz  # PyMuPDF
from dotenv import load_dotenv
import traceback
from datetime import datetime
import cv2
import numpy as np

# 🔧 Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("app.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# 🔑 API Gemini
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# ⚙️ Flask
app = Flask(__name__, template_folder="templates")
UPLOAD_FOLDER = 'uploads'
ARCHIVE_FOLDER = 'archive'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ARCHIVE_FOLDER, exist_ok=True)


# 🔧 Configurazione Tesseract
def configure_tesseract():
    try:
        pytesseract.get_tesseract_version()
        logger.info("Tesseract configurato correttamente")
        langs = pytesseract.get_languages()
        logger.info(f"Lingue Tesseract disponibili: {langs}")
    except Exception as e:
        logger.error(f"Errore configurazione Tesseract: {e}")
        for path in ['/usr/bin/tesseract', '/usr/local/bin/tesseract']:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Tesseract trovato in: {path}")
                break


configure_tesseract()


# 🔧 Preprocessing OCR
def preprocess_image_for_ocr(image_path):
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    img = cv2.adaptiveThreshold(img, 255,
                                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 2)
    img = cv2.medianBlur(img, 3)
    return img


# 📄 Estrazione testo da file
def extract_text_from_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            doc = fitz.open(file_path)
            for page_num, page in enumerate(doc, start=1):
                page_text = page.get_text()
                if page_text.strip():
                    text += page_text
                    logger.info(f"Testo estratto da pagina {page_num} (PDF digitale)")
                else:
                    # OCR immagini PDF
                    image_list = page.get_images(full=True)
                    if image_list:
                        for img_index, img in enumerate(image_list, start=1):
                            xref = img[0]
                            base_image = doc.extract_image(xref)
                            image_bytes = base_image["image"]
                            img_ext = base_image["ext"]
                            img_path = os.path.join(UPLOAD_FOLDER, f"page{page_num}_img{img_index}.{img_ext}")
                            with open(img_path, "wb") as f:
                                f.write(image_bytes)

                            preprocessed = preprocess_image_for_ocr(img_path)
                            custom_config = r'--oem 1 --psm 6 -l ita+eng'
                            if preprocessed is not None:
                                page_text = pytesseract.image_to_string(preprocessed, config=custom_config)
                            else:
                                pil_img = Image.open(img_path)
                                page_text = pytesseract.image_to_string(pil_img, config=custom_config)

                            text += page_text + "\n"
                            os.remove(img_path)
                            logger.info(f"OCR eseguito su immagine pagina {page_num}, img {img_index}")
                    else:
                        logger.warning(f"Nessun testo o immagine trovata a pagina {page_num}")
            doc.close()

        elif ext in ['.png', '.jpg', '.jpeg']:
            preprocessed = preprocess_image_for_ocr(file_path)
            custom_config = r'--oem 1 --psm 6 -l ita+eng'
            if preprocessed is not None:
                text = pytesseract.image_to_string(preprocessed, config=custom_config)
            else:
                image = Image.open(file_path)
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                text = pytesseract.image_to_string(image, config=custom_config)
            logger.info("Testo estratto da immagine")

        elif ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()

        elif ext == '.docx':
            doc = Document(file_path)
            for para in doc.paragraphs:
                text += para.text + '\n'

        elif ext == '.xlsx':
            import pandas as pd
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


# 🧠 Riassunto
def generate_summary(text):
    if not text.strip():
        return "Nessun testo da riassumere"
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Analizza questo referto medico e fornisci un riassunto chiaro e comprensibile.

        Includi:
        - Diagnosi principale (se presente)
        - Valori anomali evidenziati
        - Raccomandazioni mediche
        - Note importanti per il paziente

        Testo del referto:
        {text}
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(traceback.format_exc())
        return f"Errore generazione riassunto: {str(e)}"


# 📄 Word
def create_word_doc(summary, full_text):
    try:
        doc = Document()
        doc.add_heading('Riassunto Referto Medico', 0)
        doc.add_paragraph(summary)
        doc.add_page_break()
        doc.add_heading('Testo Integrale', level=1)
        doc.add_paragraph(full_text)
        file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
        doc.save(file_path)
        return file_path
    except Exception as e:
        logger.error(traceback.format_exc())
        return None


# 🌐 Routes
@app.route('/')
def home():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_file():
    logger.info("=== INIZIO UPLOAD ===")
    try:
        data = request.get_json(silent=True)

        if data and "extracted_text" in data:
            full_text = data["extracted_text"]
        else:
            if 'file' not in request.files:
                return jsonify({"error": "Nessun file selezionato"}), 400

            files = request.files.getlist("file")
            texts = []
            for file in files:
                filename = file.filename
                filepath = os.path.join(UPLOAD_FOLDER, filename)
                file.save(filepath)
                texts.append(extract_text_from_file(filepath))
                try:
                    os.remove(filepath)
                except:
                    pass
            full_text = "\n\n".join(texts)

        summary = generate_summary(full_text)
        create_word_doc(summary, full_text)

        return jsonify({"summary": summary, "full_text": full_text, "status": "success"})

    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route('/download-summary')
def download_summary():
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File non disponibile"}), 404


@app.route('/test')
def test():
    return jsonify({"status": "OK", "timestamp": str(datetime.now())})


# 🚀 Avvio
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
