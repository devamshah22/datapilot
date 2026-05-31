r"""Quick connectivity test for Groq API.

Run from project root:
    .\.venv\Scripts\python.exe scripts\test_groq.py

Verifies that GROQ_API_KEY is loaded and a model responds.
Groq exposes an OpenAI-compatible API; we hit it with httpx directly
to avoid adding a dependency just for a smoke test.
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    print("ERROR: GROQ_API_KEY is not set in .env")
    sys.exit(1)

# Use a current Groq-hosted model. llama-3.3-70b-versatile is a stable choice.
model = "llama-3.3-70b-versatile"
print(f"Using model: {model}")

try:
    response = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": "Reply in exactly one short sentence: confirm you are reachable.",
                }
            ],
            "max_tokens": 50,
        },
        timeout=30.0,
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"].strip()
    print("\n--- Groq response ---")
    print(content)
    print("--- end ---\n")
    print("OK: Groq API is reachable.")
except httpx.HTTPStatusError as e:
    print(f"ERROR: HTTP {e.response.status_code}: {e.response.text}")
    sys.exit(2)
except Exception as e:
    print(f"ERROR: Groq call failed: {type(e).__name__}: {e}")
    sys.exit(3)
