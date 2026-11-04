/**
 * Kecilkan foto di browser sebelum diunggah ke /predict.
 * - Sisi terpanjang dibatasi (default 768 px), foto kecil tidak diperbesar.
 * - Orientasi dari kamera HP dihormati (foto tidak terputar).
 * - Hasilnya JPEG, biasanya 80-200 KB.
 */
async function kecilkanFoto(file, sisiMaks = 768, kualitas = 0.82) {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  const skala = Math.min(1, sisiMaks / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * skala);
  const h = Math.round(bitmap.height * skala);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#fff"; // latar putih untuk PNG transparan, supaya tidak jadi hitam di JPEG
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close?.();

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", kualitas));
  if (!blob) throw new Error("Gagal mengecilkan foto");
  return new File([blob], "foto.jpg", { type: "image/jpeg" });
}

/**
 * Contoh pemakaian saat mengirim ke backend.
 * `file` = berkas dari <input type="file"> atau hasil kamera.
 */
async function kirimFoto(file) {
  let kirim = file;
  try {
    kirim = await kecilkanFoto(file);
  } catch (e) {
    console.warn("Pengecilan gagal, kirim foto asli:", e);
  }

  const form = new FormData();
  form.append("file", kirim);

  const res = await fetch("/predict", { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Gagal (${res.status})`);
  }
  return res.json();
}