from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
from datetime import datetime
from pathlib import Path
import time

# === RUTAS DE ARCHIVOS ===
BASE_DIR = Path(__file__).resolve().parent
APP_FILE = BASE_DIR / "appointments.json"
SERVICES_FILE = BASE_DIR / "services.json"
STYLISTS_FILE = BASE_DIR / "stylists.json"
GALLERY_FILE = BASE_DIR / "gallery.json"   # 👈 nuevo JSON para galería

UPLOAD_FOLDER = BASE_DIR / "uploads"       # 👈 carpeta para imágenes
UPLOAD_FOLDER.mkdir(exist_ok=True)

app = Flask(__name__)
# Para pruebas: permitir cualquier origen. Podés restringir luego a tu dominio.
CORS(app, resources={r"/api/*": {"origins": "*"}})

# === HELPERS GENERALES PARA JSON ===

def load_json(path: Path, default):
    """
    Lee un JSON desde 'path'. Si no existe o falla, devuelve 'default'.
    """
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: Path, data):
    """
    Guarda 'data' como JSON en 'path'.
    """
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# === TURNOS (APPOINTMENTS) ===

def load_appointments():
    """
    Carga la lista de turnos desde appointments.json.
    """
    return load_json(APP_FILE, [])

def save_appointments(items):
    """
    Guarda la lista de turnos en appointments.json.
    """
    save_json(APP_FILE, items)

@app.route("/ping")
def ping():
    return jsonify({"ok": True, "message": "pong"})

@app.route("/api/appointments", methods=["GET", "POST"])
def appointments_collection():
    appointments = load_appointments()

    if request.method == "GET":
        return jsonify(appointments)

    # POST -> crear nuevo turno
    data = request.get_json(silent=True) or {}
    required_fields = [
        "id", "serviceId", "stylistId", "serviceName",
        "stylistName", "dateISO", "time",
        "clientName", "clientContact", "price"
    ]
    if not all(field in data for field in required_fields):
        return jsonify({"error": "Datos incompletos"}), 400

    # Normalizamos status: por defecto "pendiente" si no viene o es inválido
    if data.get("status") not in ("pendiente", "confirmado", "cancelado"):
        data["status"] = "pendiente"

    # === LÍMITE: máximo 2 turnos por hora ===
    date_iso = data.get("dateISO") or data.get("date")
    time_str = data.get("time")

    if not date_iso or not time_str:
        return jsonify({"error": "Fecha y hora requeridas"}), 400

    day_str = date_iso[:10]  # YYYY-MM-DD

    count_existing = 0
    for a in appointments:
        a_status = a.get("status", "pendiente")
        if a_status == "cancelado":
            continue

        a_time = a.get("time")
        a_date_raw = a.get("dateISO") or a.get("date")
        if not a_time or not a_date_raw:
            continue

        a_day = a_date_raw[:10]

        if a_time == time_str and a_day == day_str:
            count_existing += 1

    if count_existing >= 2:
        return jsonify({
            "error": "La hora seleccionada ya tiene el máximo de turnos.",
            "code": "TIME_SLOT_FULL"
        }), 409

    # si ya existe el id, lo reemplazamos
    existing_idx = next(
        (i for i, a in enumerate(appointments) if a.get("id") == data["id"]),
        None
    )
    if existing_idx is not None:
        appointments[existing_idx] = data
    else:
        appointments.append(data)

    save_appointments(appointments)
    return jsonify(data), 201

@app.route("/api/appointments/<int:appt_id>", methods=["PATCH"])
def update_appointment(appt_id):
    appointments = load_appointments()
    idx = next(
        (i for i, a in enumerate(appointments) if a.get("id") == appt_id),
        None
    )
    if idx is None:
        return jsonify({"error": "Turno no encontrado"}), 404

    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("pendiente", "confirmado", "cancelado"):
        return jsonify({"error": "Estado inválido"}), 400

    appointments[idx]["status"] = status
    save_appointments(appointments)
    return jsonify(appointments[idx])

@app.route("/api/appointments/cleanup", methods=["POST"])
def cleanup_appointments():
    """
    Elimina turnos cuya fecha sea anterior a la fecha `before` (YYYY-MM-DD)
    que llega en el body JSON.
    """
    data = request.get_json(silent=True) or {}
    before = data.get("before")  # ej: "2025-11-01"

    if not before:
        return jsonify({"error": "Campo 'before' requerido (YYYY-MM-DD)"}), 400

    try:
        limit = datetime.fromisoformat(before)
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido"}), 400

    appointments = load_appointments()
    kept = []
    removed = []

    for a in appointments:
        date_str = a.get("dateISO") or a.get("date")
        if not date_str:
            kept.append(a)
            continue
        try:
            # soporta strings con o sin 'Z'
            d = datetime.fromisoformat(date_str.replace("Z", ""))
        except Exception:
            kept.append(a)
            continue

        if d < limit:
            removed.append(a)
        else:
            kept.append(a)

    save_appointments(kept)
    return jsonify({"removed": len(removed), "kept": len(kept)})

# === SERVICIOS Y ESTILISTAS (BACKEND PERSISTENTE) ===

# valores por defecto por si no existen archivos
DEFAULT_SERVICES = [
    { "id": 1, "name": "Corte Caballero", "duration": 45, "price": 15000 },
    { "id": 2, "name": "Corte Dama", "duration": 60, "price": 20000 },
    { "id": 3, "name": "Tinte Completo", "duration": 90, "price": 40000 },
    { "id": 4, "name": "Barba & Perfilado", "duration": 30, "price": 12000 },
]

DEFAULT_STYLISTS = [
    { "id": 1, "name": "Danilo Dandelo" }
]

@app.route("/api/services", methods=["GET", "POST"])
def services_api():
    """
    GET  -> devuelve lista de servicios (desde services.json o default)
    POST -> reemplaza la lista completa de servicios y la persiste
    """
    if request.method == "GET":
        services = load_json(SERVICES_FILE, DEFAULT_SERVICES)
        return jsonify(services)

    # POST: reemplaza lista completa de servicios
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify({"error": "Se espera una lista de servicios"}), 400

    save_json(SERVICES_FILE, data)
    return jsonify({"ok": True, "count": len(data)})

@app.route("/api/stylists", methods=["GET", "POST"])
def stylists_api():
    """
    GET  -> devuelve lista de estilistas (desde stylists.json o default)
    POST -> reemplaza la lista completa de estilistas y la persiste
    """
    if request.method == "GET":
        stylists = load_json(STYLISTS_FILE, DEFAULT_STYLISTS)
        return jsonify(stylists)

    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify({"error": "Se espera una lista de estilistas"}), 400

    save_json(STYLISTS_FILE, data)
    return jsonify({"ok": True, "count": len(data)})

# === GALERÍA DE FOTOS (TRABAJOS DEL ESTILISTA) ===

def load_gallery():
    return load_json(GALLERY_FILE, [])

def save_gallery(items):
    save_json(GALLERY_FILE, items)

@app.route("/api/gallery", methods=["GET", "POST"])
def gallery_collection():
    """
    GET  -> devuelve la lista de fotos de la galería
    POST -> agrega una nueva entrada (usando URL de imagen)
    """
    items = load_gallery()

    if request.method == "GET":
        return jsonify(items)

    data = request.get_json(silent=True) or {}
    # Esperamos al menos imageUrl
    image_url = data.get("imageUrl")
    if not image_url:
        return jsonify({"error": "imageUrl requerido"}), 400

    title = data.get("title") or ""
    description = data.get("description") or ""

    new_item = {
        "id": int(time.time() * 1000),
        "imageUrl": image_url,
        "title": title,
        "description": description,
        "createdAt": datetime.utcnow().isoformat() + "Z"
    }
    items.append(new_item)
    save_gallery(items)
    return jsonify(new_item), 201

@app.route("/api/gallery/<int:item_id>", methods=["DELETE"])
def delete_gallery_item(item_id):
    items = load_gallery()
    new_items = [it for it in items if it.get("id") != item_id]
    if len(new_items) == len(items):
        return jsonify({"error": "Elemento no encontrado"}), 404
    save_gallery(new_items)
    return jsonify({"ok": True})

@app.route("/api/gallery/upload", methods=["POST"])
def upload_gallery_image():
    """
    Sube un archivo de imagen y crea una entrada en la galería.
    Espera multipart/form-data con:
      - file: archivo de imagen
      - title (opcional)
      - description (opcional)
    """
    if "file" not in request.files:
        return jsonify({"error": "Archivo 'file' requerido"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Nombre de archivo vacío"}), 400

    # nombre único básico
    ext = os.path.splitext(file.filename)[1].lower()
    filename = f"gal_{int(time.time() * 1000)}{ext}"
    file_path = UPLOAD_FOLDER / filename
    file.save(file_path)

    # URL pública (ajustá si tenés proxy, dominio, etc.)
    base_url = request.host_url.rstrip("/")
    image_url = f"{base_url}/uploads/{filename}"

    title = request.form.get("title", "")
    description = request.form.get("description", "")

    items = load_gallery()
    new_item = {
        "id": int(time.time() * 1000),
        "imageUrl": image_url,
        "title": title,
        "description": description,
        "createdAt": datetime.utcnow().isoformat() + "Z"
    }
    items.append(new_item)
    save_gallery(items)

    return jsonify(new_item), 201

@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    """
    Sirve archivos subidos de la carpeta 'uploads'.
    """
    return send_from_directory(UPLOAD_FOLDER, filename)

# === MAIN ===

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
