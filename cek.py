import pandas as pd
from PIL import Image
from ml.predict import load_model, predict

m = load_model("models/segarscan.pt")
df = pd.read_csv("data/labels/skor_kesegaran.csv")
for _, r in df.iterrows():
    img = Image.open(f"data/raw/{r['jenis']}/{r['nama_file']}").convert("RGB")
    print(r["nama_file"], "label:", r["skor"], "prediksi:", predict(img, m))
