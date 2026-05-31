r"""Quick connectivity test for Gemini API.

Run from project root:
    .\.venv\Scripts\python.exe scripts\test_gemini.py

Verifies that GEMINI_API_KEY is loaded and the model responds.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root regardless of where script is invoked
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

api_key = os.getenv("GEMINI_API_KEY")
if not api_key or api_key == "your_gemini_key_here":
    print("ERROR: GEMINI_API_KEY is not set in .env")
    sys.exit(1)

import google.generativeai as genai

genai.configure(api_key=api_key)

model_name = os.getenv("PRIMARY_MODEL", "gemini-2.5-flash")
print(f"Using model: {model_name}")

model = genai.GenerativeModel(model_name)
prompt = "Reply in exactly one short sentence: confirm you are reachable."

try:
    response = model.generate_content(prompt)
    print("\n--- Gemini response ---")
    print(response.text.strip())
    print("--- end ---\n")
    print("OK: Gemini API is reachable.")
except Exception as e:
    print(f"ERROR: Gemini call failed: {type(e).__name__}: {e}")
    sys.exit(2)
