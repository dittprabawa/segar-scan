"""Uji kunci API dan lihat model Gemini yang tersedia. Jalankan: python cek_gemini.py"""
import os
from dotenv import load_dotenv
load_dotenv()
from google import genai

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
for m in client.models.list():
    nama = m.name.replace("models/", "")
    if "gemini" in nama and "flash" in nama:
        print(nama)
