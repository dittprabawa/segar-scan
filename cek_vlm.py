import traceback
from dotenv import load_dotenv
load_dotenv()
from PIL import Image
from backend.app.services import vlm

print("vlm tersedia:", vlm.tersedia())
img = Image.open("data/raw/bayam/1.jpeg").convert("RGB")
try:
    print(vlm.nilai(img))
except Exception:
    traceback.print_exc()
