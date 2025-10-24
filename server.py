from flask import Flask, request, jsonify, send_from_directory, render_template_string
import datetime, threading, time, os, cv2, json, requests
import RPi.GPIO as GPIO
import shutil
from pathlib import Path

app = Flask(__name__)

IMAGE_DIR = "/home/gateguard/door_monitor/images"
os.makedirs(IMAGE_DIR, exist_ok=True)

is_recording = False
latest_photo = None

sensor_thread = None
sensor_running = False
people_inside = 0
callback_url = None

HTML_LATEST = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>Latest Snapshot</title>
    <meta http-equiv="refresh" content="2"> <!-- refresh every 2 seconds -->
    <style>
      body { font-family: Arial, sans-serif; text-align: center; margin: 20px; }
      img { max-width: 90vw; max-height: 80vh; border: 1px solid #ccc; }
      .info { margin-top: 12px; color: #666; }
    </style>
  </head>
  <body>
    <h2>Latest Snapshot</h2>
    {% if filename %}
      <img src="/images/{{ filename }}" alt="Latest snapshot"/>
      <div class="info">File: {{ filename }} &nbsp; | &nbsp; Updated: {{ updated }}</div>
    {% else %}
      <div>No snapshot yet. Trigger POST /snapshot to take one.</div>
    {% endif %}
  </body>
</html>
"""

def simulate_camera_loop():
    global latest_photo
    cap = cv2.VideoCapture(0)

    while is_recording:
        ret, frame = cap.read()
        if ret:
            filename = f"photo_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
            filepath = os.path.join(IMAGE_DIR, filename)
            cv2.imwrite(filename, frame)
            latest_photo = filename

        time.sleep(0.3) # simulate ~3 FPS
    cap.release()

@app.route("/start", methods=["POST"])
def start_camera():
    global is_recording
    if not is_recording:
        is_recording = True
        threading.Thread(target=simulate_camera_loop, daemon=True).start()
        return jsonify({"status": "started", "time": datetime.datetime.now().isoformat()})
    else:
        return jsonify({"status": "already_running"})

@app.route("/stop", methods=["POST"])
def stop_camera():
    global is_recording
    is_recording = False
    return jsonify({"status": "stopped"})

@app.route("/snapshot", methods=["POST"])
def snapshot():
    global latest_photo
    # open camera 0 corresponds to /dev/video0
    cam = cv2.VideoCapture(0)
    if not cam.isOpened():
        return jsonify({"status": "error", "message": "Cannot open camera"}), 500

    ret, frame = cam.read()
    cam.release()
    if not ret:
        return jsonify({"status": "error", "message": "Failed to capture frame"}), 500

    # based on request data, an event name can be tagged (optional)
    body = request.get_json(silent=True) or {}
    event = body.get("event", "manual")

    filename = datetime.datetime.now().strftime(f"{event}_%Y%m%d_%H%M%S.jpg")
    path = os.path.join(IMAGE_DIR, filename)
    cv2.imwrite(path, frame)

    latest_photo = filename
    print(f"[{datetime.datetime.now().isoformat()}] Snapshot saved: {path}")

    # optional: return image URL for CPEE recording
    return jsonify({"status": "ok", "file": filename, "url": f"/images/{filename}"}), 201

@app.route("/images/<path:filename>")
def images(filename):
    """Provide static image service"""
    return send_from_directory(IMAGE_DIR, filename)

@app.route("/latest")
def latest():
    """Web page to display the latest image, auto-refreshing every 2 seconds"""
    if latest_photo:
        fpath = os.path.join(IMAGE_DIR, latest_photo)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            updated = mtime.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            updated = "N/A"
        return render_template_string(HTML_LATEST, filename=latest_photo, updated=updated)
    else:
        return render_template_string(HTML_LATEST, filename=None, updated="N/A")

@app.route("/status", methods=["GET"])
def status():
    return jsonify({
        "recording": is_recording,
        "latest_photo": latest_photo,
        "time": datetime.datetime.now().isoformat()
    })

@app.route("/cleanup", methods=["POST"])
def cleanup_photos():
    """delete photos older than specified minutes (default 2 minutes)"""
    body = request.get_json(silent=True) or {}
    keep_minutes = request.json.get("older_than", 2)
    cutoff = datetime.datetime.now() - datetime.timedelta(minutes=keep_minutes)
    deleted = 0
    for fname in os.listdir(IMAGE_DIR):
        fpath = os.path.join(IMAGE_DIR, fname)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            if mtime < cutoff:
                os.remove(fpath)
                deleted += 1
        except Exception:
            pass
    print(f"[Cleanup] Deleted {deleted} old photos.")
    return jsonify({"status": "cleanup_done", "deleted": deleted})


@app.route("/save_event_photos", methods=["POST"])
def save_event_photos():
    """
    Copy photos whose timestamp falls in the given [start_time, end_time] interval
    to /images/{label}/
    """
    print("=== /save_event_photos called ===")
    print("Raw body:", request.get_data(as_text=True))
    print("JSON parsed:", request.get_json(silent=True))
    print("Headers:", dict(request.headers))

    body = request.get_json(silent=True)
    if not body:
        body = request.form.to_dict()

    label = body.get("label")
    start_time = body.get("start_time")
    end_time = body.get("end_time")

    if not (start_time and end_time):
        return jsonify({"error": "Missing start_time or end_time"}), 400

    try:
        t_start = datetime.datetime.fromisoformat(start_time)
        t_end = datetime.datetime.fromisoformat(end_time)
    except ValueError:
        return jsonify({"error": "Invalid datetime format"}), 400

    target_dir = os.path.join(IMAGE_DIR, label)
    os.makedirs(target_dir, exist_ok=True)

    saved_files = []

    for fname in sorted(os.listdir(IMAGE_DIR)):
        # Skip subfolders like /entry or /exit
        if not fname.lower().endswith(".jpg"):
            continue
        fpath = os.path.join(IMAGE_DIR, fname)
        try:
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            if t_start <= mtime <= t_end:
                dest_path = os.path.join(target_dir, fname)
                shutil.copy2(fpath, dest_path)
                saved_files.append(fname)
        except Exception as e:
            print(f"[SaveEvent] Error checking {fname}: {e}")

    print(f"[SaveEvent] {label.upper()} event: saved {len(saved_files)} photos")
    return jsonify({
        "status": "saved",
        "label": label,
        "count": len(saved_files),
        "files": saved_files
    })


ENTRY_PIN = 17
EXIT_PIN = 27

def monitor_sensors():
    global people_inside, callback_url, sensor_running
    print("[Sensor] Monitoring thread started...")
    if not GPIO.getmode():
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(ENTRY_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        GPIO.setup(EXIT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    state = "idle"
    last_event_time = 0.0
    COOLDOWN = 1.2
    TIME_WINDOW = 0.6
    SLEEP_INTERVAL = 0.02

    entry_trigger_time = None
    exit_trigger_time = None

    while sensor_running:
        entry_state = not GPIO.input(ENTRY_PIN)
        exit_state = not GPIO.input(EXIT_PIN)
        now = datetime.datetime.now()
        now_ts = now.timestamp()

        if now_ts - last_event_time < COOLDOWN:
            time.sleep(SLEEP_INTERVAL)
            continue

        # ---- entry ----
        if state == "idle" and entry_state:
            entry_trigger_time = now
            state = "waiting_exit"

        elif state == "waiting_exit":
            if exit_state and ((datetime.datetime.now() - entry_trigger_time).total_seconds() <= TIME_WINDOW):
                exit_trigger_time = datetime.datetime.now()
                people_inside += 1
                print(f"[{now.strftime('%H:%M:%S')}] ENTRY | People inside: {people_inside}")
                _trigger_callback("entry", people_inside, entry_trigger_time, exit_trigger_time)
                last_event_time = now_ts
                state = "idle"

            elif (datetime.datetime.now() - entry_trigger_time).total_seconds() > TIME_WINDOW:
                state = "idle"

        # ---- exit ----
        elif state == "idle" and exit_state:
            state = "waiting_entry"
            exit_trigger_time = now

        elif state == "waiting_entry":
            if entry_state and ((datetime.datetime.now() - exit_trigger_time).total_seconds() <= TIME_WINDOW):
                entry_trigger_time = datetime.datetime.now()
                people_inside = max(people_inside - 1, 0)
                print(f"[{now.strftime('%H:%M:%S')}] EXIT | People inside: {people_inside}")
                _trigger_callback("exit", people_inside, exit_trigger_time, entry_trigger_time)
                last_event_time = now_ts
                state = "idle"
            elif (datetime.datetime.now() - exit_trigger_time).total_seconds() > TIME_WINDOW:
                state = "idle"

        time.sleep(SLEEP_INTERVAL)

    GPIO.cleanup()
    print("[Sensor] Monitoring thread stopped.")

def _trigger_callback(direction, count, start_dt, end_dt):
    """async callback CPEE"""
    global callback_url
    if not callback_url:
        return
    payload = {
        "direction": direction,
        "people_inside": count,
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat()
    }
    try:
        print(f"[Sensor] Sending callback → {callback_url}")
        print(f"[Sensor] Payload: {payload}")
        r = requests.put(callback_url, json=payload, timeout=5)
        print(f"[Sensor] Callback response: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[Sensor] Callback failed: {e}")

def start_sensor_monitor(new_callback_url):
    """start or update monitoring thread"""
    global sensor_running, sensor_thread, callback_url

    callback_url = new_callback_url
    print(f"[Sensor] Register new callback URL: {callback_url}")

    if not sensor_running:
        sensor_running = True
        sensor_thread = threading.Thread(target=monitor_sensors, daemon=True)
        sensor_thread.start()
        print("[Sensor] Monitoring started.")
    else:
        print("[Sensor] Already running — callback updated.")


def stop_sensor_monitor():
    """stop monitoring"""
    global sensor_running
    sensor_running = False

@app.route("/start_sensor", methods=["POST"])
def start_sensor():
    """CPEE keeps sensor monitoring"""
    global sensor_running

    cb_url = request.headers.get("CPEE_CALLBACK")
    print("[CPEE CALLBACK URL] Received:", cb_url)

    if not cb_url:
        return jsonify({"error": "Missing CPEE_CALLBACK header"}), 400

    start_sensor_monitor(cb_url)

    # Inform CPEE that monitoring is active and callbacks will be sent
    resp = jsonify({"status": "sensor_monitoring"})
    resp.headers["CPEE-CALLBACK"] = "true"
    return resp



@app.route("/debug", methods=["GET", "POST", "PUT"])
def debug_headers():
    print("\n===== Received Request =====")
    print(f"Method: {request.method}")
    print(f"Path: {request.path}")
    print("---- Headers ----")
    for k, v in request.headers.items():
        print(f"{k}: {v}")
    print("---- Body ----")
    print(request.get_data(as_text=True))
    print("==============================\n")

    # Simulate asynchronous behavior: inform CPEE that I will callback later
    response = app.response_class(
        response=json.dumps({"note": "Callback test triggered"}),
        status=202,
        mimetype="application/json",
        headers={"CPEE-CALLBACK": "true"}  # Inform CPEE: I will callback later with PUT
    )
    return response

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)