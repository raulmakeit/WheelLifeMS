import os
import sys
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

if not GEMINI_API_KEY or "tu_clave" in GEMINI_API_KEY:
    print("Error: GEMINI_API_KEY es obligatorio. Consigue una clave gratuita en Google AI Studio y colócala en el .env.")
    sys.exit(1)

# Configurar Google Gemini usando REST (evita errores de gRPC SSL)
genai.configure(api_key=GEMINI_API_KEY, transport='rest')
# Usar gemini-2.5-flash como modelo rápido y gratuito por defecto
model = genai.GenerativeModel('gemini-2.5-flash')

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

RSS_FEEDS = {
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
        "https://www.treehugger.com/feeds/category/all/"
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

# Global para capturar errores de cuota o scraping
last_error_msg = ""

def fetch_book_summaries(interest_id, label):
    """Busca libros usando Google Books API y genera un resumen estructurado con Gemini AI."""
    global last_error_msg
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

        # 2. Preguntar a Gemini por el libro definitivo/más influyente para ese interés
        recommend_prompt = f"""
        Dime el título exacto y el autor del libro de desarrollo personal, negocios, salud, finanzas, mentalidad, ciencia o temáticas afines más influyente, respetado y de mayor impacto educativo/práctico en todo el mundo sobre el tema: "{label}".{exclude_clause}
        Responde únicamente en una sola línea con el formato exacto: Título del Libro | Nombre del Autor
        Ejemplos:
        Si el tema es Criptomonedas, responde: El patrón Bitcoin | Saifedean Ammous
        Si el tema es Productividad, responde: Hábitos atómicos | James Clear
        Si el tema es Estoicismo, responde: Meditaciones | Marco Aurelio
        No agregues explicaciones, ni viñetas, ni texto adicional.
        """
        
        suggested_title = label
        suggested_author = ""
        query = label
        
        try:
            rec_res = model.generate_content(recommend_prompt)
            recommendation = rec_res.text.strip()
            
            if "|" in recommendation:
                parts = [x.strip() for x in recommendation.split("|", 1)]
                suggested_title = parts[0].replace("**", "").replace("*", "")
                suggested_author = parts[1].replace("**", "").replace("*", "")
                query = f"{suggested_title} {suggested_author}"
                print(f"    Gemini recomendó el libro: '{suggested_title}' de '{suggested_author}'")
            else:
                print(f"    Respuesta de recomendación inesperada: {recommendation}. Usando etiqueta.")
        except Exception as e:
            print(f"    Error recomendando libro con Gemini: {e}. Usando etiqueta.")

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
                print(f"    Advertencia: Google Books API devolvió código {res.status_code}.")
        except Exception as api_err:
            print(f"    Error consultando Google Books API: {api_err}")

        # --- B. FALLBACK A OPEN LIBRARY (Si no tenemos portada o enlace) ---
        if not cover_url or not book_url:
            print("    Intentando obtener datos/portada desde Open Library...")
            try:
                ol_url = f"https://openlibrary.org/search.json?q={urllib.parse.quote(query)}&limit=1"
                ol_res = requests.get(ol_url, timeout=10, verify=False)
                if ol_res.status_code == 200:
                    ol_data = ol_res.json()
                    docs = ol_data.get("docs", [])
                    if docs:
                        doc = docs[0]
                        if not title or title == suggested_title:
                            title = doc.get("title", suggested_title)
                        if authors == "Autor Desconocido" or not authors:
                            authors = ", ".join(doc.get("author_name", [suggested_author or "Autor Desconocido"]))
                        if not book_url:
                            key = doc.get("key", "")
                            book_url = f"https://openlibrary.org{key}" if key else ""
                        if not cover_url:
                            cover_i = doc.get("cover_i")
                            if cover_i:
                                cover_url = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"
                        print(f"    [+] Datos complementados con Open Library: {title} (Portada: {'Sí' if cover_url else 'No'})")
            except Exception as ol_err:
                print(f"    Error en fallback de Open Library: {ol_err}")

        # --- C. FALLBACK A ITUNES SEARCH API (Si aún no tenemos portada) ---
        if not cover_url:
            print("    Intentando obtener portada desde iTunes Search API...")
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
                            # Reemplazar las dimensiones de la imagen para obtener alta resolución
                            cover_url = cover_candidate.replace("100x100bb", "600x600bb").replace("100x100", "600x600")
                            print(f"    [+] Portada obtenida de iTunes: {cover_url}")
            except Exception as itunes_err:
                print(f"    Error en fallback de iTunes: {itunes_err}")

        # Evitar duplicados por URL
        if book_url:
            existing = supabase.table("books").select("id").eq("url", book_url).execute()
            if existing.data:
                print(f"    [=] El libro ya existe en la BD por URL: {title}")
                return
        
        # 4. Prompt de extracción de valor ultra-premium estructurado para lectura de 3-8 minutos
        prompt = f"""
        Eres un analista de ideas, curador de contenido premium y experto en destilar conocimiento práctico y de alto impacto de libros influyentes.
        Escribe una reseña profunda, técnica y sumamente valiosa del libro "{title}" escrito por {authors} en español.
        
        El objetivo es entregar un valor tan extraordinario que el usuario se lleve los conceptos fundamentales, la teoría técnica y las herramientas del libro sin necesidad de leerlo completo. 
        El texto debe tener una extensión detallada, ideal para una lectura de entre 3 y 8 minutos (aproximadamente entre 800 y 1600 palabras), redactado con un tono inspirador, profesional, riguroso y altamente estructurado.

        Pautas de formato críticas para el rendering:
        - Deja siempre al menos una línea en blanco (vacía) antes y después de cualquier tabla Markdown, lista de viñetas, lista numerada, cita en bloque o línea divisoria (---). Si dejas líneas contiguas sin separar con un salto de línea vacío, la aplicación móvil no podrá procesar el formato correctamente y mostrará el texto roto o sin estructurar.
        - Asegúrate de que las tablas Markdown tengan la fila separadora estándar de guiones `| --- | --- |` y estén rodeadas de saltos de línea limpios.
        
        Debes utilizar exactamente la siguiente estructura en Markdown (conservando las secciones y títulos correspondientes):

        # {title}
        *Por {authors}*

        ---

        ## 1. LA HISTORIA ILUSTRATIVA: COMPRENDIENDO EL CONCEPTO
        [Escribe una sección narrativa cautivadora y detallada (de 2 o 3 párrafos completos) con un caso de éxito real, un hecho histórico, un experimento científico o una analogía cotidiana explicativa potente que ilustre la teoría principal del libro. Usa técnicas de storytelling para enganchar al lector y justificar el valor práctico del libro desde la primera palabra.]

        ## 2. LA TESIS CENTRAL
        * **La Gran Idea (The Big Idea)**: [Destila el mensaje fundamental del libro en una única frase de gran potencia conceptual e impacto.]
        * **El Problema de Raíz**: [Explica detalladamente qué problema personal, profesional, financiero o técnico ataca el libro y por qué los métodos convencionales fallan en resolverlo (1-2 párrafos).]
        * **Perfil Objetivo**: [Define en una línea el perfil del lector al que este libro puede cambiarle la vida (ej: "Para inversores preocupados por la inflación" o "Para personas estancadas en sus rutinas diarias").]

        ## 3. CONCEPTOS CLAVE Y MODELOS MENTALES

        * **[Concepto Clave 1]**: [Explica el concepto en detalle, su definición técnica, lógica subyacente y cómo opera.]
        * **[Concepto Clave 2]**: [Explica el concepto en detalle...]
        (Genera de 3 a 8 conceptos en formato de lista con viñetas según sea necesario.)
        * **El Mito Derribado**: [Describe la creencia popular errónea que el libro destruye mediante datos o lógica sólida. Explica por qué esa creencia está saboteando al lector.]

        [SI EL LIBRO CONTIENE CONCEPTOS TÉCNICOS, JERGA O VOCABULARIO COMPLEJO (ej: Criptomonedas, Estoicismo, Feng Shui, Ciencia, etc.):
        ## 4. GLOSARIO DE JERGA TÉCNICA
        
        * **[Término 1]**: [Definición exacta y técnica del término.]
          ***Aplicación Práctica***: [Cómo entender el término con una analogía o ejemplo cotidiano y sencillo.]
        * **[Término 2]**: [Definición exacta...]
          ***Aplicación Práctica***: [Ejemplo cotidiano...]
        (Genera de 3 a 8 términos en este formato de lista anidada si la temática lo requiere. Si el libro es extremadamente simple o conceptual y no posee terminología especializada, omite esta sección por completo.)]

        ## 5. LOS PILARES FUNDAMENTALES (EXTRACCIÓN DE VALOR)

        1. **[Título del Pilar 1 (Accionable y Llamativo)]**:
           ***La Teoría***: [Explica el fundamento teórico y la lógica técnica de este pilar en detalle (3-4 líneas).]
           ***En la Práctica***: [Cómo se traduce este pilar al comportamiento humano o sistemas del día a día (2-3 líneas).]
           
        2. **[Título del Pilar 2]**:
           ***La Teoría***: [Explica la teoría...]
           ***En la Práctica***: [Cómo se aplica...]
        (Genera de 3 a 8 pilares numerados según requiera el libro.)

        ## 6. PROTOCOLO DE APLICACIÓN PRÁCTICA (PASOS A LA ACCIÓN)

        1. **[Paso/Protocolo 1]**: [Describe una tarea concreta, accionable y ultra-específica que el lector pueda realizar en su rutina.]
        2. **[Paso/Protocolo 2]**: [Describe la siguiente tarea...]
        (Genera de 3 a 8 pasos numerados.)

        * **Pregunta de Autoevaluación Crítica**: [Una pregunta introspectiva y desafiante diseñada para que el lector evalúe honestamente sus bloqueos actuales bajo el prisma del libro.]

        ---
        ***Resumen interpretativo independiente de "{title}", original de {authors}.***
        """
        
        print(f"    Generando resumen estructurado con Gemini para: {title}")
        ai_response = model.generate_content(prompt)
        summary_markdown = ai_response.text
        
        # Calcular tiempo de lectura estimado
        words_count = len(summary_markdown.split())
        read_time_val = max(3, min(8, round(words_count / 200)))
        read_time = f"{read_time_val} min"
        
        # Generar vista previa para la tarjeta (snippet) - Extrae "La Gran Idea" del Markdown
        snippet = f"Lectura: {read_time} • Resumen estructurado con ideas clave y plan de acción."
        try:
            # Importamos re localmente por si acaso, aunque ya está al inicio del archivo
            import re
            match_idea = re.search(r"\*\s*\*La Gran Idea.*?\*\s*:\s*(.*?)(?:\n|$)", summary_markdown)
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
            "cover_url": cover_url,
            "url": book_url,
            "summary": summary_markdown,
            "collected": False
        }
        
        supabase.table("books").insert(book_data).execute()
        print(f"    [+] Libro guardado con resumen IA: {title}")
            
    except Exception as e:
        last_error_msg = str(e)
        print(f"    Error buscando/resumiendo libros para {label}: {e}")

def fetch_wikipedia_people(interest_id, label):
    """Busca personajes en Wikipedia y genera una biografía narrativa apasionante con Gemini AI."""
    print(f"  Buscando personas/conceptos relevantes sobre: {label}")
    try:
        search_url = f"https://es.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(label)}&format=json&utf8=1&origin=*"
        res = requests.get(search_url, timeout=10, verify=False)
        if res.status_code != 200:
            return
            
        search_results = res.json().get("query", {}).get("search", [])
        if not search_results:
            return
            
        top_page = search_results[0]
        title = top_page.get("title")
        page_id = top_page.get("pageid")
        wiki_url = f"https://es.wikipedia.org/wiki/{urllib.parse.quote(title)}"
        
        # Evitar duplicados
        existing = supabase.table("people").select("id").eq("url", wiki_url).execute()
        if existing.data:
            return

        # 2. Descargar extracto largo
        detail_url = f"https://es.wikipedia.org/w/api.php?action=query&prop=extracts|pageimages&exintro=0&explaintext=1&piprop=thumbnail&pithumbsize=300&titles={urllib.parse.quote(title)}&format=json&origin=*"
        res_detail = requests.get(detail_url, timeout=10, verify=False)
        if res_detail.status_code != 200:
            return
            
        pages = res_detail.json().get("query", {}).get("pages", {})
        page_info = pages.get(str(page_id)) or list(pages.values())[0]
        
        raw_text = page_info.get("extract", "")
        # Cortar a 6000 caracteres para no exceder tokens
        wikipedia_context = raw_text[:6000] if raw_text else top_page.get("snippet", "")
        
        thumbnail = page_info.get("thumbnail", {})
        image_url = thumbnail.get("source") if isinstance(thumbnail, dict) else None
        
        # Prompt para Gemini
        prompt = f"""
        Eres un historiador y escritor con gran talento para el storytelling. Escribe una biografía en español sumamente apasionante e inspiradora de "{title}" basada en el siguiente contexto de Wikipedia:
        
        --- CONTEXTO ---
        {wikipedia_context}
        -----------------
        
        Instrucciones de redacción:
        1. No listes datos aburridos, fechas secas o listas de hechos de forma académica.
        2. Escribe una narrativa fluida e intensa. Enfócate en:
           - Sus mayores luchas, crisis o adversidades y cómo las superó.
           - Su momento decisivo o revelación (el "antes y después" de su vida).
           - Su filosofía clave, legado y por qué su vida es una fuente de inspiración real.
        3. El formato de salida debe ser Markdown limpio y estructurado. Debe comenzar con un título emocionante que resuma su esencia (ej: "# Marco Aurelio: El Emperador Filósofo que Conquistó sus Miedos") y estructurarse con títulos atractivos (usa H2 y H3).
        4. Al final, añade una sección con créditos que mencione: "*Créditos: Basado en registros históricos y contenido público de Wikipedia.*"
        """
        
        print(f"    Redactando biografía apasionante con Gemini para: {title}")
        ai_response = model.generate_content(prompt)
        biography_markdown = ai_response.text
        
        # Snippet corto de tarjeta
        snippet_preview = f"La apasionante historia de {title} y su impacto en {label}. Descubre sus luchas y su legado."
        
        people_data = {
            "interest_id": interest_id,
            "name": title,
            "snippet": snippet_preview,
            "image_url": image_url,
            "url": wiki_url,
            "biography": biography_markdown,
            "collected": False
        }
        supabase.table("people").insert(people_data).execute()
        print(f"    [+] Persona/Concepto guardado con biografía IA: {title}")
        
    except Exception as e:
        print(f"    Error buscando/redactando biografía para {label}: {e}")

def fetch_rss_and_devto_articles(interest_id, label, area_id):
    """Busca artículos, extrae el texto del cuerpo HTML, y genera un formato Markdown limpio con Gemini AI."""
    print(f"  Buscando artículos sobre: {label}")
    articles_found = 0
    article_candidates = []
    
    # 1. Dev.to
    if area_id == "career" or label.lower() in ["programación", "ia", "robotica", "diseño ux/ui", "marketing digital"]:
        try:
            clean_tag = label.lower().replace("programación", "programming").replace("diseño ux/ui", "ux").replace("robótica e ia", "ai")
            url = f"https://dev.to/api/articles?tag={urllib.parse.quote(clean_tag)}&per_page=2"
            res = requests.get(url, timeout=10, verify=False)
            if res.status_code == 200:
                for item in res.json():
                    art_url = item.get("url")
                    title = item.get("title")
                    
                    # Evitar duplicados
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

    # 2. RSS
    feeds = RSS_FEEDS.get(area_id, [])
    for feed_url in feeds:
        if len(article_candidates) >= 2:
            break
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                link = entry.get("link")
                
                # Comprobar palabra clave
                text_to_check = (title + " " + entry.get("summary", "")).lower()
                if label.lower() in text_to_check or any(kw in text_to_check for kw in [label.lower()]):
                    existing = supabase.table("articles").select("id").eq("url", link).execute()
                    if existing.data:
                        continue
                    
                    article_candidates.append({
                        "url": link,
                        "title": title,
                        "cover": None
                    })
                    if len(article_candidates) >= 2:
                        break
        except Exception as e:
            print(f"    Error parseando feed RSS {feed_url}: {e}")
            
    # 3. Procesar candidatos con BeautifulSoup + Gemini
    for cand in article_candidates:
        if articles_found >= 1: # Limitar a 1 artículo por interés para mantener rapidez
            break
        try:
            print(f"    Extrayendo texto web de: {cand['url']}")
            web_res = requests.get(cand["url"], timeout=10, verify=False)
            if web_res.status_code != 200:
                continue
                
            soup = BeautifulSoup(web_res.text, 'html.parser')
            
            # Limpieza básica de elementos no textuales
            for s in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript']):
                s.decompose()
                
            # Buscar el contenedor del artículo principal o fallback a body
            article_elem = soup.find('article') or soup.find(class_=re.compile("content|body|post|entry|article")) or soup.body
            
            # Obtener párrafos de texto limpios
            paragraphs = [p.get_text().strip() for p in article_elem.find_all('p') if len(p.get_text().strip()) > 30]
            article_raw_text = "\n\n".join(paragraphs[:15]) # Máximo 15 párrafos para evitar tokens excedidos
            
            if len(article_raw_text) < 200:
                continue
                
            # Prompt de formato para Gemini
            prompt = f"""
            Eres un editor de contenido experto. Limpia y dale un formato Markdown hermoso, limpio y estructurado en español al siguiente artículo:
            
            --- TEXTO ORIGINAL ---
            {article_raw_text}
            ----------------------
            
            Instrucciones:
            1. En la parte superior del artículo, añade un bloque en cursiva de "Resumen Ejecutivo de 1 Minuto" con las 3 conclusiones principales.
            2. Adapta el texto original para que sea cómodo de leer: organiza en títulos y subtítulos (H2, H3), usa listas con viñetas para puntos clave y destaca citas relevantes en bloque.
            3. Asegúrate de corregir cualquier error de copiado o formato del texto original.
            4. Al final del artículo, incluye siempre una sección de créditos diciendo: "*Créditos: Este contenido es una adaptación limpia del artículo original publicado en {cand['url']}. Todos los derechos pertenecen a su respectivo autor.*"
            """
            
            print(f"    Estructurando y resumiendo artículo con Gemini...")
            ai_res = model.generate_content(prompt)
            article_markdown = ai_res.text
            
            words_count = len(article_markdown.split())
            read_time_val = max(2, min(10, round(words_count / 200)))
            read_time = f"{read_time_val} min"
            
            # Recortar snippet para la vista previa de la tarjeta
            snippet = cand["title"]
            if len(snippet) > 80:
                snippet = snippet[:77] + "..."
            else:
                snippet = f"Lectura recomendada de {read_time}: " + snippet
                
            art_data = {
                "interest_id": interest_id,
                "title": cand["title"],
                "snippet": snippet,
                "read_time": read_time,
                "image_url": cand["cover"],
                "url": cand["url"],
                "content": article_markdown,
                "collected": False
            }
            
            supabase.table("articles").insert(art_data).execute()
            print(f"    [+] Artículo guardado y formateado con IA: {cand['title']}")
            articles_found += 1
            
        except Exception as e:
            print(f"    Error procesando artículo {cand['url']}: {e}")

def run_gather(limit=3, interest_ids=None):
    global last_error_msg
    last_error_msg = ""
    print("="*60)
    print("WHEELLIFE RSS - RECOLECTOR DE CONTENIDOS CON GEMINI AI")
    print("="*60)
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

        print(f"\nIniciando recolección de contenido (límite: {limit} libros/resúmenes)...")
        books_generated = 0
        max_attempts = limit * 3  # Previene bucles infinitos si hay errores repetidos o no hay más libros
        attempts = 0
        interest_index = 0
        
        if not interests:
            print("No hay intereses para procesar.")
            return
            
        while books_generated < limit and attempts < max_attempts:
            interest = interests[interest_index % len(interests)]
            interest_id = interest["id"]
            label = interest["label"]
            area_id = interest["area_id"]
            
            print(f"\n[*] Procesando [{interest_id}] {label} ({area_id}) - Intento {attempts + 1}")
            
            # Consultar cuántos libros hay antes de buscar
            try:
                res_before = supabase.table("books").select("id", count="exact").eq("interest_id", interest_id).execute()
                count_before = res_before.count if res_before.count is not None else 0
            except Exception:
                count_before = 0
                
            # 1. Buscar libro
            fetch_book_summaries(interest_id, label)
            
            # Consultar cuántos libros hay después de buscar para verificar si se añadió uno nuevo
            try:
                res_after = supabase.table("books").select("id", count="exact").eq("interest_id", interest_id).execute()
                count_after = res_after.count if res_after.count is not None else 0
            except Exception:
                count_after = 0
                
            if count_after > count_before:
                books_generated += 1
                print(f"    -> Libro generado con éxito ({books_generated}/{limit})")
            else:
                print("    -> No se generó ningún libro nuevo (posible duplicado o límite alcanzado).")
                
            # 2. Buscar personaje (sólo la primera vez para este interés para evitar llamadas repetidas redundantes)
            if attempts < len(interests):
                fetch_wikipedia_people(interest_id, label)
            
            # 3. Buscar artículos (sólo la primera vez para este interés para evitar llamadas repetidas redundantes)
            if attempts < len(interests):
                fetch_rss_and_devto_articles(interest_id, label, area_id)
                
            interest_index += 1
            attempts += 1
            
        print("\n" + "="*60)
        print(f"¡Recolección completada! Libros generados: {books_generated}/{limit} en {attempts} intentos.")
        print("="*60)
    finally:
        try:
            if books_generated == 0 and last_error_msg:
                if "429" in last_error_msg or "Quota exceeded" in last_error_msg:
                    friendly_error = "Se ha superado el límite de uso de la Inteligencia Artificial. Por favor, espera aproximadamente un minuto e inténtalo de nuevo."
                elif "Max retries exceeded" in last_error_msg:
                    friendly_error = "Error de conexión. No se pudo conectar con los servicios externos de búsqueda."
                else:
                    friendly_error = "Ocurrió un error inesperado generando el resumen. Inténtalo de nuevo más tarde."
                final_result = f"Error: {friendly_error}"
            else:
                final_result = f"Éxito: Se generaron {books_generated} libros."
                
            supabase.table("system_settings").upsert({"key": "scraping_last_error", "value": final_result}).execute()
            supabase.table("system_settings").upsert({"key": "scraping_status", "value": "idle"}).execute()
        except Exception as e:
            print(f"Error actualizando status a idle: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=3, help="Límite de libros a generar en total")
    parser.add_argument('--interests', type=str, default=None, help="Lista de IDs de intereses separados por coma")
    args = parser.parse_args()
    run_gather(limit=args.limit, interest_ids=args.interests)

if __name__ == "__main__":
    main()
