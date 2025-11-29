from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
from datetime import datetime
from flask import request, jsonify

APP_FILE = "appointments.json"

app = Flask(__name__)
# Para pruebas: permitir cualquier origen. Podés restringir luego a tu dominio de Netlify.
CORS(app, resources={r"/api/*": {"origins": "*"}})


def load_appointments():
    if not os.path.exists(APP_FILE):
        return []
    try:
        with open(APP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []


def save_appointments(items):
    with open(APP_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
