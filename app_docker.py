import os
import logging
import pytesseract
import google.generativeai as genai
from PIL import Image
from flask import Flask, render_template, request, jsonify, send_file
from docx import Document
import fitz  # PyMuPDF
from dotenv import load_dotenv

# 🔧 Logging su console e file
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 🔐 Carica chiave API Gemini
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# ⚙️ Configurazione Flask
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
ARCHIVE_FOLDER = 'archive'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ARCHIVE_FOLDER, exist_ok=True)

# 📄 Estrazione testo da file
def extract_text_from_file(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        if ext == '.pdf':
            doc = fitz.open(file_path)
            for page in doc:
                text += page.get_text()
            doc.close()
            logger.info("Testo estratto da PDF")

        elif ext in ['.png', '.jpg', '.jpeg']:
            text = pytesseract.image_to_string(Image.open(file_path), lang='ita')
            logger.info("Testo estratto da immagine con Tesseract")

        elif ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
            logger.info("Testo estratto da TXT")

        elif ext == '.docx':
            doc = Document(file_path)
            for para in doc.paragraphs:
                text += para.text + '\n'
            logger.info("Testo estratto da DOCX")

        elif ext == '.xlsx':
            import pandas as pd
            xls = pd.read_excel(file_path, sheet_name=None)
            for sheet_name, df in xls.items():
                text += f"\n--- Foglio: {sheet_name} ---\n"
                text += df.to_string(index=False)
            logger.info("Testo estratto da XLSX")

        else:
            text = "Formato non supportato."
            logger.warning(f"Formato non supportato: {ext}")

    except Exception as e:
        text = f"Errore OCR interno: {e}"
        logger.error(f"Errore durante l'estrazione del testo: {e}")

    return text

# 🧠 Riassunto con Gemini
def generate_summary(text):
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(
            f"Riassumi questo referto medico in un linguaggio semplice. Evidenzia i valori fuori dalla norma, la diagnosi principale e le raccomandazioni del medico. Sii conciso. Il referto è:\n\n{text}"
        )
        logger.info("Riassunto generato con Gemini")
        return response.text
    except Exception as e:
        logger.error(f"Errore Gemini: {e}")
        return f"Errore nella generazione del riassunto: {e}"

# 📄 Creazione file Word
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
        logger.info("Documento Word creato")
        return file_path
    except Exception as e:
        logger.error(f"Errore nella creazione del documento Word: {e}")
        return None

# 🌐 Rotte Flask
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files or request.files['file'].filename == '':
        logger.warning("Nessun file selezionato")
        return jsonify({"error": "Nessun file selezionato"}), 400
    
    file = request.files['file']
    filename = file.filename
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    logger.info(f"File ricevuto: {filename}")

    full_text = extract_text_from_file(filepath)
    simple_summary = generate_summary(full_text)
    word_path = create_word_doc(simple_summary, full_text)

    return jsonify({
        "summary": simple_summary,
        "full_text": full_text
    })

@app.route('/download-summary')
def download_summary():
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        logger.error("File Word non trovato per il download")
        return jsonify({"error": "File non disponibile"}), 404

# 🚀 Avvio compatibile con Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
