from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os

app = Flask(__name__)
CORS(app)

DB_FILE = "appointments.db.json"

# Crear archivo si no existe
if not os.path.exists(DB_FILE):
    with open(DB_FILE, "w") as f:
        json.dump([], f)

def load_db():
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)

@app.get("/ping")
def ping():
    return {"status": "ok"}, 200

@app.get("/api/appointments")
def get_appointments():
    return jsonify(load_db()), 200

@app.post("/api/appointments")
def create_appointment():
    data = request.json
    db = load_db()
    db.append(data)
    save_db(db)
    return jsonify({"status": "ok", "saved": data}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
