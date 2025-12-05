from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
from datetime import datetime, date
from pathlib import Path

from twilio.rest import Client  # integración real Twilio

# === RUTAS DE ARCHIVOS ===
BASE_DIR = Path(__file__).resolve().parent
APP_FILE = BASE_DIR / "appointments.json"
SERVICES_FILE = BASE_DIR / "services.json"
STYLISTS_FILE = BASE_DIR / "stylists.json"
GALLERY_FILE = BASE_DIR / "gallery.json"   # 👈 NUEVO

app = Flask(__name__)
# Para pruebas: permitir cualquier origen. Luego podés restringir a tu dominio.
CORS(app, resources={r"/api/*": {"origins": "*"}})

# =========================
#  HELPERS JSON
# =========================

def load_json(path: Path, default):
    """Lee un JSON desde 'path'. Si no existe o falla, devuelve 'default'."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    """Guarda 'data' como JSON en 'path'."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# =========================
#  HELPERS FECHAS / TEL
# =========================

def parse_date_only(value: str):
    """
    Recibe un string ISO (ej: '2025-12-04T14:00:00.000Z' o '2025-12-04')
    y devuelve sólo la fecha (date). Si falla, devuelve None.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except Exception:
        return None


def normalize_phone(raw: str, default_country: str = "54"):
    """
    Normaliza teléfonos para WhatsApp en formato numérico muy simple.

    - Deja sólo dígitos.
    - Si empieza con '00', quita esos dos dígitos (ej: 0054... -> 54...).
    - Si empieza con '0', quita el 0 y antepone el código de país.
    - Si no empieza con el código de país, lo agrega.
    """
    if not raw:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith(default_country):
        return digits

    if digits.startswith("0") and len(digits) >= 10:
        digits = digits[1:]

    return default_country + digits


def get_twilio_client():
    """Construye el cliente de Twilio usando variables de entorno."""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        app.logger.error("Twilio no configurado: faltan TWILIO_ACCOUNT_SID o TWILIO_AUTH_TOKEN")
        return None

    return Client(account_sid, auth_token)


def send_whatsapp_message(phone: str, message: str) -> bool:
    """
    Envía un mensaje real de WhatsApp usando Twilio.
    Devuelve True si Twilio aceptó el envío, False si hubo error.
    """
    client = get_twilio_client()
    if client is None:
        return False

    from_number = os.environ.get("TWILIO_WHATSAPP_FROM")  # ej: 'whatsapp:+14155238886'
    if not from_number:
        app.logger.error("Twilio no configurado: falta TWILIO_WHATSAPP_FROM")
        return False

    to_number = f"whatsapp:+{phone}"

    try:
        msg = client.messages.create(
            from_=from_number,
            to=to_number,
            body=message
        )
        app.logger.info(f"[WHATSAPP] Enviado a {to_number}, SID={msg.sid}")
        return True
    except Exception as e:
        app.logger.error(f"Error enviando WhatsApp a {to_number}: {e}")
        return False


# =========================
#  TURNOS (APPOINTMENTS)
# =========================

def load_appointments():
    """Carga la lista de turnos desde appointments.json."""
    return load_json(APP_FILE, [])


def save_appointments(items):
    """Guarda la lista de turnos en appointments.json."""
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

    # Normalizar teléfono
    raw_contact = (data.get("clientContact") or "").strip()
    data["clientContactNormalized"] = normalize_phone(raw_contact)

    # Límite de máximo 2 turnos por misma fecha & hora (sin cancelados)
    new_date_str = data.get("dateISO") or data.get("date")
    new_time = data.get("time")
    new_date_only = parse_date_only(new_date_str)

    if new_date_only and new_time:
        same_slot_count = 0
        for appt in appointments:
            appt_date_str = appt.get("dateISO") or appt.get("date")
            appt_time = appt.get("time")
            appt_date_only = parse_date_only(appt_date_str)

            if not appt_date_only or not appt_time:
                continue

            if (
                appt_date_only == new_date_only
                and appt_time == new_time
                and appt.get("status", "pendiente") != "cancelado"
            ):
                same_slot_count += 1

        if same_slot_count >= 2:
            return jsonify({
                "error": "Ya hay 2 turnos agendados para esa fecha y hora."
            }), 400

    # upsert por id
    existing_idx = next(
        (i for i, a in enumerate(appointments) if a.get("id") == data["id"]),
        None
    )
    if existing_idx is not None:
        appointments[existing_idx] = data
    else:
        if "status" not in data:
            data["status"] = "pendiente"
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
        limit = datetime.fromisoformat(before).date()
    except ValueError:
        return jsonify({"error": "Formato de fecha inválido"}), 400

    appointments = load_appointments()
    kept = []
    removed = []

    for a in appointments:
        date_str = a.get("dateISO") or a.get("date")
        d = parse_date_only(date_str)
        if not d:
            kept.append(a)
            continue

        if d < limit:
            removed.append(a)
        else:
            kept.append(a)

    save_appointments(kept)
    return jsonify({"removed": len(removed), "kept": len(kept)})


# =========================
#  SERVICIOS / ESTILISTAS
# =========================

DEFAULT_SERVICES = [
    {"id": 1, "name": "Corte Caballero", "duration": 45, "price": 15000},
    {"id": 2, "name": "Corte Dama", "duration": 60, "price": 20000},
    {"id": 3, "name": "Tinte Completo", "duration": 90, "price": 40000},
    {"id": 4, "name": "Barba & Perfilado", "duration": 30, "price": 12000},
]

DEFAULT_STYLISTS = [
    {"id": 1, "name": "Danilo Dandelo"}
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


# =========================
#  GALERÍA DE FOTOS
# =========================

@app.route("/api/gallery", methods=["GET", "POST"])
def gallery_api():
    """
    GET  -> devuelve la lista completa de ítems de galería (gallery.json)
    POST -> 
        - si recibe lista -> reemplaza todo
        - si recibe objeto -> agrega / actualiza por id
    """
    if request.method == "GET":
        items = load_json(GALLERY_FILE, [])
        return jsonify(items)

    data = request.get_json(silent=True)

    # Lista completa -> reemplazar
    if isinstance(data, list):
        save_json(GALLERY_FILE, data)
        return jsonify({"ok": True, "count": len(data)})

    # Objeto único -> agregar / actualizar
    if not isinstance(data, dict):
        return jsonify({"error": "Se espera un objeto o una lista de objetos"}), 400

    items = load_json(GALLERY_FILE, [])
    item_id = data.get("id") or int(datetime.utcnow().timestamp() * 1000)
    data["id"] = item_id

    existing_idx = next(
        (i for i, x in enumerate(items) if x.get("id") == item_id),
        None
    )
    if existing_idx is not None:
        items[existing_idx] = data
    else:
        items.append(data)

    save_json(GALLERY_FILE, items)
    return jsonify(data), 201


# =========================
#  RECORDATORIOS WHATSAPP
# =========================

@app.route("/api/reminders/whatsapp", methods=["POST"])
def whatsapp_reminders():
    """
    Envía recordatorios de WhatsApp para los turnos de una fecha dada.

    Body JSON opcional:
    {
      "date": "YYYY-MM-DD",          # si se omite, usa hoy (UTC)
      "only_confirmed": true/false   # por defecto False => todos menos cancelados
    }
    """
    data = request.get_json(silent=True) or {}

    date_str = data.get("date")
    only_confirmed = bool(data.get("only_confirmed", False))

    if date_str:
        try:
            target_date = datetime.fromisoformat(date_str).date()
        except ValueError:
            return jsonify({"error": "Formato de fecha inválido (usar YYYY-MM-DD)"}), 400
    else:
        target_date = datetime.utcnow().date()

    appointments = load_appointments()
    sent_count = 0
    skipped_no_phone = 0
    skipped_status = 0

    for appt in appointments:
        appt_date_str = appt.get("dateISO") or appt.get("date")
        appt_date_only = parse_date_only(appt_date_str)
        if appt_date_only != target_date:
            continue

        status = appt.get("status", "pendiente")
        if status == "cancelado":
            skipped_status += 1
            continue
        if only_confirmed and status != "confirmado":
            skipped_status += 1
            continue

        phone_norm = appt.get("clientContactNormalized") or normalize_phone(appt.get("clientContact"))
        if not phone_norm:
            skipped_no_phone += 1
            continue

        client_name = appt.get("clientName") or "cliente"
        time_str = appt.get("time") or ""
        service_name = appt.get("serviceName") or "tu servicio"

        message = (
            f"Hola {client_name}, te recordamos tu turno hoy a las {time_str} "
            f"en Dandelo Peluquería para {service_name}. "
            "Si no podés asistir, por favor avisá respondiendo este mensaje. 🤘💈"
        )

        ok = send_whatsapp_message(phone_norm, message)
        if ok:
            sent_count += 1
        else:
            app.logger.error(f"No se pudo enviar WhatsApp a {phone_norm}")

    return jsonify({
        "date": target_date.isoformat(),
        "sent": sent_count,
        "skipped_no_phone": skipped_no_phone,
        "skipped_status": skipped_status,
        "total": len(appointments),
    })
    
@app.route("/api/debug/twilio-test", methods=["POST"])
def twilio_test():
    """
    Prueba simple de envío WhatsApp directo a Twilio.
    Body JSON:
    {
      "phone": "1159121384"   # o "+5491159121384", como prefieras
    }
    """
    data = request.get_json(silent=True) or {}
    raw_phone = data.get("phone")
    if not raw_phone:
        return jsonify({"error": "Campo 'phone' requerido"}), 400

    phone_norm = normalize_phone(raw_phone)  # ej: 5491159121384
    if not phone_norm:
        return jsonify({"error": "No se pudo normalizar el teléfono"}), 400

    ok = send_whatsapp_message(
        phone_norm,
        "⚡ Test Dandelo: si ves este mensaje, Twilio WhatsApp está funcionando."
    )

    return jsonify({
        "ok": ok,
        "phone_normalized": phone_norm
    })
    


# =========================
#  MAIN
# =========================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
