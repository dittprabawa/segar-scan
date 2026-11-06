
"use strict";

const cameraVideo = document.getElementById("video");
const cameraMessage = document.getElementById("camera-message");
const cameraPlaceholder = document.getElementById("camera-placeholder");

let cameraStream = null;

// Mengaktifkan kamera
async function startCamera() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        cameraMessage.textContent =
            "Kamera memerlukan HTTPS atau localhost. Gunakan Kamera HP atau Galeri.";
        return false;
    }

    try {
        cameraMessage.textContent = "Meminta izin kamera…";

        stopCamera();

        cameraStream = await navigator.mediaDevices.getUserMedia({
            audio: false,
            video: {
                facingMode: { ideal: "environment" },
                width: { ideal: 1280 },
                height: { ideal: 720 }
            }
        });

        cameraVideo.srcObject = cameraStream;
        cameraVideo.muted = true;
        cameraVideo.playsInline = true;

        await cameraVideo.play();

        cameraPlaceholder.hidden = true;

        cameraMessage.textContent =
            "Dekatkan ke satu jenis bahan dapur. Pastikan cahaya cukup.";

        console.log("Kamera SegarScan berhasil aktif");

        return true;

    } catch (error) {
        console.error("ERROR KAMERA SEGERSCAN:", error);

        stopCamera();

        if (error.name === "NotAllowedError") {
            cameraMessage.textContent =
                "Izin kamera ditolak. Periksa pengaturan browser.";

        } else if (error.name === "NotFoundError") {
            cameraMessage.textContent =
                "Kamera tidak ditemukan. Gunakan galeri.";

        } else if (error.name === "NotReadableError") {
            cameraMessage.textContent =
                "Kamera sedang digunakan aplikasi lain.";

        } else {
            cameraMessage.textContent =
                "Kamera gagal dibuka. Coba lagi atau gunakan galeri.";
        }

        return false;
    }
}


// Crop foto bagian tengah kamera
async function captureCenterCrop(video) {
    const width = video.videoWidth;
    const height = video.videoHeight;

    if (!width || !height) {
        throw new Error("Kamera belum siap");
    }

    const side = Math.round(Math.min(width, height) * 0.75);
    const x = Math.round((width - side) / 2);
    const y = Math.round((height - side) / 2);

    const canvas = document.createElement("canvas");
    const outputSize = Math.min(1024, side);

    canvas.width = outputSize;
    canvas.height = outputSize;

    const ctx = canvas.getContext("2d");

    if (!ctx) {
        throw new Error("Canvas tidak tersedia");
    }

    ctx.drawImage(
        video,
        x, y, side, side,
        0, 0, outputSize, outputSize
    );

    return new Promise((resolve, reject) => {
        canvas.toBlob((blob) => {
            if (blob) {
                resolve(blob);
            } else {
                reject(new Error("Gagal mengambil foto"));
            }
        }, "image/jpeg", 0.85);
    });
}

// Mematikan kamera
function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach((track) => {
            track.stop();
        });

        cameraStream = null;
    }

    cameraVideo.srcObject = null;
    cameraPlaceholder.hidden = false;
}

window.addEventListener("pagehide", stopCamera);
