from flask import Flask, render_template, request, jsonify
import requests
import xml.etree.ElementTree as ET
import os
import subprocess
import webbrowser
import base64
import sqlite3
import datetime
import pyautogui
from io import BytesIO
from pypdf import PdfReader

app = Flask(__name__)
DB_NAME = "jarvis_memory.db"

# --- 0. GESTION BASE DE DONNÉES ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Table des conversations
    c.execute('''CREATE TABLE IF NOT EXISTS conversations 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, timestamp DATETIME)''')
    # Table des messages
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER, role TEXT, content TEXT, timestamp DATETIME)''')
    conn.commit()
    conn.close()

init_db()

# --- 1. OUTILS (Yeux, Mains, Web) ---
def capture_ecran():
    """Prend une capture d'écran pour l'IA"""
    try:
        screenshot = pyautogui.screenshot().resize((1024, 576))
        buffered = BytesIO()
        screenshot.save(buffered, format="JPEG", quality=80)
        return base64.b64encode(buffered.getvalue()).decode("utf-8")
    except: return None

def search_web(query):
    """Cherche sur Google News (Flux RSS)"""
    print(f"--- Google News : {query} ---")
    try:
        url = f"https://news.google.com/rss/search?q={query}&hl=fr&gl=FR&ceid=FR:fr"
        root = ET.fromstring(requests.get(url, timeout=4).content)
        context = "ACTUALITÉS WEB RÉCENTES :\n"
        count = 0
        for item in root.findall('./channel/item'):
            title = item.find('title').text
            desc = item.find('description').text if item.find('description') is not None else ""
            clean = desc.replace('<b>', '').replace('</b>', '').replace('&nbsp;', ' ')
            context += f"- {title} ({clean})\n"
            count += 1
            if count >= 4: break
        return context if count > 0 else None
    except: return None

def executer_action(cmd):
    """Exécute les commandes PC"""
    cmd = cmd.lower()
    if "youtube" in cmd: webbrowser.open("https://www.youtube.com"); return "YouTube ouvert."
    if "google" in cmd: webbrowser.open("https://www.google.com"); return "Google ouvert."
    if "calc" in cmd: subprocess.Popen('calc.exe'); return "Calculatrice lancée."
    if "notepad" in cmd: subprocess.Popen('notepad.exe'); return "Bloc-notes ouvert."
    if "deezer" in cmd: webbrowser.open("https://www.deezer.com/fr/"); return "Deezer lancé."
    if "cmd" in cmd: os.system("start cmd"); return "Terminal ouvert."
    return None

# --- 2. ROUTES FLASK ---
@app.route('/')
def home(): return render_template('index.html')

@app.route('/new_chat', methods=['POST'])
def new_chat():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    title = f"Chat {datetime.datetime.now().strftime('%H:%M')}"
    c.execute("INSERT INTO conversations (title, timestamp) VALUES (?, ?)", (title, datetime.datetime.now()))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': new_id, 'title': title})

@app.route('/get_conversations')
def get_conversations():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, title FROM conversations ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

@app.route('/load_chat/<int:chat_id>')
def load_chat(chat_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id ASC", (chat_id,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{'role': r[0], 'content': r[1]} for r in rows])

# --- 3. L'INTELLIGENCE ---
@app.route('/ask', methods=['POST'])
def ask():
    user_message = request.form.get('message', '')
    # PAR DÉFAUT : LLAMA 3.1 (Rapide)
    selected_model = request.form.get('model', 'llama3.1') 
    chat_id = request.form.get('chat_id')
    use_web = request.form.get('web') == 'true'
    use_screen = request.form.get('screen') == 'true'
    
    # Sauvegarde User DB
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)", 
              (chat_id, 'user', user_message, datetime.datetime.now()))
    # Mise à jour titre conversation
    c.execute("SELECT count(*) FROM messages WHERE conversation_id = ?", (chat_id,))
    if c.fetchone()[0] <= 1:
        new_title = user_message[:30] + "..." if len(user_message) > 30 else user_message
        c.execute("UPDATE conversations SET title = ? WHERE id = ?", (new_title, chat_id))
    conn.commit()
    conn.close()

    # Logique Contexte
    image_data = None
    context_sup = ""
    
    if use_screen:
        selected_model = "llava" # Force vision
        image_data = [capture_ecran()]
        context_sup = "(L'utilisateur te montre son écran)."
    elif use_web:
        res = search_web(user_message)
        if res: context_sup = res

    # Lecture PDF
    uploaded_file = request.files.get('file')
    if uploaded_file and uploaded_file.filename.endswith('.pdf'):
        try:
            reader = PdfReader(uploaded_file)
            text = "".join([p.extract_text() for p in reader.pages])[:6000]
            context_sup += f"\nCONTENU PDF: {text}"
        except: pass

    # Lecture Profil
    try:
        with open("profil.txt", "r", encoding="utf-8") as f: profil = f.read()
    except: profil = ""

    # --- PROMPT BLINDÉ FRANÇAIS & COMMANDES ---
    system_prompt = f"Tu es J.A.R.V.I.S, l'assistant de Nolan. Profil: {profil}. "
    system_prompt += "RÈGLE : RÉPONDS TOUJOURS EN FRANÇAIS. "
    system_prompt += "Si demande d'action (deezer, youtube, calc...), réponds [CMD: app]. "
    
    if context_sup: system_prompt += f" INFO CONTEXTE : {context_sup}"

    # Prompt Utilisateur renforcé
    final_user_prompt = f"{user_message} (Réponds en Français)"

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": selected_model,
        "prompt": final_user_prompt,
        "system": system_prompt,
        "stream": False
    }
    if image_data: payload['images'] = image_data

    try:
        response = requests.post(url, json=payload)
        ai_reply = response.json()['response'].strip()

        # Exécution Action PC
        if "[CMD:" in ai_reply:
            cmd = ai_reply.split("[CMD:")[1].split("]")[0]
            res = executer_action(cmd)
            ai_reply = f"✅ {res}" if res else ai_reply

        # Sauvegarde IA DB
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("INSERT INTO messages (conversation_id, role, content, timestamp) VALUES (?, ?, ?, ?)", 
                  (chat_id, 'ai', ai_reply, datetime.datetime.now()))
        conn.commit()
        conn.close()

        return jsonify({'response': ai_reply})

    except Exception as e:
        return jsonify({'response': f"Erreur : {str(e)}"})

# Reset global (Attention ça efface tout)
@app.route('/reset', methods=['POST'])
def reset():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('DELETE FROM history') 
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)