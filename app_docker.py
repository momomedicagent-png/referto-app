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

# 🔑 Carica chiave API Gemini
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# ⚙️ Configurazione Flask
app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
ARCHIVE_FOLDER = 'archive'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(ARCHIVE_FOLDER, exist_ok=True)

# 🔧 Configurazione Tesseract per Docker
def configure_tesseract():
    """Configura Tesseract per l'ambiente Docker"""
    try:
        # Test se Tesseract è disponibile
        pytesseract.get_tesseract_version()
        logger.info("Tesseract configurato correttamente")
        
        # Verifica lingue disponibili
        langs = pytesseract.get_languages()
        logger.info(f"Lingue Tesseract disponibili: {langs}")
        
    except Exception as e:
        logger.error(f"Errore configurazione Tesseract: {e}")
        # Fallback: prova percorsi comuni in Docker
        possible_paths = [
            '/usr/bin/tesseract',
            '/usr/local/bin/tesseract'
        ]
        for path in possible_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"Tesseract trovato in: {path}")
                break

# Configura Tesseract all'avvio
configure_tesseract()

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
            # Configurazione migliorata per Tesseract
            image = Image.open(file_path)
            
            # Converti in RGB se necessario
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Configura Tesseract con parametri ottimizzati
            custom_config = r'--oem 3 --psm 6 -l ita+eng'
            text = pytesseract.image_to_string(image, config=custom_config)
            
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
        text = f"Errore nell'estrazione del testo: {str(e)}"
        logger.error(f"Errore durante l'estrazione del testo: {e}")

    return text.strip() if text else "Nessun testo estratto"

# 🧠 Riassunto con Gemini
def generate_summary(text):
    if not text or text.strip() == "":
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
        
        Usa un linguaggio semplice e accessibile.
        
        Testo del referto:
        {text}
        """
        
        response = model.generate_content(prompt)
        logger.info("Riassunto generato con Gemini")
        return response.text
    except Exception as e:
        logger.error(f"Errore Gemini: {e}")
        return f"Errore nella generazione del riassunto: {str(e)}"

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
    try:
        if 'file' not in request.files or request.files['file'].filename == '':
            logger.warning("Nessun file selezionato")
            return jsonify({"error": "Nessun file selezionato"}), 400
        
        file = request.files['file']
        filename = file.filename
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)
        logger.info(f"File ricevuto: {filename}")

        # Estrai testo
        full_text = extract_text_from_file(filepath)
        logger.info(f"Testo estratto (lunghezza: {len(full_text)})")
        
        # Genera riassunto
        simple_summary = generate_summary(full_text)
        
        # Crea documento Word
        word_path = create_word_doc(simple_summary, full_text)

        # Pulisci file temporanei
        try:
            os.remove(filepath)
        except:
            pass

        return jsonify({
            "summary": simple_summary,
            "full_text": full_text
        })
    
    except Exception as e:
        logger.error(f"Errore in upload_file: {e}")
        return jsonify({"error": f"Errore del server: {str(e)}"}), 500

@app.route('/download-summary')
def download_summary():
    file_path = os.path.join(ARCHIVE_FOLDER, "riassunto_referto.docx")
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        logger.error("File Word non trovato per il download")
        return jsonify({"error": "File non disponibile"}), 404

# 🔧 Endpoint di debug per Tesseract
@app.route('/debug/tesseract')
def debug_tesseract():
    try:
        version = pytesseract.get_tesseract_version()
        langs = pytesseract.get_languages()
        return jsonify({
            "tesseract_version": str(version),
            "available_languages": langs,
            "tesseract_cmd": pytesseract.pytesseract.tesseract_cmd
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 🚀 Avvio compatibile con Render
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Avvio app sulla porta {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
