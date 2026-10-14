
import os
import time
from collections import defaultdict, deque

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/")

MODELS = [
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

SYSTEM_PROMPT = """Tum X1 ho, ek helpful AI assistant.
Seedha, saaf aur practical jawab do.
User ki bhasha mein jawab do; Hindi/Hinglish ho to usi mein.
Coding aur technical sawaalon mein step-by-step madad karo.
Galti ho to use maan kar sahi jawab do.
Safety aur applicable laws ka dhyan rakho.
"""

# Basic per-IP in-memory rate limit.
# Production-scale apps should use a shared rate-limit store.
requests_by_ip = defaultdict(deque)
MAX_REQUESTS = 10
WINDOW_SECONDS = 60


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin", "").rstrip("/")
    if FRONTEND_ORIGIN and origin == FRONTEND_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = FRONTEND_ORIGIN
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/chat", methods=["POST", "OPTIONS"])
def chat():
    origin = request.headers.get("Origin", "").rstrip("/")

    if not FRONTEND_ORIGIN or origin != FRONTEND_ORIGIN:
        return jsonify({"error": "Origin not allowed"}), 403

    if request.method == "OPTIONS":
        return "", 204

    if not API_KEY:
        return jsonify({"error": "Backend API key is not configured"}), 500

    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    ip = ip.split(",")[0].strip()
    now = time.time()
    bucket = requests_by_ip[ip]

    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= MAX_REQUESTS:
        return jsonify({"error": "Too many requests. Try again in a minute."}), 429

    bucket.append(now)

    body = request.get_json(silent=True) or {}
    incoming = body.get("messages")

    if not isinstance(incoming, list) or not incoming:
        return jsonify({"error": "Messages are required"}), 400

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    for item in incoming[-20:]:
        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = item.get("content")

        if role not in ("user", "assistant"):
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        if len(content) > 6000:
            return jsonify({"error": "A message is too long"}), 400

        messages.append({"role": role, "content": content})

    if len(messages) == 1 or messages[-1]["role"] != "user":
        return jsonify({"error": "A user message is required"}), 400

    last_error = "All configured models failed"

    for model in MODELS:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                    "X-Title": "X1",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.75,
                },
                timeout=45,
            )

            if response.ok:
                data = response.json()
                choices = data.get("choices", [])
                if choices:
                    reply = choices[0].get("message", {}).get("content")
                    if isinstance(reply, str) and reply.strip():
                        return jsonify({"reply": reply, "model": model})

                last_error = f"{model}: Empty response"
            else:
                last_error = f"{model}: Provider HTTP {response.status_code}"

                # Try the next model on errors.
                continue

        except requests.RequestException:
            last_error = f"{model}: Network or timeout error"

    # Avoid exposing provider response bodies or secrets to the browser.
    app.logger.warning("X1 model chain failed: %s", last_error)
    return jsonify({
        "error": "AI provider unavailable. Try again later."
    }), 502


@app.get("/")
def home():
    return jsonify({
        "success": True,
        "server": "X1 AI Backend",
        "status": "online",
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
