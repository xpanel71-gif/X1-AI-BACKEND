
import os
import time
from collections import defaultdict, deque

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024

# Configure these in Render Environment Variables.
API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "").rstrip("/")

# Keep the configured models and fall back when one fails.
MODELS = [
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

SYSTEM_PROMPT = """You are X1, a helpful and capable AI assistant.

PERSONALITY AND LANGUAGE:
- Reply in the user's language. Use Hindi/Hinglish when appropriate.
- Be direct, practical, friendly, and accurate.
- Preserve the existing X1 personality and user preferences.
- Avoid unnecessary refusals for ordinary, legitimate requests.
- Explain limitations honestly instead of pretending to have capabilities.

CODING AND DEVELOPMENT:
- When code is requested, provide actual implementation code, not just an explanation.
- Help with Python, HTML, CSS, JavaScript, APIs, backend systems,
  websites, Android development, games, automation, and debugging.
- For complete projects, provide each filename and its complete contents.
- Preserve existing code and features unless a change is necessary.
- For bugs, explain the cause and provide corrected code.
- Include dependencies, setup steps, and instructions to run the project.
- If essential details are missing, ask a concise clarifying question.
- Never claim code was tested unless it actually was.
- Never expose API keys or credentials in frontend code.
- If a project is too large for one response, split it into clear parts.

RESPONSE FORMAT:
1. Briefly identify the goal.
2. Provide the actual solution or code.
3. Identify where each file belongs.
4. Explain installation and run instructions.
5. Describe expected results and troubleshooting steps.

ACCURACY:
- Do not invent results, files, or successful deployments.
- Follow applicable safety requirements and laws.
"""

# Basic in-memory per-IP rate limiting.
# This resets when the server restarts and is not shared across workers.
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
        app.logger.error("OPENROUTER_API_KEY is not configured")
        return jsonify({"error": "Backend API key is not configured"}), 500

    # Use the direct connection address for basic rate limiting.
    ip = request.remote_addr or "unknown"
    now = time.time()
    bucket = requests_by_ip[ip]

    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= MAX_REQUESTS:
        return jsonify({
            "error": "Too many requests. Try again in a minute."
        }), 429

    bucket.append(now)

    body = request.get_json(silent=True)

    if not isinstance(body, dict):
        return jsonify({"error": "A valid JSON body is required"}), 400

    incoming = body.get("messages")

    if not isinstance(incoming, list) or not incoming:
        return jsonify({"error": "Messages are required"}), 400

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Keep only the latest 20 conversation messages.
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

        messages.append({
            "role": role,
            "content": content.strip()
        })

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
                    "X-Title": "X1 AI",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.4,
                    "max_tokens": 4096,
                },
                timeout=60,
            )

            if not response.ok:
                last_error = (
                    f"{model}: Provider HTTP {response.status_code}"
                )
                continue

            data = response.json()
            choices = data.get("choices", [])

            if not isinstance(choices, list) or not choices:
                last_error = f"{model}: Empty response"
                continue

            message = choices[0].get("message", {})
            reply = message.get("content")

            if isinstance(reply, str) and reply.strip():
                return jsonify({
                    "reply": reply.strip(),
                    "model": model
                })

            last_error = f"{model}: Empty response"

        except (requests.RequestException, ValueError) as exc:
            # Log a short error without logging the API key.
            last_error = f"{model}: {type(exc).__name__}"
            continue

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
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000"))
    )
