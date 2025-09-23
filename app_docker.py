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
import shutil
import threading
import time

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

# Global per gestire timeout
processing_status = {}

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

# --- Preprocessing immagini OTTIMIZZATO ---
def preprocess_image_for_ocr(image_path, fast_mode=True):
    """Preprocessing ottimizzato con modalità veloce per Render"""
    try:
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        
        if fast_mode:
            # Modalità veloce: solo resize e threshold semplice
            h, w = img.shape
            if max(h, w) > 1500:  # Ridimensiona se troppo grande
                scale = 1500 / max(h, w)
                img = cv2.resize(img, (int(w*scale), int(h*scale)))
            
            _, img = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            # Modalità completa
            img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 31, 2)
            img = cv2.medianBlur(img, 3)
        
        return img
    except Exception as e:
        logger.error(f"Errore preprocessing: {e}")
        return None

# --- Estrazione testo OTTIMIZZATA ---
def extract_text_from_file_async(file_path, task_id):
    """Versione asincrona con timeout handling"""
    try:
        processing_status[task_id] = {"status": "processing", "progress": 0}
        ext = os.path.splitext(file_path)[1].lower()
        text = ""
        
        if ext == ".pdf":
            doc = fitz.open(file_path)
            total_pages = len(doc)
            
            for page_num, page in enumerate(doc, start=1):
                if time.time() - processing_status[task_id].get("start_time", 0) > 25:  # Timeout 25s
                    processing_status[task_id]["status"] = "timeout"
                    return "⚠️ Timeout: elaborazione troppo lenta. Prova con immagini più piccole."
                
                processing_status[task_id]["progress"] = (page_num / total_pages) * 100
                
                page_text = page.get_text().strip()
                if page_text and len(page_text) > 10:
                    text += page_text + "\n"
                    logger.info(f"Pagina {page_num}: testo digitale estratto")
                else:
                    # OCR veloce per Render
                    image_list = page.get_images(full=True)
                    for img_index, img in enumerate(image_list[:2], start=1):  # Max 2 img per pagina
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        img_ext = base_image["ext"]
                        img_path = os.path.join(UPLOAD_FOLDER, f"temp_img_{task_id}.{img_ext}")
                        
                        with open(img_path, "wb") as f:
                            f.write(image_bytes)

                        # OCR veloce
                        preprocessed = preprocess_image_for_ocr(img_path, fast_mode=True)
                        config = r"--oem 1 --psm 6 -l ita"  # Solo italiano per velocità
                        
                        if preprocessed is not None:
                            page_text = pytesseract.image_to_string(preprocessed, config=config)
                        else:
                            pil_img = Image.open(img_path)
                            if pil_img.mode != "RGB":
                                pil_img = pil_img.convert("RGB")
                            page_text = pytesseract.image_to_string(pil_img, config=config)

                        text += page_text + "\n"
                        if os.path.exists(img_path):
                            os.remove(img_path)
                        break  # Solo la prima immagine per velocità
            doc.close()

        elif ext in [".png", ".jpg", ".jpeg"]:
            # OCR su immagine con timeout
            preprocessed = preprocess_image_for_ocr(file_path, fast_mode=True)
            config = r"--oem 1 --psm 6 -l ita"
            
            if preprocessed is not None:
                text = pytesseract.image_to_string(preprocessed, config=config)
            else:
                image = Image.open(file_path)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                text = pytesseract.image_to_string(image, config=config)

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

        processing_status[task_id]["status"] = "completed"
        processing_status[task_id]["result"] = text.strip() if text else "Nessun testo estratto"
        
    except Exception as e:
        processing_status[task_id]["status"] = "error"
        processing_status[task_id]["result"] = f"Errore: {str(e)}"
        logger.error(traceback.format_exc())

# --- Prompt generator ---
def get_prompt(base_type, custom, text):
    # Limita il testo per evitare timeout API
    max_chars = 8000
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[testo troncato per performance]"
    
    if base_type == "simple":
        return f"Analizza questo referto medico e fornisci un riassunto chiaro e semplice per un paziente (max 300 parole):\n\n{text}"
    elif base_type == "intermediate":
        return f"""Analizza questo referto medico e fornisci un riassunto strutturato (max 400 parole):
        - Diagnosi principale
        - Parametri fuori norma con valori numerici
        - Terapie o raccomandazioni
        Testo referto:
        {text}"""
    elif base_type == "detailed":
        return f"""Analisi tecnica del referto medico (max 500 parole):
        - Diagnosi e classificazioni mediche
        - Valori di laboratorio con range normali
        - Correlazioni cliniche
        Testo referto:
        {text}"""
    elif base_type == "custom" and custom:
        return f"{custom}\n\nTesto referto:\n{text}"
    return f"Riassumi il seguente referto medico:\n{text}"

# --- Summarization OTTIMIZZATA ---
def generate_summary(prompt):
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        # Timeout più basso per Gemini
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Errore Gemini: {traceback.format_exc()}")
        return f"⚠️ Servizio temporaneamente non disponibile. Riprova tra qualche minuto.\nErrore: {str(e)}"

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

# --- Routes OTTIMIZZATE ---
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        task_id = str(int(time.time() * 1000))  # Timestamp come ID
        processing_status[task_id] = {"status": "starting", "start_time": time.time()}
        
        files = request.files.getlist("file")
        if not files or not files[0].filename:
            return jsonify({"error": "Nessun file caricato"}), 400
            
        # Controllo dimensioni
        total_size = sum(file.content_length or 0 for file in files if file.content_length)
        if total_size > 10 * 1024 * 1024:  # 10MB limit
            return jsonify({"error": "File troppo grandi. Limite: 10MB totali"}), 400
        
        # Salva file e avvia elaborazione asincrona
        filepaths = []
        for file in files:
            if file.filename:
                filepath = os.path.join(UPLOAD_FOLDER, f"{task_id}_{file.filename}")
                file.save(filepath)
                filepaths.append(filepath)
        
        # Avvia thread di elaborazione
        thread = threading.Thread(target=extract_text_from_files_thread, args=(filepaths, task_id))
        thread.daemon = True
        thread.start()
        
        return jsonify({"task_id": task_id, "status": "processing"})
        
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Errore upload: {str(e)}"}), 500

def extract_text_from_files_thread(filepaths, task_id):
    """Thread per elaborazione asincrona"""
    try:
        texts = []
        for filepath in filepaths:
            extract_text_from_file_async(filepath, f"{task_id}_file")
            if f"{task_id}_file" in processing_status:
                texts.append(processing_status[f"{task_id}_file"].get("result", ""))
            os.remove(filepath)  # Pulisci file temporaneo
        
        full_text = "\n\n".join(texts)
        processing_status[task_id]["status"] = "completed"
        processing_status[task_id]["result"] = full_text
        
    except Exception as e:
        processing_status[task_id]["status"] = "error"
        processing_status[task_id]["result"] = str(e)

@app.route("/check_status/<task_id>", methods=["GET"])
def check_status(task_id):
    """Controlla status elaborazione"""
    if task_id not in processing_status:
        return jsonify({"error": "Task non trovato"}), 404
    
    return jsonify(processing_status[task_id])

@app.route("/analyze", methods=["POST"])
def analyze_text():
    try:
        full_text = request.form.get("extracted_text", "").strip()
        if not full_text:
            return jsonify({"error": "Nessun testo da analizzare"}), 400
            
        prompt_type = request.form.get("prompt_type", "simple")
        custom_prompt = request.form.get("custom_prompt", "")
        
        prompt = get_prompt(prompt_type, custom_prompt, full_text)
        summary = generate_summary(prompt)
        
        # Salva solo se riuscito
        if not summary.startswith("⚠️"):
            create_word_doc(summary, full_text)
        
        return jsonify({"summary": summary, "status": "success"})
        
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": f"Errore analisi: {str(e)}"}), 500

@app.route("/download-summary")
def download_summary():
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    return jsonify({"error": "File non disponibile"}), 404

@app.route("/reset", methods=["POST"])
def reset():
    try:
        # Pulisci cartelle e status
        for folder in [UPLOAD_FOLDER, ARCHIVE_FOLDER]:
            if os.path.exists(folder):
                shutil.rmtree(folder)
                os.makedirs(folder)
        
        processing_status.clear()
        logger.info("Reset completato")
        return jsonify({"status": "reset_done"})
        
    except Exception as e:
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "OK", "timestamp": str(datetime.now())})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # Configurazione ottimizzata per Render
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
