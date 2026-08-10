import os
import sys
import socket
socket.setdefaulttimeout(10)
import urllib.parse
import re
import requests
import feedparser
import ssl
import urllib3
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client, Client
from supabase import ClientOptions
import google.generativeai as genai

# Desactivar verificación de certificados SSL globalmente para entorno local/proxy
try:
    ssl._create_default_https_context = ssl._create_unverified_context
    ssl.create_default_context = ssl._create_unverified_context
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # Forzar verify=False en todas las llamadas de la librería requests (usada por Gemini REST)
    original_send = requests.Session.send
    requests.Session.send = lambda self, req, **kw: original_send(self, req, **{**kw, 'verify': False})
except Exception:
    pass

# Cargar variables de entorno
load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY or "your-project-id" in SUPABASE_URL:
    print("Error: Configura SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en el archivo .env de WheelLifeMS.")
    sys.exit(1)

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()
LOCAL_LLM_BASE_URL = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")
LOCAL_LLM_MODEL = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5:14b")
LOCAL_LLM_API_KEY = os.environ.get("LOCAL_LLM_API_KEY", "")

LOCAL_LLM_TIMEOUT_RAW = os.environ.get("LOCAL_LLM_TIMEOUT", "600")
try:
    LOCAL_LLM_TIMEOUT = int(LOCAL_LLM_TIMEOUT_RAW) if LOCAL_LLM_TIMEOUT_RAW.lower() != "none" and int(LOCAL_LLM_TIMEOUT_RAW) > 0 else None
except ValueError:
    LOCAL_LLM_TIMEOUT = 600

# Configurar Google Gemini solo si es el proveedor seleccionado
model = None
if LLM_PROVIDER == "gemini":
    if not GEMINI_API_KEY or "tu_clave" in GEMINI_API_KEY:
        print("Error: GEMINI_API_KEY es obligatorio cuando LLM_PROVIDER es 'gemini'. Consigue una clave gratuita en Google AI Studio y colócala en el .env.")
        sys.exit(1)
    
    # Configurar Google Gemini usando REST (evita errores de gRPC SSL)
    genai.configure(api_key=GEMINI_API_KEY, transport='rest')
    model = genai.GenerativeModel('gemini-2.5-flash')

def log_status(task_name, msg_detail=""):
    """Emite un mensaje de log formateado tanto a la consola como a la base de datos Supabase para el CMS."""
    full_msg = f"{task_name}: {msg_detail}" if msg_detail else task_name
    print(f"    [STATUS] {full_msg}")
    try:
        supabase.table("system_settings").upsert({
            "key": "scraping_current_task",
            "value": full_msg[:255]
        }).execute()
    except Exception:
        pass

def check_should_abort():
    """Consulta Supabase para comprobar si el usuario solicitó detener el proceso."""
    try:
        res = supabase.table("system_settings").select("value").eq("key", "scraping_status").single().execute()
        if res.data and res.data.get("value") == "stopping":
            print("    [!] Proceso de recolección abortado a petición del usuario.")
            return True
    except Exception:
        pass
    return False

def generate_llm_content(prompt, system_instruction=None, response_mime_type=None):
    """Genera contenido de texto utilizando el proveedor de IA configurado (Gemini o Local/OpenAI)."""
    if check_should_abort():
        raise InterruptedError("Recolección cancelada por el usuario.")

    if LLM_PROVIDER == "gemini":
        if model is None:
            raise ValueError("El modelo Gemini no ha sido configurado correctamente.")
        generation_config = {}
        if response_mime_type:
            generation_config["response_mime_type"] = response_mime_type
            
        full_prompt = prompt
        if system_instruction:
            full_prompt = f"{system_instruction}\n\n{prompt}"
            
        res = model.generate_content(full_prompt, generation_config=generation_config)
        return res.text
    else:
        # Enviar petición POST HTTP a la API compatible con OpenAI / Ollama
        url = f"{LOCAL_LLM_BASE_URL.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json"
        }
        if LOCAL_LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LOCAL_LLM_API_KEY}"
            
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        data = {
            "model": LOCAL_LLM_MODEL,
            "messages": messages,
            "temperature": 0.3
        }
        if response_mime_type == "application/json":
            data["response_format"] = {"type": "json_object"}
            
        # Usar un timeout de conexión corto (10s) para fallar rápido si Ollama no está corriendo
        timeout_val = (10, LOCAL_LLM_TIMEOUT or 300)
        try:
            response = requests.post(url, headers=headers, json=data, timeout=timeout_val)
            response.raise_for_status()
            res_json = response.json()
            return res_json["choices"][0]["message"]["content"]
        except (requests.exceptions.ConnectionError, requests.exceptions.ConnectTimeout) as conn_err:
            raise ConnectionError(f"No se pudo conectar a Ollama en {LOCAL_LLM_BASE_URL}. ¿Está Ollama en ejecución?") from conn_err

def clean_markdown_text(text):
    if not text:
        return ""
    cleaned = text.strip()
    
    # 1. Quitar saludos conversacionales al inicio de la IA (ej: "¡Claro! Aquí tienes...")
    cleaned = re.sub(
        r"^(?:¡?(?:Claro|Absolutamente|Por\s+supuesto|Entendido|Excelente|Perfecto|Genial|Hola)[!,.]?\s*)*"
        r"(?:Aquí\s+(?:tienes|está|tienen|presento)|Te\s+presento|Este\s+es|He\s+(?:preparado|estructurado|redactado|limpiado|formateado|adaptado|aquí)).*?"
        r"(?::\s*(\n\s*---?\s*\n|\n\n)?|(?=\n+\s*(?:#|\*|_|-)))",
        "",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL
    )
    cleaned = cleaned.strip()

    # 2. Match ```markdown ... ```
    match_md = re.match(r"^```markdown\s+(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if match_md:
        cleaned = match_md.group(1).strip()
    else:
        # Match ``` ... ```
        match_code = re.match(r"^```\s+(.*?)\s*```$", cleaned, re.DOTALL)
        if match_code:
            cleaned = match_code.group(1).strip()
            
    # 3. Corregir énfasis que envuelven encabezados (ej: *### Título* -> ### *Título*)
    cleaned = re.sub(r"^(\*|_)+(#{1,6})\s*(.*?)\s*\1+$", r"\2 \1\3\1", cleaned, flags=re.MULTILINE)
    
    return cleaned.strip()

# Crear cliente de Supabase con permisos de admin (Bypassing SSL checks)
try:
    http_client = httpx.Client(verify=False)
    options = ClientOptions(httpx_client=http_client)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY, options=options)
except Exception as e:
    print(f"Advertencia: Usando fallback estándar para Supabase: {e}")
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 80 Intereses por defecto para inicializar la base de datos si está vacía
DEFAULT_INTERESTS = [
    # HEALTH (Salud)
    { "id": "h1", "label": "CrossFit", "area_id": "health", "emoji": "🏋️", "color_class": "bg-green-100" },
    { "id": "h2", "label": "Nutrición", "area_id": "health", "emoji": "🥗", "color_class": "bg-green-100" },
    { "id": "h3", "label": "Running", "area_id": "health", "emoji": "🏃", "color_class": "bg-green-100" },
    { "id": "h4", "label": "Yoga", "area_id": "health", "emoji": "🧘", "color_class": "bg-green-100" },
    { "id": "h5", "label": "Biohacking", "area_id": "health", "emoji": "🧬", "color_class": "bg-green-100" },
    { "id": "h6", "label": "Sueño / Recuperación", "area_id": "health", "emoji": "💤", "color_class": "bg-green-100" },
    { "id": "h7", "label": "Escalada", "area_id": "health", "emoji": "🧗", "color_class": "bg-green-100" },
    { "id": "h8", "label": "Ciclismo", "area_id": "health", "emoji": "🚴", "color_class": "bg-green-100" },
    { "id": "h9", "label": "Artes Marciales", "area_id": "health", "emoji": "🥋", "color_class": "bg-green-100" },
    { "id": "h10", "label": "Salud Mental", "area_id": "health", "emoji": "🧠", "color_class": "bg-green-100" },
    
    # MONEY (Dinero)
    { "id": "m1", "label": "Criptomonedas", "area_id": "money", "emoji": "₿", "color_class": "bg-yellow-100" },
    { "id": "m2", "label": "Bolsa e Inversión", "area_id": "money", "emoji": "📈", "color_class": "bg-yellow-100" },
    { "id": "m3", "label": "Bienes Raíces", "area_id": "money", "emoji": "🏠", "color_class": "bg-yellow-100" },
    { "id": "m4", "label": "Ahorro / Presupuesto", "area_id": "money", "emoji": "🐷", "color_class": "bg-yellow-100" },
    { "id": "m5", "label": "Finanzas Descentralizadas", "area_id": "money", "emoji": "🌐", "color_class": "bg-yellow-100" },
    { "id": "m6", "label": "Negocios Online", "area_id": "money", "emoji": "💻", "color_class": "bg-yellow-100" },
    { "id": "m7", "label": "Libertad Financiera (FIRE)", "area_id": "money", "emoji": "🔥", "color_class": "bg-yellow-100" },
    { "id": "m8", "label": "Trading", "area_id": "money", "emoji": "📉", "color_class": "bg-yellow-100" },
    { "id": "m9", "label": "Economía Conductual", "area_id": "money", "emoji": "⚖️", "color_class": "bg-yellow-100" },
    { "id": "m10", "label": "Impuestos y Fiscalidad", "area_id": "money", "emoji": "🏛️", "color_class": "bg-yellow-100" },
    
    # FAMILY (Familia)
    { "id": "fa1", "label": "Paternidad / Maternidad", "area_id": "family", "emoji": "👶", "color_class": "bg-orange-100" },
    { "id": "fa2", "label": "Eventos Familiares", "area_id": "family", "emoji": "🎉", "color_class": "bg-orange-100" },
    { "id": "fa3", "label": "Cuidado de Mayores", "area_id": "family", "emoji": "👵", "color_class": "bg-orange-100" },
    { "id": "fa4", "label": "Mascotas", "area_id": "family", "emoji": "🐕", "color_class": "bg-orange-100" },
    { "id": "fa5", "label": "Juegos de Mesa", "area_id": "family", "emoji": "🎲", "color_class": "bg-orange-100" },
    { "id": "fa6", "label": "Construcción de Comunidad", "area_id": "family", "emoji": "🤝", "color_class": "bg-orange-100" },
    { "id": "fa7", "label": "Resolución de Conflictos", "area_id": "family", "emoji": "🕊️", "color_class": "bg-orange-100" },
    { "id": "fa8", "label": "Tradiciones", "area_id": "family", "emoji": "🦃", "color_class": "bg-orange-100" },
    { "id": "fa9", "label": "Apoyo Emocional", "area_id": "family", "emoji": "🤗", "color_class": "bg-orange-100" },
    { "id": "fa10", "label": "Actividades de Grupo", "area_id": "family", "emoji": "🏕️", "color_class": "bg-orange-100" },
    
    # LOVE (Amor)
    { "id": "l1", "label": "Citas (Dating)", "area_id": "love", "emoji": "🥂", "color_class": "bg-red-100" },
    { "id": "l2", "label": "Matrimonio", "area_id": "love", "emoji": "💍", "color_class": "bg-red-100" },
    { "id": "l3", "label": "Comunicación en Pareja", "area_id": "love", "emoji": "🗣️", "color_class": "bg-red-100" },
    { "id": "l4", "label": "Lenguajes del Amor", "area_id": "love", "emoji": "💌", "color_class": "bg-red-100" },
    { "id": "l5", "label": "Sexualidad", "area_id": "love", "emoji": "🔥", "color_class": "bg-red-100" },
    { "id": "l6", "label": "Escapadas Románticas", "area_id": "love", "emoji": "✈️", "color_class": "bg-red-100" },
    { "id": "l7", "label": "Inteligencia Emocional", "area_id": "love", "emoji": "❤️‍🩹", "color_class": "bg-red-100" },
    { "id": "l8", "label": "Gestión del Tiempo 1a1", "area_id": "love", "emoji": "⏳", "color_class": "bg-red-100" },
    { "id": "l9", "label": "Regalos y Detalles", "area_id": "love", "emoji": "🎁", "color_class": "bg-red-100" },
    { "id": "l10", "label": "Proyectos en Común", "area_id": "love", "emoji": "🏗️", "color_class": "bg-red-100" },
    
    # GROWTH (Crecimiento)
    { "id": "g1", "label": "Neurociencia", "area_id": "growth", "emoji": "🧠", "color_class": "bg-indigo-100" },
    { "id": "g2", "label": "Estoicismo", "area_id": "growth", "emoji": "🏛️", "color_class": "bg-indigo-100" },
    { "id": "g3", "label": "Robótica e IA", "area_id": "growth", "emoji": "🤖", "color_class": "bg-indigo-100" },
    { "id": "g4", "label": "Lectura Rápida", "area_id": "growth", "emoji": "📖", "color_class": "bg-indigo-100" },
    { "id": "g5", "label": "Productividad", "area_id": "growth", "emoji": "⏱️", "color_class": "bg-indigo-100" },
    { "id": "g6", "label": "Espiritualidad", "area_id": "growth", "emoji": "✨", "color_class": "bg-indigo-100" },
    { "id": "g7", "label": "Oratoria / Hablar en Público", "area_id": "growth", "emoji": "🎤", "color_class": "bg-indigo-100" },
    { "id": "g8", "label": "Meditación / Mindfulness", "area_id": "growth", "emoji": "🧘‍♂️", "color_class": "bg-indigo-100" },
    { "id": "g9", "label": "Aprender Idiomas", "area_id": "growth", "emoji": "🌍", "color_class": "bg-indigo-100" },
    { "id": "g10", "label": "Escritura Creativa", "area_id": "growth", "emoji": "✍️", "color_class": "bg-indigo-100" },
    
    # FUN (Diversión)
    { "id": "fu1", "label": "Buceo", "area_id": "fun", "emoji": "🤿", "color_class": "bg-cyan-100" },
    { "id": "fu2", "label": "Videojuegos", "area_id": "fun", "emoji": "🎮", "color_class": "bg-cyan-100" },
    { "id": "fu3", "label": "Cine y Series", "area_id": "fun", "emoji": "🎬", "color_class": "bg-cyan-100" },
    { "id": "fu4", "label": "Fotografía", "area_id": "fun", "emoji": "📸", "color_class": "bg-cyan-100" },
    { "id": "fu5", "label": "Cocina / Gastronomía", "area_id": "fun", "emoji": "🍳", "color_class": "bg-cyan-100" },
    { "id": "fu6", "label": "Música", "area_id": "fun", "emoji": "🎸", "color_class": "bg-cyan-100" },
    { "id": "fu7", "label": "Viajar", "area_id": "fun", "emoji": "🎒", "color_class": "bg-cyan-100" },
    { "id": "fu8", "label": "Arte y Pintura", "area_id": "fun", "emoji": "🎨", "color_class": "bg-cyan-100" },
    { "id": "fu9", "label": "Astronomía", "area_id": "fun", "emoji": "🔭", "color_class": "bg-cyan-100" },
    { "id": "fu10", "label": "Coleccionismo", "area_id": "fun", "emoji": "🪙", "color_class": "bg-cyan-100" },
    
    # CAREER (Carrera)
    { "id": "c1", "label": "Programación", "area_id": "career", "emoji": "👨‍💻", "color_class": "bg-blue-100" },
    { "id": "c2", "label": "Liderazgo y Management", "area_id": "career", "emoji": "📈", "color_class": "bg-blue-100" },
    { "id": "c3", "label": "Marketing Digital", "area_id": "career", "emoji": "📱", "color_class": "bg-blue-100" },
    { "id": "c4", "label": "Diseño UX/UI", "area_id": "career", "emoji": "🖌️", "color_class": "bg-blue-100" },
    { "id": "c5", "label": "Emprendimiento", "area_id": "career", "emoji": "💡", "color_class": "bg-blue-100" },
    { "id": "c6", "label": "Networking", "area_id": "career", "emoji": "🤝", "color_class": "bg-blue-100" },
    { "id": "c7", "label": "Gestión de Proyectos", "area_id": "career", "emoji": "📊", "color_class": "bg-blue-100" },
    { "id": "c8", "label": "Ventas", "area_id": "career", "emoji": "🎯", "color_class": "bg-blue-100" },
    { "id": "c9", "label": "Data Science", "area_id": "career", "emoji": "📊", "color_class": "bg-blue-100" },
    { "id": "c10", "label": "Derecho y Leyes", "area_id": "career", "emoji": "⚖️", "color_class": "bg-blue-100" },
    
    # ENVIRONMENT (Entorno)
    { "id": "e1", "label": "Minimalismo", "area_id": "environment", "emoji": "🧊", "color_class": "bg-emerald-100" },
    { "id": "e2", "label": "Diseño de Interiores", "area_id": "environment", "emoji": "🛋️", "color_class": "bg-emerald-100" },
    { "id": "e3", "label": "Jardinería", "area_id": "environment", "emoji": "🌿", "color_class": "bg-emerald-100" },
    { "id": "e4", "label": "Sostenibilidad", "area_id": "environment", "emoji": "♻️", "color_class": "bg-emerald-100" },
    { "id": "e5", "label": "Marie Kondo (Orden)", "area_id": "environment", "emoji": "🧹", "color_class": "bg-emerald-100" },
    { "id": "e6", "label": "Domótica", "area_id": "environment", "emoji": "📱", "color_class": "bg-emerald-100" },
    { "id": "e7", "label": "Bricolaje (DIY)", "area_id": "environment", "emoji": "🔨", "color_class": "bg-emerald-100" },
    { "id": "e8", "label": "Movilidad Urbana", "area_id": "environment", "emoji": "🚲", "color_class": "bg-emerald-100" },
    { "id": "e9", "label": "Naturaleza", "area_id": "environment", "emoji": "🌲", "color_class": "bg-emerald-100" },
    { "id": "e10", "label": "Arquitectura", "area_id": "environment", "emoji": "🏙️", "color_class": "bg-emerald-100" }
]

INTEREST_TRANSLATIONS = {
    # HEALTH (Salud)
    "h1": "CrossFit", "h2": "Nutrition", "h3": "Running", "h4": "Yoga", "h5": "Biohacking",
    "h6": "Sleep Recovery", "h7": "Climbing", "h8": "Cycling", "h9": "Martial Arts", "h10": "Mental Health",
    # MONEY (Dinero)
    "m1": "Cryptocurrencies", "m2": "Stock Investing", "m3": "Real Estate", "m4": "Saving Budgeting",
    "m5": "Decentralized Finance", "m6": "Online Business", "m7": "Financial Independence", "m8": "Trading",
    "m9": "Behavioral Economics", "m10": "Taxes",
    # FAMILY (Familia)
    "fa1": "Parenting", "fa2": "Family Events", "fa3": "Elderly Care", "fa4": "Pets", "fa5": "Board Games",
    "fa6": "Community Building", "fa7": "Conflict Resolution", "fa8": "Traditions", "fa9": "Emotional Support", "fa10": "Group Activities",
    # LOVE (Amor)
    "l1": "Dating", "l2": "Marriage", "l3": "Relationship Communication", "l4": "Love Languages",
    "l5": "Sexuality", "l6": "Romantic Getaways", "l7": "Emotional Intelligence", "l8": "1-on-1 Time Management",
    "l9": "Gifts and Details", "l10": "Shared Projects",
    # GROWTH (Crecimiento)
    "g1": "Neuroscience", "g2": "Stoicism", "g3": "Robotics and AI", "g4": "Speed Reading", "g5": "Productivity",
    "g6": "Spirituality", "g7": "Public Speaking", "g8": "Meditation Mindfulness", "g9": "Language Learning", "g10": "Creative Writing",
    # FUN (Diversión)
    "fu1": "Scuba Diving", "fu2": "Video Games", "fu3": "Movies and Series", "fu4": "Photography",
    "fu5": "Cooking Gastronomy", "fu6": "Music", "fu7": "Travel", "fu8": "Art Painting", "fu9": "Astronomy", "fu10": "Collecting",
    # CAREER (Carrera)
    "c1": "Programming", "c2": "Leadership and Management", "c3": "Digital Marketing", "c4": "UX/UI Design",
    "c5": "Entrepreneurship", "c6": "Networking", "c7": "Project Management", "c8": "Sales", "c9": "Data Science", "c10": "Law and Justice",
    # ENVIRONMENT (Entorno)
    "e1": "Minimalism", "e2": "Interior Design", "e3": "Gardening", "e4": "Sustainability", "e5": "Marie Kondo Tidying",
    "e6": "Home Automation", "e7": "DIY", "e8": "Urban Mobility", "e9": "Nature", "e10": "Architecture"
}

RSS_FEEDS_ES = {
    "health": [
        "https://www.cuerpomente.com/rss",
        "https://www.hsnstore.com/blog/feed/"
    ],
    "money": [
        "https://www.estrategiasdeinversion.com/rss",
        "https://www.bolsamania.com/rss/portada.xml"
    ],
    "family": [
        "https://www.serpadres.es/rss"
    ],
    "love": [
        "https://lamenteesmaravillosa.com/feed/"
    ],
    "growth": [
        "https://habitualmente.com/feed/",
        "https://lamenteesmaravillosa.com/feed/"
    ],
    "fun": [
        "https://www.vidaextra.com/feed",
        "https://www.espinof.com/feed"
    ],
    "career": [
        "https://www.genbeta.com/feed"
    ],
    "environment": [
        "https://ecoinventos.com/feed/",
        "https://www.jardineriaon.com/feed/"
    ]
}

RSS_FEEDS_EN = {
    "health": [
        "https://medicalxpress.com/rss-feed/",
        "https://wellnessmama.com/feed/"
    ],
    "money": [
        "https://www.moneytalksnews.com/feed/",
        "https://investorjunkie.com/feed/"
    ],
    "family": [
        "https://wellnessmama.com/feed/"
    ],
    "love": [
        "https://zenhabits.net/feed/"
    ],
    "growth": [
        "https://dailystoic.com/feed/",
        "https://zenhabits.net/feed/"
    ],
    "fun": [
        "https://www.escapistmagazine.com/feed/"
    ],
    "career": [
        "https://hnrss.github.io/active"
    ],
    "environment": [
        "https://www.treehugger.com/feeds/rss"
    ]
}


def init_interests():
    print("Verificando tabla 'interests'...")
    try:
        res = supabase.table("interests").select("count", count="exact").limit(1).execute()
        if res.count == 0:
            print("Poblando tabla 'interests' con 80 intereses por defecto...")
            supabase.table("interests").insert(DEFAULT_INTERESTS).execute()
            print("¡Intereses insertados con éxito!")
        else:
            print(f"Tabla 'interests' ya contiene {res.count} registros.")
    except Exception as e:
        print(f"Error al verificar/inicializar intereses: {e}")

def translate_interest_label(interest_id, label):
    """Gets the English translation for the interest, using static lookup or Gemini fallback."""
    if interest_id in INTEREST_TRANSLATIONS:
        return INTEREST_TRANSLATIONS[interest_id]
        
    print(f"    Interest ID '{interest_id}' not found in static translations. Querying Gemini fallback...")
    try:
        prompt = f"Translate the following Spanish topic/interest label to a single standard English keyword or short term used in blogs/articles. Return only the translated term and nothing else: '{label}'"
        res_text = generate_llm_content(prompt)
        translated = res_text.strip().lower()
        translated = re.sub(r'[^a-z0-9\s-]', '', translated).strip()
        if translated:
            return translated
    except Exception as e:
        print(f"    Error translating with Gemini: {e}")
    return label.lower()

def translate_title_to_spanish(title):
    """Translates the article title to Spanish if it is in English/other language."""
    try:
        prompt = f"Translate the following article title to natural and attractive Spanish for a blog. Return ONLY the translated title, no quotes, no explanations, no prefix. If the title is already in Spanish, return it exactly as is: '{title}'"
        res_text = generate_llm_content(prompt)
        translated = res_text.strip().replace('"', '').replace("'", "")
        translated = re.sub(r'^(Traducción|Spanish):\s*', '', translated, flags=re.IGNORECASE)
        print(f"    Título de artículo traducido a español: '{translated}'")
        return translated
    except Exception as e:
        print(f"    Error translating title to Spanish: {e}")
        return title

def translate_book_title_to_spanish(title):
    """Traduce o busca el título oficial de un libro en español."""
    try:
        prompt = f"Dime el título oficial en español (o la traducción al español más común si no existe edición oficial) del libro: '{title}'. Devuelve ÚNICAMENTE el título en español, sin comillas, sin explicaciones ni texto adicional."
        res_text = generate_llm_content(prompt)
        translated = res_text.strip().replace('"', '').replace("'", "")
        print(f"    Título del libro traducido a español: '{translated}'")
        return translated
    except Exception as e:
        print(f"    Error traduciendo título del libro a español: {e}")
        return title

# Global para capturar errores de cuota o scraping
last_error_msg = ""

def fetch_book_summaries(interest_id, label):
    """Busca libros usando Google Books API y genera un resumen estructurado con la IA configurada."""
    global last_error_msg
    provider_name = "Gemini" if LLM_PROVIDER == "gemini" else "IA Local"
    print(f"  Buscando libros sobre: {label}")
    try:
        # 1. Consultar títulos ya existentes en Supabase para evitar duplicados
        existing_books = []
        try:
            res_exist = supabase.table("books").select("title").eq("interest_id", interest_id).execute()
            if res_exist.data:
                existing_books = [b["title"] for b in res_exist.data]
                print(f"    Libros ya existentes para este interés: {existing_books}")
        except Exception as e:
            print(f"    Advertencia al leer libros existentes de Supabase: {e}")

        # Crear cláusula de exclusión si hay existentes
        exclude_clause = ""
        if existing_books:
            exclude_clause = f"\nEvita recomendar o sugerir cualquiera de los siguientes libros que ya están en la base de datos: {', '.join(existing_books)}."

        # 2. Preguntar al LLM por el libro definitivo/más influyente para ese interés
        recommend_prompt = f"""
        Dime el título exacto en español (o la traducción/edición oficial en español si existe) y el autor del libro de desarrollo personal, negocios, salud, finanzas, mentalidad, ciencia o temáticas afines más influyente, respetado y de mayor impacto educativo/práctico en todo el mundo sobre el tema: "{label}".{exclude_clause}
        Responde únicamente en una sola línea con el formato exacto: Título del Libro en Español | Nombre del Autor
        Ejemplos:
        Si el tema es Criptomonedas, responde: El patrón Bitcoin | Saifedean Ammous
        Si el tema es Liderazgo, responde: Empieza con el porqué | Simon Sinek
        Si el tema es Productividad, responde: Hábitos atómicos | James Clear
        Si el tema es Estoicismo, responde: Meditaciones | Marco Aurelio
        No agregues explicaciones, ni viñetas, ni texto adicional.
        """
        
        suggested_title = label
        suggested_author = ""
        query = label
        
        try:
            res_text = generate_llm_content(recommend_prompt)
            recommendation = res_text.strip()
            
            if "|" in recommendation:
                parts = [x.strip() for x in recommendation.split("|", 1)]
                suggested_title = parts[0].replace("**", "").replace("*", "")
                suggested_author = parts[1].replace("**", "").replace("*", "")
                query = f"{suggested_title} {suggested_author}"
                print(f"    {provider_name} recomendó el libro: '{suggested_title}' de '{suggested_author}'")
            else:
                log_status(f"Libro para {label}", f"IA recomendó '{suggested_title}' de '{suggested_author}'")
        except (ConnectionError, InterruptedError) as fatal_err:
            raise fatal_err
        except Exception as e:
            print(f"    Error recomendando libro con {provider_name}: {e}. Usando etiqueta.")

        # 3. Consultar metadatos (Google Books API con fallbacks a Open Library e iTunes)
        title = suggested_title
        authors = suggested_author or "Autor Desconocido"
        cover_url = None
        book_url = ""

        # --- A. INTENTAR GOOGLE BOOKS API ---
        try:
            url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(query)}&maxResults=1"
            res = requests.get(url, timeout=10, verify=False)
            if res.status_code == 200:
                data = res.json()
                items = data.get("items", [])
                if items:
                    volume_info = items[0].get("volumeInfo", {})
                    title = volume_info.get("title", suggested_title)
                    authors_list = volume_info.get("authors", [])
                    authors = ", ".join(authors_list) if authors_list else (suggested_author or "Autor Desconocido")
                    book_url = volume_info.get("infoLink", "")
                    image_links = volume_info.get("imageLinks", {})
                    if image_links:
                        cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
                        if cover_url and cover_url.startswith("http://"):
                            cover_url = cover_url.replace("http://", "https://")
                    print(f"    [+] Metadatos obtenidos de Google Books: {title} (Portada: {'Sí' if cover_url else 'No'})")
            else:
                log_status(f"Libro ({title})", f"Google Books 429 -> Obteniendo datos desde Open Library...")
        except Exception as api_err:
            log_status(f"Libro ({title})", "Consultando fuentes alternativas de portada...")

        # --- B. FALLBACK A OPEN LIBRARY (Si no tenemos portada o enlace) ---
        if not cover_url or not book_url:
            log_status(f"Libro ({title})", "Buscando portada en Open Library / iTunes...")
            try:
                ol_url = f"https://openlibrary.org/search.json?title={urllib.parse.quote(title)}&limit=1"
                ol_res = requests.get(ol_url, timeout=10, verify=False)
                if ol_res.status_code == 200:
                    ol_data = ol_res.json()
                    docs = ol_data.get("docs", [])
                    if docs:
                        doc = docs[0]
                        if not book_url:
                            key = doc.get("key")
                            if key:
                                book_url = f"https://openlibrary.org{key}"
                        if not cover_url:
                            cover_i = doc.get("cover_i")
                            if cover_i:
                                cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
            except Exception as ol_err:
                pass

        # --- C. FALLBACK A ITUNES SEARCH API (Si aún no tenemos portada) ---
        if not cover_url:
            try:
                itunes_url = f"https://itunes.apple.com/search?term={urllib.parse.quote(query)}&limit=1"
                itunes_res = requests.get(itunes_url, timeout=10, verify=False)
                if itunes_res.status_code == 200:
                    itunes_data = itunes_res.json()
                    results = itunes_data.get("results", [])
                    if results:
                        volume = results[0]
                        cover_candidate = volume.get("artworkUrl100") or volume.get("artworkUrl60")
                        if cover_candidate:
                            cover_url = cover_candidate.replace("100x100bb", "600x600bb").replace("100x100", "600x600")
            except Exception:
                pass

        # Asegurarnos de que el título final esté en español
        title = translate_book_title_to_spanish(title)

        # Evitar duplicados por URL
        if book_url:
            existing = supabase.table("books").select("id").eq("url", book_url).execute()
            if existing.data:
                print(f"    [=] El libro ya existe en la BD por URL: {title}")
                return
        
        log_status(f"Generando IA para '{title}'", "Escribiendo modelos mentales y plan de acción...")
        
        # 4. Prompt de extracción de valor ultra-premium estructurado y profundo (Blinkist / Shortform style)
        prompt = f"""
        Eres un analista de ideas senior, curador de contenido premium y experto en destilar conocimiento exhaustivo y de alto impacto de libros influyentes.
        Escribe un resumen profundo, detallado, extenso y sumamente valioso del libro "{title}" escrito por {authors} en español.

        El objetivo es entregar un valor extraordinario (equivalente a una lectura profunda de 8 a 12 minutos) para que el usuario domine los conceptos fundamentales, las implicaciones prácticas y las herramientas del libro.
        No resumas superficialmente: desarrolla ampliamente los puntos clave, aporta ejemplos ricos en matices y explicaciones detalladas.

        Pautas de formato críticas para el rendering en la app móvil:
        - NO utilices listas de viñetas (bullet points con asterisco o guión) si la entrada comienza con un título o etiqueta en negrita. Usa líneas limpias de texto donde la etiqueta en negrita sirva como inicio directo de párrafo.
        - DEJA SIEMPRE al menos una línea en blanco (vacía) antes y después de cualquier sección, lista numerada, cita en bloque o línea divisoria (---).

        Debes utilizar exactamente la siguiente estructura en Markdown:

        # {title}
        Por {authors}

        ---

        ## ⚡ RESUMEN EJECUTIVO (TL;DR)

        **La Gran Idea**: Destila el mensaje fundamental del libro en una síntesis conceptual potente y memorable.

        **El Mito Derribado**: Explica con profundidad la creencia popular errónea que el libro destruye mediante evidencia, datos o lógica sólida, y por qué esa creencia saboteaba al lector.

        **Perfil Objetivo**: Define con precisión el perfil del lector al que este libro puede transformar su carrera o vida.

        ## 🧠 MODELOS MENTALES Y CONCEPTOS CLAVE

        Desarrolla obligatoriamente entre 4 y 6 modelos mentales o lecciones fundamentales del libro. Cada modelo mental debe ser extenso y detallado:

        ### 1. Nombre del Modelo Mental / Concepto Clave

        **La Lógica Teórica**: Explica minuciosamente el principio, su lógica subyacente, cómo opera en la psicología o en el mercado y por qué es crucial entenderlo.

        **Ejemplo Práctico Real**: Una analogía cotidiana detallada o un caso de estudio real ampliamente explicado que ilustre cómo se aplica este principio.

        ### 2. Nombre del Modelo Mental 2

        **La Lógica Teórica**: Explica en detalle...

        **Ejemplo Práctico Real**: Ejemplo real...

        (Desarrolla al menos 4 o 5 modelos mentales completos de forma sustancial).

        ## 🔄 TRANSFORMACIÓN DE MENTALIDAD

        Escribe 3 a 4 contrastes profundos de mentalidad contrapesando la vieja forma de pensar frente al nuevo paradigma del libro:

        **Enfoque Tradicional**: Vieja creencia o hábito ineficiente...
        **Nuevo Paradigma**: Principio transformador del libro...

        ## 🎯 PLAN DE ACCIÓN PASO A PASO
        1. **Acción Inmediata (Hoy)**: Paso concreto y aplicable en 5 minutos.
        2. **Acción Semanal**: Hábito o ejercicio a implementar durante los próximos 7 días.
        3. **Evaluación de Progreso**: Métrica o criterio para evaluar la mejora.

        ## 💡 PREGUNTA DE REFLEXIÓN INTROSPECTIVA
        > Escribe una pregunta incómoda, profunda y desafiante diseñada para que el lector evalúe honestamente su situación actual bajo el prisma del libro.

        IMPORTANTE: Devuelve ÚNICAMENTE el texto en formato Markdown de acuerdo a la estructura anterior. Empieza directamente con el título del libro en H1 (# {title}).
        """
        
        print(f"    Generando resumen estructurado con IA para: {title}")
        ai_response_text = generate_llm_content(prompt)
        summary_markdown = clean_markdown_text(ai_response_text)
        
        # Calcular tiempo de lectura estimado (ritmo reflexivo: ~160 palabras/min, rango objetivo: 5 a 15 min)
        words_count = len(summary_markdown.split())
        read_time_val = max(5, min(15, round(words_count / 160)))
        read_time = f"{read_time_val} min"
        
        # Generar vista previa para la tarjeta (snippet) - Extrae "La Gran Idea" del Markdown
        snippet = f"Lectura: {read_time} • Resumen estructurado con ideas clave y plan de acción."
        try:
            # Importamos re localmente por si acaso, aunque ya está al inicio del archivo
            import re
            match_idea = re.search(r"\*\*La Gran Idea\*\*\s*:\s*(.*?)(?:\n|$)", summary_markdown)
            if match_idea:
                grand_idea = match_idea.group(1).strip()
                grand_idea = re.sub(r"^\[|\]$", "", grand_idea).strip() # Limpiar corchetes
                if len(grand_idea) > 160:
                    grand_idea = grand_idea[:157] + "..."
                snippet = f"Lectura: {read_time} • {grand_idea}"
        except Exception as e_regex:
            print(f"    Advertencia al extraer La Gran Idea para el snippet: {e_regex}")
        book_data = {
            "interest_id": interest_id,
            "title": title,
            "author": authors,
            "snippet": snippet,
            "read_time": read_time,
            "cover_url": cover_url,
            "url": book_url,
            "summary": summary_markdown,
            "collected": False
        }
        
        supabase.table("books").insert(book_data).execute()
        print(f"    [+] Libro guardado con resumen IA: {title}")
            
    except (ConnectionError, InterruptedError) as fatal_err:
        raise fatal_err
    except Exception as e:
        last_error_msg = str(e)
        print(f"    Error buscando/resumiendo libros para {label}: {e}")

def fetch_wikipedia_people(interest_id, label):
    """Busca personajes en Wikipedia y genera una biografía narrativa apasionante con Gemini AI."""
    print(f"  Buscando personas relevantes sobre: {label}")
    try:
        # 1. Consultar nombres ya existentes en Supabase para evitar duplicados
        existing_people = []
        try:
            res_exist = supabase.table("people").select("name").eq("interest_id", interest_id).execute()
            if res_exist.data:
                existing_people = [p["name"] for p in res_exist.data]
                print(f"    Personas ya existentes para este interés: {existing_people}")
        except Exception as e:
            print(f"    Advertencia al leer personas existentes de Supabase: {e}")

        # 2. Reintentar hasta 3 veces con la IA si Wikipedia no devuelve una persona válida o devuelve un término ambiguo
        wiki_headers = {
            "User-Agent": "WheelLifeApp/1.0 (https://wheellife.app; contact@wheellife.app) Python/requests"
        }
        
        top_page = None
        title = None
        page_id = None
        wiki_url = None

        for attempt in range(1, 4):
            exclude_str = f" Evita estas personas ya intentadas/existentes: {', '.join(existing_people)}." if existing_people else ""
            recommend_prompt = f"""
            Dime el nombre de un SER HUMANO real (persona histórica o contemporánea importante) profundamente asociado al tema: "{label}".{exclude_str}
            
            REGLAS STRICTAS:
            1. Debe ser el NOMBRE PROPIO Y APELLIDO de una PERSONA REAL FÍSICA (ejemplo: Steve Jobs, Sara Blakely, Séneca).
            2. NUNCA respondas con programas de TV, empresas, marcas o conceptos (NADA de "Shark Tank", "Apple", "Google", "Estoicismo").
            3. Responde únicamente con el nombre exacto de la persona en una sola línea.
            """
            
            log_status(f"Biografía para {label}", f"Pidiendo personaje real a la IA (Intento {attempt}/3)...")
            res_text = generate_llm_content(recommend_prompt)
            recommended_name = clean_markdown_text(res_text).replace('"', '').replace("'", "").strip()

            if not recommended_name or len(recommended_name) < 2 or len(recommended_name) > 60:
                continue

            # Buscar en Wikipedia
            search_url = f"https://es.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(recommended_name)}&format=json&utf8=1"
            res = requests.get(search_url, timeout=10, verify=False, headers=wiki_headers)
            if res.status_code == 200:
                search_results = res.json().get("query", {}).get("search", [])
                if search_results:
                    top_page = search_results[0]
                    cand_title = top_page.get("title")
                    cand_url = f"https://es.wikipedia.org/wiki/{urllib.parse.quote(cand_title)}"
                    
                    # Comprobar que no exista ya en la BD
                    existing = supabase.table("people").select("id").eq("url", cand_url).execute()
                    if not existing.data:
                        title = cand_title
                        page_id = top_page.get("pageid")
                        wiki_url = cand_url
                        existing_people.append(recommended_name)
                        break
                    else:
                        print(f"    [=] Biografía ya existe: {cand_title}. Probando alternativa...")
                        existing_people.append(recommended_name)

        if not title or not page_id:
            log_status(f"Biografía para {label}", "Wikipedia no devolvió una figura inédita. Generando perfil biográfico guiado por IA...")
            title = recommended_name if 'recommended_name' in locals() and recommended_name else label
            wiki_url = f"https://es.wikipedia.org/wiki/{urllib.parse.quote(title)}"

        # 4. Descargar extracto largo
        detail_url = f"https://es.wikipedia.org/w/api.php?action=query&prop=extracts|pageimages&exintro=0&explaintext=1&piprop=thumbnail&pithumbsize=300&titles={urllib.parse.quote(title)}&format=json"
        res_detail = requests.get(detail_url, timeout=10, verify=False, headers=wiki_headers)
        if res_detail.status_code != 200:
            print(f"    [-] Wikipedia rechazó el detalle con status {res_detail.status_code} para: {title}")
            return
            
        pages = res_detail.json().get("query", {}).get("pages", {})
        page_info = pages.get(str(page_id)) or list(pages.values())[0]
        
        raw_text = page_info.get("extract", "")
        # Cortar a 6000 caracteres para no exceder tokens
        wikipedia_context = raw_text[:6000] if raw_text else top_page.get("snippet", "")
        
        log_status(f"Biografía para {label}", f"Seleccionado: '{title}' -> Obteniendo registros históricos de Wikipedia...")
        
        thumbnail = page_info.get("thumbnail", {})
        image_url = thumbnail.get("source") if isinstance(thumbnail, dict) else None
        
        log_status(f"Redactando biografía de '{title}'", "Sintetizando momentos clave y 3 reglas de oro...")
        prompt = f"""
        Eres un historiador, biógrafo experto y maestro del storytelling. Escribe una biografía en español profunda, detallada, apasionante e inspiradora de "{title}" basada en el siguiente contexto histórico:

        --- CONTEXTO HISTÓRICO DE WIKIPEDIA ---
        {wikipedia_context}
        ----------------------------------------

        Instrucciones de redacción y profundidad:
        1. La biografía debe tener cuerpo, riqueza narrativa y sustancia (equivalente a 5-8 minutos de lectura). Desarrolla los pasajes clave, sus dilemas internos y cómo superó los obstáculos sin resumir de forma precipitada.
        2. Mantener pautas de renderizado en app móvil: Deja siempre saltos de línea vacíos antes y después de listas de viñetas, citas en bloque y divisores (---).

        Estructura en Markdown obligatoria:

        # {title}: [Subtítulo Épico que Defina su Filosofía o Impacto]

        ---

        ## 🔥 LA CRISIS Y EL PUNTO DE INFLEXIÓN
        [Escribe una narrativa apasionante de 3 a 4 párrafos extensos enfocado en su mayor crisis, sus dilemas humanos, momentos de mayor vulnerabilidad y la revelación decisiva donde cambió su destino y el de su entorno.]

        ## 🏛️ EL LEGADO Y LA FILOSOFÍA DE VIDA
        [Analiza en detalle de 3 párrafos su filosofía de pensamiento, su visión del mundo, sus grandes contribuciones y por qué su vida transformó la historia de su disciplina.]

        ## ⭐ 3 REGLAS DE ORO DE {title} PARA TU VIDA
        [Destila los 3 principios o valores de mentalidad más potentes del personaje con explicaciones detalladas que el lector pueda aplicar a su propia vida]:

        1. **[Regla/Principio 1]**: [Explicación detallada y profunda de cómo aplicar esta mentalidad].
        2. **[Regla/Principio 2]**: [Explicación detallada...].
        3. **[Regla/Principio 3]**: [Explicación detallada...].

        ---
        *Créditos: Basado en registros históricos y contenido público de Wikipedia.*
        """
        
        print(f"    Redactando biografía apasionante con la IA para: {title}")
        ai_response_text = generate_llm_content(prompt)
        biography_markdown = clean_markdown_text(ai_response_text)
        
        # Calcular tiempo de lectura estimado (ritmo humano: ~160 palabras/min, rango objetivo: 4 a 10 min)
        words_count = len(biography_markdown.split())
        read_time_val = max(4, min(10, round(words_count / 160)))
        read_time = f"{read_time_val} min"
        
        # Snippet corto de tarjeta
        snippet_preview = f"Lectura: {read_time} • La apasionante historia de {title} y su impacto en {label}. Descubre sus luchas y su legado."
        
        people_data = {
            "interest_id": interest_id,
            "name": title,
            "snippet": snippet_preview,
            "read_time": read_time,
            "image_url": image_url,
            "url": wiki_url,
            "biography": biography_markdown,
            "collected": False
        }
        supabase.table("people").insert(people_data).execute()
        print(f"    [+] Biografía IA guardada con éxito: {title}")
        
    except (ConnectionError, InterruptedError) as fatal_err:
        raise fatal_err
    except Exception as e:
        global last_error_msg
        last_error_msg = str(e)
        print(f"    Error buscando/resumiendo biografía de persona para {label}: {e}")

def check_keyword_match(text, label):
    """Checks if the label or any of its main keywords exist in the text, ignoring stopwords and accents."""
    import unicodedata
    
    def normalize_str(s):
        s = s.lower()
        return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')
        
    normalized_text = normalize_str(text)
    normalized_label = normalize_str(label)
    
    # 1. Direct match of the full label
    if normalized_label in normalized_text:
        return True
        
    # 2. Split into words and filter out stopwords
    stopwords = {"and", "or", "y", "e", "de", "con", "for", "with", "the", "a", "an", "on", "in", "at", "to", "la", "el", "los", "las", "un", "una", "unos", "unas"}
    words = [re.sub(r'[^a-z0-9]', '', w) for w in normalized_label.split()]
    key_words = [w for w in words if len(w) > 3 and w not in stopwords]
    
    # Check if any key word is matched
    if key_words and any(kw in normalized_text for kw in key_words):
        return True
        
    return False

def fetch_rss_and_devto_articles(interest_id, label, area_id, limit=1):
    """Busca artículos en español e inglés, extrae el texto del cuerpo HTML, y genera un formato Markdown limpio con Gemini AI."""
    print(f"  Buscando artículos sobre: {label}")
    articles_found = 0
    article_candidates = []
    
    label_en = translate_interest_label(interest_id, label)
    print(f"    Término de búsqueda: Español='{label}', Inglés='{label_en}'")

    # 1. Dev.to (usamos tag en inglés)
    if area_id == "career" or label.lower() in ["programación", "ia", "robotica", "diseño ux/ui", "marketing digital", "networking", "desarrollo"]:
        try:
            clean_tag = label_en.lower()
            if "programming" in clean_tag:
                clean_tag = "programming"
            elif "leadership" in clean_tag:
                clean_tag = "leadership"
            elif "marketing" in clean_tag:
                clean_tag = "marketing"
            elif "ux" in clean_tag:
                clean_tag = "ux"
            elif "entrepreneurship" in clean_tag:
                clean_tag = "startup"
            elif "project management" in clean_tag:
                clean_tag = "projectmanagement"
            elif "data science" in clean_tag:
                clean_tag = "datascience"
            elif "law" in clean_tag:
                clean_tag = "law"
            else:
                clean_tag = clean_tag.replace(" ", "").replace("-", "")
                
            url = f"https://dev.to/api/articles?tag={urllib.parse.quote(clean_tag)}&per_page=4"
            res = requests.get(url, timeout=10, verify=False)
            if res.status_code == 200:
                for item in res.json():
                    art_url = item.get("url")
                    title = item.get("title")
                    
                    existing = supabase.table("articles").select("id").eq("url", art_url).execute()
                    if existing.data:
                        continue
                    
                    article_candidates.append({
                        "url": art_url,
                        "title": title,
                        "cover": item.get("cover_image") or item.get("social_image")
                    })
        except Exception as e:
            print(f"    Error consultando Dev.to para {label}: {e}")

    # 2. Google News RSS (Búsqueda principal por palabra clave)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    # 2.1 Google News RSS en Español
    if len(article_candidates) < 3:
        try:
            encoded_label = urllib.parse.quote(label)
            gnews_url_es = f"https://news.google.com/rss/search?q={encoded_label}&hl=es-ES&gl=ES&ceid=ES:es"
            print(f"    Consultando Google News RSS (ES) para: {label}")
            res = requests.get(gnews_url_es, timeout=10, headers=headers, verify=False)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                for entry in feed.entries[:8]:
                    title = entry.get("title", "")
                    link = entry.get("link")
                    
                    if any(c["url"] == link for c in article_candidates):
                        continue
                    existing = supabase.table("articles").select("id").eq("url", link).execute()
                    if existing.data:
                        continue
                        
                    article_candidates.append({
                        "url": link,
                        "title": title,
                        "cover": None
                    })
                    if len(article_candidates) >= 3:
                        break
        except Exception as e:
            print(f"    Error buscando en Google News RSS (ES) para {label}: {e}")

    # 2.2 Google News RSS en Inglés
    if len(article_candidates) < 3:
        try:
            encoded_label_en = urllib.parse.quote(label_en)
            gnews_url_en = f"https://news.google.com/rss/search?q={encoded_label_en}&hl=en-US&gl=US&ceid=US:en"
            print(f"    Consultando Google News RSS (EN) para: {label_en}")
            res = requests.get(gnews_url_en, timeout=10, headers=headers, verify=False)
            if res.status_code == 200:
                feed = feedparser.parse(res.content)
                for entry in feed.entries[:8]:
                    title = entry.get("title", "")
                    link = entry.get("link")
                    
                    if any(c["url"] == link for c in article_candidates):
                        continue
                    existing = supabase.table("articles").select("id").eq("url", link).execute()
                    if existing.data:
                        continue
                        
                    article_candidates.append({
                        "url": link,
                        "title": title,
                        "cover": None
                    })
                    if len(article_candidates) >= 3:
                        break
        except Exception as e:
            print(f"    Error buscando en Google News RSS (EN) para {label_en}: {e}")

    # 3. Fallback: RSS - Fuentes estáticas por área (sólo si no encontramos suficientes artículos con Google News)
    if len(article_candidates) < 3:
        print(f"    Mecanismo de Fallback: Consultando feeds estáticos para el área '{area_id}'")
        
        # Feeds estáticos en Español
        feeds_es = RSS_FEEDS_ES.get(area_id, [])
        for feed_url in feeds_es:
            if len(article_candidates) >= 3:
                break
            try:
                res = requests.get(feed_url, timeout=10, headers=headers, verify=False)
                if res.status_code == 200:
                    feed = feedparser.parse(res.content)
                    for entry in feed.entries[:8]:
                        title = entry.get("title", "")
                        link = entry.get("link")
                        
                        if any(c["url"] == link for c in article_candidates):
                            continue
                        text_to_check = (title + " " + entry.get("summary", "")).lower()
                        if check_keyword_match(text_to_check, label):
                            existing = supabase.table("articles").select("id").eq("url", link).execute()
                            if existing.data:
                                continue
                            
                            article_candidates.append({
                                "url": link,
                                "title": title,
                                "cover": None
                            })
                            if len(article_candidates) >= 3:
                                break
            except Exception as e:
                print(f"    Error en feed estático español {feed_url}: {e}")

        # Feeds estáticos en Inglés
        feeds_en = RSS_FEEDS_EN.get(area_id, [])
        for feed_url in feeds_en:
            if len(article_candidates) >= 3:
                break
            try:
                res = requests.get(feed_url, timeout=10, headers=headers, verify=False)
                if res.status_code == 200:
                    feed = feedparser.parse(res.content)
                    for entry in feed.entries[:8]:
                        title = entry.get("title", "")
                        link = entry.get("link")
                        
                        if any(c["url"] == link for c in article_candidates):
                            continue
                        text_to_check = (title + " " + entry.get("summary", "")).lower()
                        if check_keyword_match(text_to_check, label_en):
                            existing = supabase.table("articles").select("id").eq("url", link).execute()
                            if existing.data:
                                continue
                            
                            article_candidates.append({
                                "url": link,
                                "title": title,
                                "cover": None
                            })
                            if len(article_candidates) >= 3:
                                break
            except Exception as e:
                print(f"    Error en feed estático inglés {feed_url}: {e}")
            
    print(f"    Candidatos encontrados: {len(article_candidates)}")
    
    # 4. Procesar candidatos con BeautifulSoup + Gemini
    duplicates_count = 0
    error_count = 0
    for cand in article_candidates:
        if articles_found >= limit:
            break
        try:
            # Evitar duplicados por URL de forma estricta antes de descargar
            existing = supabase.table("articles").select("id").eq("url", cand["url"]).execute()
            if existing.data:
                duplicates_count += 1
                continue

            print(f"    Extrayendo texto web de: {cand['url']}")
            web_res = requests.get(cand["url"], timeout=10, verify=False)
            if web_res.status_code != 200:
                error_count += 1
                continue
                
            soup = BeautifulSoup(web_res.text, 'html.parser')
            
            for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript']):
                s.decompose()
                
            article_elem = soup.find('article') or soup.find(class_=re.compile("content|body|post|entry|article")) or soup.body
            paragraphs = [p.get_text().strip() for p in article_elem.find_all('p') if len(p.get_text().strip()) > 30]
            article_raw_text = "\n\n".join(paragraphs)
            
            if len(article_raw_text) < 200:
                error_count += 1
                continue
                
            import json
            # Prompt de formato para Gemini (JSON estructurado)
            prompt = f"""
            Eres un Editor Senior y analista de contenidos de alta precisión. Tu objetivo es transformar el siguiente artículo web en una síntesis inteligente (Smart Digest) en español que ahorre tiempo al lector y le aporte valor puro.

            Título Original del Artículo: {cand["title"]}
            URL del Artículo: {cand["url"]}

            --- TEXTO COMPLETO DEL ARTÍCULO ---
            {article_raw_text}
            -----------------------------------

            Debes devolver obligatoriamente un objeto JSON válido con los siguientes tres campos:
            1. "title_es": Traduce o adapta el título a un español natural, profesional y atractivo para una app de crecimiento personal/profesional. Si ya está en español, devuélvelo optimizado.
            2. "snippet": Una frase corta y potente (máximo 120 caracteres) que resuma por qué este artículo merece leerse. Evita frases vacías como "En este artículo...".
            3. "content": Redacta la síntesis inteligente en Markdown siguiendo rigurosamente esta estructura:

               ### 📌 3 CLAVES EN 1 MINUTO
               * *[Conclusión principal 1]*
               * *[Conclusión principal 2]*
               * *[Conclusión principal 3]*

               ## 🔍 ANÁLISIS Y SÍNTESIS INTELIGENTE
               [Sintetiza el contenido completo en 2 o 3 secciones temáticas con títulos H2 y H3 llamativos. Organiza en párrafos concisos y usa listas con viñetas para datos o listas de ideas. No uses paja periodística ni introducciones irrelevantes.]

               ## 💡 ¿POR QUÉ ESTO TE IMPORTA HOY?
               [Un párrafo final de aplicabilidad real: explica qué debe hacer, tener en cuenta o cambiar el lector en su día a día tras leer esta información.]

               ---
               *Créditos: Adaptación inteligente del artículo original publicado en [{cand['url']}]({cand['url']}). Todos los derechos pertenecen a su respectivo autor.*

            REGLA DE RENDERING: Deja siempre saltos de línea vacíos antes y después de listas de viñetas, citas en bloque y divisores (---).

            Respuesta estrictamente en JSON válido con esta estructura:
            {{
              "title_es": "Título en español",
              "snippet": "Vista previa atractiva",
              "content": "Contenido Markdown completo"
            }}
            """
            
            print(f"    Estructurando, traduciendo y generando snippet en una sola llamada de IA...")
            ai_res_text = generate_llm_content(
                prompt,
                response_mime_type="application/json"
            )
            
            try:
                data = json.loads(ai_res_text)
                title_es = data.get("title_es", cand["title"]).strip()
                snippet = data.get("snippet", title_es).strip()
                content_raw = data.get("content", "").strip()
            except Exception as e_json:
                print(f"    Advertencia al parsear respuesta JSON de Gemini: {e_json}. Usando fallback.")
                title_es = translate_title_to_spanish(cand["title"])
                snippet = title_es
                content_raw = ai_res.text
                
            article_markdown = clean_markdown_text(content_raw)
            
            words_count = len(article_markdown.split())
            read_time_val = max(2, min(10, round(words_count / 200)))
            read_time = f"{read_time_val} min"
            
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
                
            art_data = {
                "interest_id": interest_id,
                "title": title_es,
                "snippet": snippet,
                "read_time": read_time,
                "image_url": cand["cover"],
                "url": cand["url"],
                "content": article_markdown,
                "collected": False
            }
            
            supabase.table("articles").insert(art_data).execute()
            print(f"    [+] Artículo guardado y formateado con IA: {title_es}")
            articles_found += 1
            
        except Exception as e:
            global last_error_msg
            last_error_msg = str(e)
            print(f"    Error procesando artículo {cand['url']}: {e}")
            error_count += 1

    return {
        "candidates": len(article_candidates),
        "duplicates": duplicates_count,
        "errors": error_count,
        "generated": articles_found
    }

def run_gather(limit=3, interest_ids=None, content_types=None, limits_config=None):
    global last_error_msg
    last_error_msg = ""
    print("="*60)
    print("WHEELLIFE RSS - RECOLECTOR DE CONTENIDOS CON GEMINI AI")
    print("="*60)
    
    # Parsear content_types
    if content_types:
        types_list = [x.strip().lower() for x in content_types.split(",") if x.strip()]
        enabled_types = set(types_list)
    else:
        enabled_types = {"books", "articles", "people"}

    # Extraer límites específicos por tipo
    book_limit = limits_config.get("books", limit) if limits_config else limit
    article_limit = limits_config.get("articles", limit) if limits_config else limit
    person_limit = limits_config.get("people", limit) if limits_config else limit
        
    print(f"Módulos de recolección activados: {list(enabled_types)} (Libros: {book_limit}, Artículos: {article_limit}, Biografías: {person_limit})")
    
    try:
        init_interests()
        
        try:
            res = supabase.table("interests").select("*").execute()
            interests = res.data
            print(f"Cargados {len(interests)} intereses globales.")
        except Exception as e:
            print(f"Error cargando intereses de Supabase: {e}")
            return

        # 2. Filtrar intereses según argumentos
        if interest_ids:
            selected_ids = [x.strip() for x in interest_ids.split(",") if x.strip()]
            interests = [i for i in interests if i["id"] in selected_ids]
            print(f"Filtrado a {len(interests)} intereses especificados: {selected_ids}")
            if len(interests) == 0:
                print("Ninguno de los intereses especificados coincide con los cargados de Supabase.")
                return
        else:
            import random
            random.shuffle(interests)
            print(f"Sin intereses específicos solicitados. Procesando aleatoriamente.")

        # 3. Construir la lista completa y desglosada de todas las tareas (por cada interés y por cada elemento de contenido)
        full_queue_items = []
        for i in interests:
            lbl = i["label"]
            if "books" in enabled_types and book_limit > 0:
                for k in range(1, book_limit + 1):
                    full_queue_items.append(f"Libro {k} de {book_limit} para {lbl}")
            if "people" in enabled_types and person_limit > 0:
                for k in range(1, person_limit + 1):
                    full_queue_items.append(f"Biografía {k} de {person_limit} para {lbl}")
            if "articles" in enabled_types and article_limit > 0:
                for k in range(1, article_limit + 1):
                    full_queue_items.append(f"Artículo {k} de {article_limit} para {lbl}")

        try:
            supabase.table("system_settings").upsert({
                "key": "scraping_queue",
                "value": ", ".join(full_queue_items)
            }).execute()
        except Exception:
            pass

        print(f"\nIniciando recolección de contenido (Total ítems en cola: {len(full_queue_items)})...")
        items_generated = 0
        attempts = 0
        total_candidates = 0
        total_duplicates = 0
        total_errors = 0
        
        if not interests:
            print("No hay intereses para procesar.")
            return
            
        current_queue_index = 0
            
        # Iterar sobre cada interés de forma secuencial y aplicar los límites individuales
        for idx, interest in enumerate(interests):
            if check_should_abort():
                print("Abortando recolección de intereses...")
                break

            interest_id = interest["id"]
            label = interest["label"]
            area_id = interest["area_id"]
            
            # 1. Buscar libros (hasta 'book_limit' individuales por interés)
            if "books" in enabled_types and book_limit > 0:
                books_generated = 0
                book_attempts = 0
                while books_generated < book_limit and book_attempts < book_limit * 2:
                    if check_should_abort():
                        print("Abortando recolección de libros...")
                        break

                    remaining_queue = full_queue_items[current_queue_index:]
                    current_item_name = f"Libro {books_generated + 1} de {book_limit} para [{label}] ({area_id})"

                    try:
                        supabase.table("system_settings").upsert({
                            "key": "scraping_current_task",
                            "value": current_item_name
                        }).execute()
                        supabase.table("system_settings").upsert({
                            "key": "scraping_queue",
                            "value": ", ".join(remaining_queue)
                        }).execute()
                    except Exception:
                        pass

                    try:
                        res_before = supabase.table("books").select("id", count="exact").eq("interest_id", interest_id).execute()
                        count_before = res_before.count if res_before.count is not None else 0
                    except Exception:
                        count_before = 0
                        
                    fetch_book_summaries(interest_id, label)
                    
                    try:
                        res_after = supabase.table("books").select("id", count="exact").eq("interest_id", interest_id).execute()
                        count_after = res_after.count if res_after.count is not None else 0
                    except Exception:
                        count_after = 0
                        
                    if count_after > count_before:
                        books_generated += 1
                        items_generated += 1
                        print(f"    -> Libro generado con éxito ({books_generated}/{book_limit})")
                    else:
                        print("    -> No se generó ningún libro nuevo en este intento.")
                    book_attempts += 1
                    attempts += 1
                    current_queue_index += 1
                    
            # 2. Buscar personajes (hasta 'person_limit' individuales por interés)
            if "people" in enabled_types and person_limit > 0:
                people_generated = 0
                people_attempts = 0
                while people_generated < person_limit and people_attempts < person_limit * 2:
                    if check_should_abort():
                        print("Abortando recolección de personas...")
                        break
                        
                    remaining_queue = full_queue_items[current_queue_index:]
                    current_item_name = f"Biografía {people_generated + 1} de {person_limit} para [{label}] ({area_id})"

                    try:
                        supabase.table("system_settings").upsert({
                            "key": "scraping_current_task",
                            "value": current_item_name
                        }).execute()
                        supabase.table("system_settings").upsert({
                            "key": "scraping_queue",
                            "value": ", ".join(remaining_queue)
                        }).execute()
                    except Exception:
                        pass

                    try:
                        res_before = supabase.table("people").select("id", count="exact").eq("interest_id", interest_id).execute()
                        count_before = res_before.count if res_before.count is not None else 0
                    except Exception:
                        count_before = 0
                        
                    fetch_wikipedia_people(interest_id, label)
                    
                    try:
                        res_after = supabase.table("people").select("id", count="exact").eq("interest_id", interest_id).execute()
                        count_after = res_after.count if res_after.count is not None else 0
                    except Exception:
                        count_after = 0
                        
                    if count_after > count_before:
                        people_generated += 1
                        items_generated += 1
                        print(f"    -> Biografía generada con éxito ({people_generated}/{person_limit})")
                    else:
                        print("    -> No se generó ninguna biografía nueva en este intento.")
                    people_attempts += 1
                    attempts += 1
                    current_queue_index += 1
                    
            # 3. Buscar artículos (hasta 'article_limit' en una sola llamada)
            if "articles" in enabled_types and article_limit > 0:
                remaining_queue = full_queue_items[current_queue_index:]
                current_item_name = f"Artículos (hasta {article_limit}) para [{label}] ({area_id})"

                try:
                    supabase.table("system_settings").upsert({
                        "key": "scraping_current_task",
                        "value": current_item_name
                    }).execute()
                    supabase.table("system_settings").upsert({
                        "key": "scraping_queue",
                        "value": ", ".join(remaining_queue)
                    }).execute()
                except Exception:
                    pass

                try:
                    res_before = supabase.table("articles").select("id", count="exact").eq("interest_id", interest_id).execute()
                    count_before = res_before.count if res_before.count is not None else 0
                except Exception:
                    count_before = 0

                result_stats = fetch_rss_and_devto_articles(interest_id, label, area_id, limit=article_limit)
                if isinstance(result_stats, dict):
                    total_candidates += result_stats.get("candidates", 0)
                    total_duplicates += result_stats.get("duplicates", 0)
                    total_errors += result_stats.get("errors", 0)
                    articles_gen = result_stats.get("generated", 0)
                    items_generated += articles_gen
                    current_queue_index += article_limit
                else:
                    articles_gen = 0

                try:
                    res_after = supabase.table("articles").select("id", count="exact").eq("interest_id", interest_id).execute()
                    count_after = res_after.count if res_after.count is not None else 0
                except Exception:
                    count_after = 0

                if count_after > count_before:
                    items_generated += articles_gen
                    print(f"    -> Artículos generados con éxito: {articles_gen} artículo(s) para este interés")
                else:
                    if isinstance(result_stats, dict):
                        if result_stats["candidates"] == 0:
                            print("    -> No se encontraron artículos candidatos en los feeds para este tema.")
                        elif result_stats["duplicates"] == result_stats["candidates"]:
                            print("    -> Todos los artículos candidatos encontrados ya existen en la base de datos.")
                        elif result_stats["errors"] > 0 and result_stats["generated"] == 0:
                            print(f"    -> Fallo al descargar o estructurar los artículos ({result_stats['errors']} errores).")
                        else:
                            print("    -> No se pudo generar ningún artículo nuevo.")
                    else:
                        print("    -> No se generó ningún artículo nuevo.")
                attempts += limit # Cada candidato procesado o intentado cuenta en el reporte
            
        print("\n" + "="*60)
        print(f"¡Recolección completada! Elementos generados: {items_generated} en total en {attempts} intentos.")
        print("="*60)
    finally:
        try:
            if items_generated == 0 and last_error_msg:
                if "429" in last_error_msg or "Quota exceeded" in last_error_msg:
                    friendly_error = "Se ha superado el límite de uso de la Inteligencia Artificial. Por favor, espera aproximadamente un minuto e inténtalo de nuevo."
                elif "localhost" in last_error_msg or "127.0.0.1" in last_error_msg or "11434" in last_error_msg or (LLM_PROVIDER == "local" and "Max retries exceeded" in last_error_msg):
                    friendly_error = f"No se pudo conectar con el servidor de IA local (Ollama) en '{LOCAL_LLM_BASE_URL}'. Por favor, comprueba que Ollama está abierto y ejecutándose en tu ordenador."
                elif "Max retries exceeded" in last_error_msg:
                    friendly_error = "Error de conexión. No se pudo conectar con los servicios externos de búsqueda."
                else:
                    friendly_error = f"Ocurrió un error inesperado: {last_error_msg[:120]}"
                final_result = f"Error: {friendly_error}"
            elif items_generated == 0:
                # Caso exitoso pero sin nuevos registros generados
                if total_candidates == 0:
                    reason = "No se encontraron artículos candidatos en los feeds para estos temas."
                elif total_duplicates == total_candidates:
                    reason = "Todos los artículos candidatos encontrados ya existen en la base de datos."
                elif total_errors > 0:
                    reason = f"Fallo al descargar o estructurar los artículos ({total_errors} errores de descarga o de cuota de IA)."
                else:
                    reason = "No se encontraron elementos nuevos en la búsqueda."
                final_result = f"Sin novedades: {reason}"
            else:
                final_result = f"Éxito: Se generaron {items_generated} elemento(s) de contenido."
                
            supabase.table("system_settings").upsert({"key": "scraping_last_error", "value": final_result}).execute()
            supabase.table("system_settings").upsert({"key": "scraping_status", "value": "idle"}).execute()
            supabase.table("system_settings").upsert({"key": "scraping_queue", "value": ""}).execute()
            supabase.table("system_settings").upsert({"key": "scraping_current_task", "value": ""}).execute()
        except Exception as e:
            print(f"Error actualizando status a idle: {e}")

import time
import json
import uuid

is_worker_running = False
worker_thread_obj = None

def append_order_to_queue(interest_ids_str=None, content_types_str=None, limits_config=None, limit=3):
    """Crea un objeto Orden único con todos sus ítems desglosados y lo anexa (append) al JSON de scraping_queue en Supabase."""
    init_interests()
    
    # 1. Obtener lista de intereses a procesar
    try:
        res = supabase.table("interests").select("*").execute()
        all_interests = res.data or []
    except Exception as e:
        print(f"Error cargando intereses: {e}")
        all_interests = []

    if interest_ids_str:
        selected_ids = [x.strip() for x in interest_ids_str.split(",") if x.strip()]
        target_interests = [i for i in all_interests if i["id"] in selected_ids]
    else:
        import random
        target_interests = list(all_interests)
        random.shuffle(target_interests)

    if content_types_str:
        types_list = [x.strip().lower() for x in content_types_str.split(",") if x.strip()]
        enabled_types = set(types_list)
    else:
        enabled_types = {"books", "articles", "people"}

    book_limit = limits_config.get("books", limit) if limits_config else limit
    article_limit = limits_config.get("articles", limit) if limits_config else limit
    person_limit = limits_config.get("people", limit) if limits_config else limit

    # 2. Desglosar los ítems individuales de esta orden
    order_id = f"ord_{int(time.time())}_{uuid.uuid4().hex[:4]}"
    items = []
    
    for interest in target_interests:
        label = interest["label"]
        area_id = interest["area_id"]
        interest_id = interest["id"]

        if "books" in enabled_types and book_limit > 0:
            for k in range(1, book_limit + 1):
                items.append({
                    "id": f"item_{uuid.uuid4().hex[:6]}",
                    "type": "book",
                    "label": f"Libro {k} de {book_limit} para {label}",
                    "interest_id": interest_id,
                    "interest_label": label,
                    "area_id": area_id,
                    "sub_index": k,
                    "total_sub": book_limit
                })
        if "people" in enabled_types and person_limit > 0:
            for k in range(1, person_limit + 1):
                items.append({
                    "id": f"item_{uuid.uuid4().hex[:6]}",
                    "type": "person",
                    "label": f"Biografía {k} de {person_limit} para {label}",
                    "interest_id": interest_id,
                    "interest_label": label,
                    "area_id": area_id,
                    "sub_index": k,
                    "total_sub": person_limit
                })
        if "articles" in enabled_types and article_limit > 0:
            for k in range(1, article_limit + 1):
                items.append({
                    "id": f"item_{uuid.uuid4().hex[:6]}",
                    "type": "article",
                    "label": f"Artículo {k} de {article_limit} para {label}",
                    "interest_id": interest_id,
                    "interest_label": label,
                    "area_id": area_id,
                    "sub_index": k,
                    "total_sub": article_limit
                })

    order_summary = f"{len(target_interests)} interés(es) • {len(items)} elemento(s)"
    new_order = {
        "order_id": order_id,
        "created_at": time.strftime("%H:%M:%S"),
        "summary": order_summary,
        "items": items
    }

    # 3. Leer la cola existente de Supabase y hacer APPEND de la nueva orden
    current_orders = []
    try:
        res = supabase.table("system_settings").select("value").eq("key", "scraping_queue").execute()
        if res and res.data and len(res.data) > 0 and res.data[0].get("value"):
            try:
                current_orders = json.loads(res.data[0]["value"])
                if not isinstance(current_orders, list):
                    current_orders = []
            except Exception:
                current_orders = []
    except Exception as e:
        print(f"Error leyendo cola previa: {e}")

    current_orders.append(new_order)

    # 4. Guardar JSON actualizado en Supabase
    try:
        supabase.table("system_settings").upsert({
            "key": "scraping_queue",
            "value": json.dumps(current_orders, ensure_ascii=False)
        }).execute()
        supabase.table("system_settings").upsert({
            "key": "scraping_status",
            "value": "in_progress"
        }).execute()
    except Exception as e:
        print(f"Error actualizando scraping_queue en Supabase: {e}")

    return new_order


def process_queue_worker():
    """Worker secuencial FIFO: procesa orden por orden y elemento por elemento desde el JSON de Supabase."""
    global is_worker_running
    is_worker_running = True
    print("\n[WORKER] Hilo procesador de cola iniciado en segundo plano...")

    while True:
        if check_should_abort():
            print("[WORKER] Señal de aborto recibida. Deteniendo worker...")
            break

        # 1. Leer estado actual de la cola desde Supabase
        orders = []
        try:
            res = supabase.table("system_settings").select("value").eq("key", "scraping_queue").execute()
            if res and res.data and len(res.data) > 0 and res.data[0].get("value"):
                try:
                    orders = json.loads(res.data[0]["value"])
                except Exception:
                    orders = []
        except Exception as e:
            print(f"[WORKER] Error leyendo cola: {e}")

        # Si no hay órdenes pendientes, finalizar worker y marcar status idle
        if not orders or len(orders) == 0:
            print("[WORKER] No hay órdenes pendientes en la cola. Pasando a idle.")
            try:
                supabase.table("system_settings").upsert({"key": "scraping_status", "value": "idle"}).execute()
                supabase.table("system_settings").upsert({"key": "scraping_current_task", "value": ""}).execute()
                supabase.table("system_settings").upsert({"key": "scraping_queue", "value": "[]"}).execute()
            except Exception:
                pass
            break

        # 2. Tomar la primera Orden activa y su primer Ítem pendiente
        current_order = orders[0]
        items = current_order.get("items", [])

        if not items or len(items) == 0:
            # Si la orden se quedó vacía, eliminarla y guardar
            orders.pop(0)
            try:
                supabase.table("system_settings").upsert({"key": "scraping_queue", "value": json.dumps(orders, ensure_ascii=False)}).execute()
            except Exception:
                pass
            continue

        item = items[0]
        item_id = item.get("id")
        item_type = item.get("type")
        interest_id = item.get("interest_id")
        interest_label = item.get("interest_label")
        area_id = item.get("area_id")
        task_label = item.get("label", "Procesando elemento")

        # 3. Notificar tarea actual a Supabase
        try:
            supabase.table("system_settings").upsert({"key": "scraping_current_task", "value": f"[{current_order['order_id'][-6:]}] {task_label}"}).execute()
            supabase.table("system_settings").upsert({"key": "scraping_status", "value": "in_progress"}).execute()
        except Exception:
            pass

        print(f"\n[WORKER] Procesando tarea: {task_label} (Orden {current_order['order_id']})")

        # 4. Ejecutar la recolección según el tipo de ítem
        try:
            if item_type == "book":
                fetch_book_summaries(interest_id, interest_label)
            elif item_type == "person":
                fetch_wikipedia_people(interest_id, interest_label)
            elif item_type == "article":
                fetch_rss_and_devto_articles(interest_id, interest_label, area_id, limit=1)
        except Exception as e:
            print(f"[WORKER] Error procesando ítem {task_label}: {e}")

        # 5. Volver a leer la cola actual (por si el usuario canceló algo mientras procesábamos)
        latest_orders = []
        try:
            res = supabase.table("system_settings").select("value").eq("key", "scraping_queue").execute()
            if res and res.data and len(res.data) > 0 and res.data[0].get("value"):
                try:
                    latest_orders = json.loads(res.data[0]["value"])
                except Exception:
                    latest_orders = []
        except Exception:
            latest_orders = orders

        # Eliminar el ítem procesado de la lista actual de Supabase
        if latest_orders and len(latest_orders) > 0:
            first_ord = latest_orders[0]
            items_arr = first_ord.get("items", [])
            if items_arr and len(items_arr) > 0:
                items_arr.pop(0)
            if len(items_arr) == 0:
                latest_orders.pop(0)
            try:
                supabase.table("system_settings").upsert({"key": "scraping_queue", "value": json.dumps(latest_orders, ensure_ascii=False)}).execute()
            except Exception as e:
                print(f"[WORKER] Error al guardar cola tras procesar ítem: {e}")

        time.sleep(1) # Breve pausa entre tareas

    is_worker_running = False


def start_worker_if_needed():
    """Inicia el worker thread si no está corriendo actualmente."""
    global is_worker_running, worker_thread_obj
    if not is_worker_running or worker_thread_obj is None or not worker_thread_obj.is_alive():
        import threading
        worker_thread_obj = threading.Thread(target=process_queue_worker)
        worker_thread_obj.daemon = True
        worker_thread_obj.start()
        print("[WORKER] Worker thread iniciado.")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=3, help="Límite de libros/elementos a generar en total")
    parser.add_argument('--interests', type=str, default=None, help="Lista de IDs de intereses separados por coma")
    parser.add_argument('--content_types', type=str, default=None, help="Tipos de contenido separados por coma (books,articles,people)")
    args = parser.parse_args()
    append_order_to_queue(interest_ids_str=args.interests, content_types_str=args.content_types, limit=args.limit)
    process_queue_worker()

if __name__ == "__main__":
    main()
