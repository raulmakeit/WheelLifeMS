import os
import threading
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Importar la lógica principal refactorizada de main.py
from main import run_gather, supabase

load_dotenv()

app = Flask(__name__)
CORS(app)

PYTHON_API_KEY = os.environ.get("PYTHON_API_KEY", "tu-llave-secreta")

@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"status": "online", "message": "WheelLifeMS Microservice is running"})

def check_auth(req):
    auth_header = req.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {PYTHON_API_KEY}":
        return False
    return True

@app.route("/gather", methods=["POST"])
def gather_data():
    if not check_auth(request):
        return jsonify({"success": False, "error": "No autorizado"}), 401
    
    data = request.json or {}
    limit = data.get("limit", 3)
    interests = data.get("interests", None)
    content_types = data.get("contentTypes", None)
    limits_config = data.get("limitsConfig", None)
    
    if interests and isinstance(interests, list):
        interests_str = ",".join(interests)
    elif interests and isinstance(interests, str):
        interests_str = interests
    else:
        interests_str = None
        
    if content_types and isinstance(content_types, list):
        content_types_str = ",".join(content_types)
    elif content_types and isinstance(content_types, str):
        content_types_str = content_types
    else:
        content_types_str = None

    # Anexar nueva Orden a la cola JSON en Supabase
    try:
        from main import append_order_to_queue, start_worker_if_needed
        new_order = append_order_to_queue(interests_str, content_types_str, limits_config, limit)
        start_worker_if_needed()
    except Exception as e:
        print(f"Error al encolar orden: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
        
    return jsonify({
        "success": True, 
        "message": f"Orden {new_order.get('order_id')} añadida a la cola."
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
