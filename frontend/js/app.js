
"use strict";

const API_URL = "/predict";
const MAX_SIDE = 1024;
const TIMEOUT_MS = 30000;
const el = (id) => document.getElementById(id);
const MAX_FILE_SIZE = 5 * 1024 * 1024;

const cameraScreen = el("camera-section");
const resultScreen = el("result-section");
const startButton = el("btn-start");
const captureButton = el("btn-capture");
const nativeButton = el("btn-native");
const uploadButton = el("btn-upload");
const againButton = el("btn-again");
const fileInput = el("file-input");
const nativeInput = el("native-input");
const loading = el("loading");
const loadingText = el("loading-text");
const outcome = el("outcome");
const preview = el("preview");
const score = el("skor");
const category = el("label");
const kind = el("jenis");
const extra = el("extra");
const disclaimer = el("disclaimer");

let busy = false;
let previewUrl = null;
let slowTimer = null;
let timeoutTimer = null;

// Kembali ke halaman kamera

function showCamera() {
    stopCamera();

    cameraScreen.hidden = false;
    resultScreen.hidden = true;
    loading.hidden = true;
    outcome.hidden = true;

    startButton.hidden = false;
    captureButton.hidden = true;
    againButton.hidden = false;

    busy = false;

    for (const button of [
        startButton, captureButton,
        nativeButton, uploadButton
    ]) {
        button.disabled = false;
    }

    if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
    }

    previewUrl = null;
    preview.removeAttribute("src");
    window.scrollTo(0, 0);
}

// Membuka halaman hasil
function showResultScreen() {
    stopCamera();

    cameraScreen.hidden = true;
    resultScreen.hidden = false;

    window.scrollTo(0, 0);
}

// Menampilkan pesan
function showMessage(title, detail) {
    loading.hidden = true;
    outcome.hidden = false;

    kind.textContent = "";
    score.textContent = "";

    category.className = "category warn";
    category.textContent = title;

    extra.textContent = detail || "";
    disclaimer.hidden = true;
}

// Preview foto
function showPreview(blob) {
    if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
    }

    previewUrl = URL.createObjectURL(blob);
    preview.src = previewUrl;
}

// Kompresi gambar dari galeri
async function compressImage(source, width, height) {
    const scale = Math.min(
        1,
        MAX_SIDE / Math.max(width, height)
    );

    const canvas = document.createElement("canvas");

    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));

    const ctx = canvas.getContext("2d");

    if (!ctx) {
        throw new Error("Foto gagal diproses.");
    }

    ctx.drawImage(
        source, 0, 0,
        canvas.width, canvas.height
    );

    const blob = await new Promise((resolve) => {
        canvas.toBlob(resolve, "image/jpeg", 0.8);
    });

    if (!blob) {
        throw new Error("Foto gagal diproses.");
    }

    return blob;
}

// Menyiapkan foto upload
async function prepareFile(file) {
    if (
        !file.type.startsWith("image/") &&
        !/\.(heic|heif)$/i.test(file.name)
    ) {
        throw new Error("Pilih file gambar yang valid.");
    }

    try {
        const bitmap = await createImageBitmap(file);

        try {
            return {
                blob: await compressImage(
                    bitmap,
                    bitmap.width,
                    bitmap.height
                ),
                name: "image.jpg"
            };
        } finally {
            bitmap.close?.();
        }

    } catch {
        // Fallback apabila konversi tidak didukung.
        return {
            blob: file,
            name: file.name || "foto"
        };
    }
}

// Menampilkan hasil AI
function showPrediction(data) {
    loading.hidden = true;
    outcome.hidden = false;

    if (data?.status === "ditolak") {
        showMessage(
            data.label || "Foto belum dapat dinilai",
            data.alasan || "Coba foto ulang lebih dekat."
        );
        return;
    }

    const value = Number(data?.skor);

    if (
        data?.skor == null ||
        !Number.isFinite(value)
    ) {
        showMessage(
            "Hasil belum tersedia",
            "Server belum mengirim skor yang valid."
        );
        return;
    }

    if (
        data.skor_min != null &&
        data.skor_maks != null
    ) {
        score.textContent =
            `${Math.round(data.skor_min)}–${Math.round(data.skor_maks)}%`;
    } else {
        score.textContent = `${Math.round(value)}%`;
    }

    kind.textContent =
        data.jenis && data.jenis !== "unknown"
            ? `Perkiraan: ${data.jenis}`
            : "Perkiraan kesegaran";

    const level =
        value >= 60 ? "good" :
        value >= 40 ? "warn" : "bad";

    const symbol =
        value >= 60 ? "✓" :
        value >= 40 ? "!" : "✕";

    category.className = `category ${level}`;
    category.textContent =
        `${symbol} ${data.label || "Hasil perkiraan"}`;

    const lines = [];

    if (data.jenis_lokal?.jenis) {
        lines.push(`ML lokal (eksperimental): ${data.jenis_lokal.jenis}. Dilatih dari ${data.jenis_lokal.jumlah_latih} foto terverifikasi.`);
        if (data.jenis_lokal.jenis !== data.jenis) {
            lines.push("Prediksi jenis berbeda; perlu verifikasi manusia.");
        }
    }

    if (data.keyakinan === "rendah") {
        lines.push(
            "⚠ Kami kurang yakin. Coba foto ulang lebih dekat."
        );
    }

    if (data.sumber === "model_lokal") {
        lines.push(
            "⚠ Penilai cadangan, hasil kurang akurat."
        );
    }

    if (data.saran) {
        lines.push(data.saran);
    }

    if (data.ciri) {
        lines.push(data.ciri);
    }

    extra.textContent = lines.join("\n");
    disclaimer.hidden = false;
}

// Mengirim foto ke backend AI
async function sendImage(blob, name) {
    showResultScreen();

    loading.hidden = false;
    outcome.hidden = true;
    againButton.hidden = true;

    loadingText.textContent =
        "Sedang menilai… biasanya 3–5 detik";

    const controller = new AbortController();

    slowTimer = setTimeout(() => {
        loadingText.textContent =
            "Agak lama. Sinyal mungkin lemah, tunggu sebentar.";
    }, 10000);

    timeoutTimer = setTimeout(() => {
        controller.abort();
    }, TIMEOUT_MS);

    try {
        if (!navigator.onLine) {
            throw new Error("offline");
        }

        const form = new FormData();
        form.append("file", blob, name);

        const response = await fetch(API_URL, {
            method: "POST",
            body: form,
            signal: controller.signal
        });

        if (!response.ok) {
            const messages = {
                400: "Foto tidak valid atau format gambar tidak didukung.",
                413: "Foto terlalu besar. Coba foto ulang.",
                422: "Format upload tidak sesuai.",
                503: "Penilai AI sedang tidak tersedia. Coba lagi nanti."
            };

            showMessage(
                "Belum berhasil",
                messages[response.status] ||
                "Server sedang bermasalah."
            );
            return;
        }

        const data = await response.json();
        showPrediction(data);

    } catch (error) {
        if (error.name === "AbortError") {
            showMessage(
                "Waktu habis",
                "Penilaian lebih dari 30 detik. Periksa koneksi."
            );

        } else if (
            !navigator.onLine ||
            error.message === "offline"
        ) {
            showMessage(
                "Tidak ada koneksi",
                "Periksa sinyal internet lalu coba lagi."
            );

        } else {
            console.error(
                "ERROR PREDIKSI SEGERSCAN:",
                error
            );

            showMessage(
                "Belum terhubung",
                "Tidak dapat menghubungi server SegarScan."
            );
        }

    } finally {
        clearTimeout(slowTimer);
        clearTimeout(timeoutTimer);

        busy = false;
        againButton.hidden = false;
    }
}

// Memproses foto dari galeri / kamera HP
async function processFile(file) {
    if (!file || busy) return;

    busy = true;

    try {
        const prepared = await prepareFile(file);

        showPreview(prepared.blob);

        await sendImage(
            prepared.blob,
            prepared.name
        );

    } catch (error) {
        showResultScreen();

        showMessage(
            "Foto tidak dapat dibuka",
            error.message || "Coba foto lain."
        );

        againButton.hidden = false;
        busy = false;
    }
}

// Tombol Nyalakan Kamera
startButton.addEventListener("click", async () => {
    if (busy) return;

    busy = true;
    startButton.disabled = true;

    try {
        const ready = await startCamera();

        startButton.hidden = ready;
        captureButton.hidden = !ready;

    } catch (error) {
        console.error(
            "ERROR TOMBOL KAMERA:",
            error
        );

        cameraMessage.textContent =
            "Kamera tidak dapat diaktifkan.";

    } finally {
        busy = false;
        startButton.disabled = false;
    }
});

// Tombol Ambil Foto dengan crop tengah
captureButton.addEventListener("click", async () => {
    if (busy) return;

    if (!cameraVideo.videoWidth) {
        cameraMessage.textContent =
            "Kamera belum siap. Coba lagi.";
        return;
    }

    busy = true;

    try {
        // Crop tengah untuk fokus satu jenis bahan dapuran
        const blob = await captureCenterCrop(cameraVideo);

        showPreview(blob);

        await sendImage(blob, "image.jpg");

    } catch (error) {
        console.error(
            "ERROR AMBIL FOTO:",
            error
        );

        cameraMessage.textContent =
            "Gagal mengambil foto. Coba lagi.";

        busy = false;
    }
});

// Tombol Kamera HP
nativeButton.addEventListener("click", () => {
    if (!busy) {
        nativeInput.click();
    }
});

// Tombol Galeri
uploadButton.addEventListener("click", () => {
    if (!busy) {
        fileInput.click();
    }
});

// Input foto
for (const input of [fileInput, nativeInput]) {
    input.addEventListener("change", (event) => {
        const file = event.currentTarget.files?.[0];

        event.currentTarget.value = "";

        processFile(file);
    });
}

// Tombol Scan Lagi
againButton.addEventListener("click", showCamera);

// Tombol Informasi
el("btn-info").addEventListener("click", () => {
    el("info-dialog").showModal();
});

// Tombol Tutup Informasi
el("btn-close").addEventListener("click", () => {
    el("info-dialog").close();
});

console.log("app.js SegarScan berhasil dimuat");