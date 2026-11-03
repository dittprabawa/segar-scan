
"use strict";

const API_URL = "/predict";
// const form = new FormData();
// form.append("file", Blob, "image.png")
const MAX_SIDE = 1024;
const TIMEOUT_MS = 30000;
const el = (id) => document.getElementById(id);

const cameraScreen = el("camera-section");
const resultScreen = el("result-section");
const startButton = el("btn-start");
const captureButton = el("btn-capture");
const nativeButton = el("btn-native");
const uploadButton = el("btn-upload");
const againButton = el("btn-again");
const loading = el("loading");
const outcome = el("outcome");
const preview = el("preview");
const score = el("skor");
const category = el("label");
const kind = el("jenis");
const extra = el("extra");
const disclaimer = el("disclaimer");
const loadingText = el("loading-text");

let busy = false;
let previewUrl = null;
let currentRequest = null;
let slowTimer = null;
let timeoutTimer = null;

function showCamera() {
  currentRequest?.abort();
  clearTimeout(slowTimer);
  clearTimeout(timeoutTimer);
  currentRequest = null;
  busy = false;

  cameraScreen.hidden = false;
  resultScreen.hidden = true;
  loading.hidden = true;
  outcome.hidden = true;
  againButton.hidden = false;

  for (const button of [startButton, captureButton, nativeButton, uploadButton]) {
    button.disabled = false;
  }

  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  preview.removeAttribute("src");
}

function showResultScreen() {
  cameraScreen.hidden = true;
  resultScreen.hidden = false;
  window.scrollTo(0, 0);
}

function showMessage(title, detail) {
  loading.hidden = true;
  outcome.hidden = false;
  kind.textContent = "";
  score.textContent = "";
  category.className = "category warn";
  category.textContent = title;
  extra.textContent = detail;
  disclaimer.hidden = true;
}

function showPreview(blob) {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = URL.createObjectURL(blob);
  preview.src = previewUrl;
}

async function compressImage(source, width, height) {
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  const canvas = document.createElement("canvas");

  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));

  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);

  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/jpeg", 0.8)
  );

  if (!blob) throw new Error("Foto gagal diproses. Coba foto lain.");
  return blob;
}

async function prepareFile(file) {
  if (!file.type.startsWith("image/") && !/\.(heic|heif)$/i.test(file.name)) {
    throw new Error("Pilih berkas gambar yang didukung.");
  }

  try {
    const bitmap = await createImageBitmap(file);

    try {
      return {
        blob: await compressImage(bitmap, bitmap.width, bitmap.height),
        name: "image.jpg"
      };
    } finally {
      bitmap.close?.();
    }
  } catch {
    // Jika konversi tidak didukung, kirim file asli.
    // Backend harus mendukung format file tersebut.
    return { blob: file, name: file.name || "foto" };
  }
}

function showPrediction(data) {
  loading.hidden = true;
  outcome.hidden = false;

  if (data?.status === "ditolak") {
    showMessage(
      data.label || "Foto belum dapat dinilai",
      data.alasan || "Coba foto ulang dengan lebih jelas."
    );
    return;
  }

  const value = Number(data?.skor);

  if (data?.skor == null || !Number.isFinite(value)) {
    showMessage(
      "Hasil belum tersedia",
      "Server belum mengirim skor yang valid. Silakan coba lagi."
    );
    return;
  }

  if (data.skor_min != null && data.skor_maks != null) {
    score.textContent =
      `${Math.round(data.skor_min)}–${Math.round(data.skor_maks)}%`;
  } else {
    score.textContent = `${Math.round(value)}%`;
  }

  kind.textContent = data.jenis && data.jenis !== "unknown"
    ? `Perkiraan: ${data.jenis}`
    : "Perkiraan kesegaran";

  const level = value >= 60 ? "good" : value >= 40 ? "warn" : "bad";
  const symbol = value >= 60 ? "✓" : value >= 40 ? "!" : "✕";

  category.className = `category ${level}`;
  category.textContent = `${symbol} ${data.label || "Hasil perkiraan"}`;

  const lines = [];

  if (data.keyakinan === "rendah") {
    lines.push("⚠ Kami kurang yakin. Coba foto ulang dari lebih dekat.");
  }

  if (data.sumber === "model_lokal") {
    lines.push("⚠ Penilai cadangan, hasil kurang akurat.");
  }

  if (data.saran) lines.push(data.saran);
  if (data.ciri) lines.push(data.ciri);

  extra.textContent = lines.join("\n");
  disclaimer.hidden = false;
}

async function sendImage(blob, name) {
  showResultScreen();
  loading.hidden = false;
  outcome.hidden = true;
  againButton.hidden = true;
  loadingText.textContent = "Biasanya 3–5 detik";

  currentRequest = new AbortController();

  slowTimer = setTimeout(() => {
    loadingText.textContent =
      "Agak lama. Sinyal mungkin lemah. Tunggu sebentar.";
  }, 10000);

  timeoutTimer = setTimeout(() => currentRequest?.abort(), TIMEOUT_MS);

  try {
    if (!navigator.onLine) throw new Error("offline");

    const form = new FormData();
    form.append("file", blob, name);

    const response = await fetch(API_URL, {
      method: "POST",
      body: form,
      signal: currentRequest.signal
    });

    if (!response.ok) {
      const details = {
        400: "Foto bukan gambar yang didukung.",
        413: "Foto terlalu besar. Coba foto ulang.",
        503: "Penilai sedang sibuk. Coba lagi sebentar."
      };

      showMessage(
        "Belum berhasil",
        details[response.status] || "Server sedang bermasalah. Coba lagi."
      );
    } else {
      showPrediction(await response.json());
    }
  } catch (error) {
    if (error.name === "AbortError") {
      showMessage(
        "Waktu habis",
        "Penilaian lebih dari 30 detik. Periksa sinyal lalu coba lagi."
      );
    } else if (!navigator.onLine || error.message === "offline") {
      showMessage(
        "Tidak ada koneksi",
        "Periksa sinyal lalu coba lagi."
      );
    } else {
      showMessage(
        "Belum terhubung",
        "Tidak bisa menghubungi server. Periksa koneksi lalu coba lagi."
      );
    }
  } finally {
    clearTimeout(slowTimer);
    clearTimeout(timeoutTimer);
    currentRequest = null;
    busy = false;
    againButton.hidden = false;
  }
}

async function processFile(file) {
  if (!file || busy) return;
  busy = true;

  try {
    const { blob, name } = await prepareFile(file);
    showPreview(blob);
    await sendImage(blob, name);
  } catch (error) {
    showResultScreen();
    showMessage(
      "Foto tidak dapat dibuka",
      error.message || "Silakan pilih foto lain."
    );
    againButton.hidden = false;
    busy = false;
  }
}

startButton.addEventListener("click", async () => {
  if (busy) return;

  busy = true;
  startButton.disabled = true;

  try {
    const ready = await startCamera();

    startButton.hidden = ready;
    captureButton.hidden = !ready;
  } finally {
    startButton.disabled = false;
    busy = false;
  }
});

captureButton.addEventListener("click", async () => {
  if (busy) return;

  if (!cameraVideo.videoWidth) {
    cameraMessage.textContent = "Kamera belum siap. Coba sebentar lagi.";
    return;
  }

  busy = true;

  try {
    const blob = await compressImage(
      cameraVideo,
      cameraVideo.videoWidth,
      cameraVideo.videoHeight
    );

    showPreview(blob);
    await sendImage(blob, "image.jpg");
  } catch {
    cameraMessage.textContent = "Foto gagal diambil. Coba lagi.";
    busy = false;
  }
});

uploadButton.addEventListener("click", () => {
  if (!busy) el("file-input").click();
});

nativeButton.addEventListener("click", () => {
  if (!busy) el("native-input").click();
});

for (const id of ["file-input", "native-input"]) {
  el(id).addEventListener("change", (event) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    processFile(file);
  });
}

againButton.addEventListener("click", showCamera);

el("btn-info").addEventListener("click", () => {
  el("info-dialog").showModal();
});

el("btn-close").addEventListener("click", () => {
  el("info-dialog").close();
});