
"use strict";

const cameraVideo = document.getElementById("video");
const cameraMessage = document.getElementById("camera-message");
const cameraPlaceholder = document.getElementById("camera-placeholder");

let cameraStream = null;

// Mengaktifkan kamera
async function startCamera() {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    cameraMessage.textContent =
      "Kamera browser memerlukan HTTPS atau localhost. Gunakan Kamera HP atau Pilih dari Galeri.";
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
      "Dekatkan ke satu jenis sayur. Pastikan cahaya cukup.";

    return true;

  } catch (error) {
    console.error("ERROR KAMERA SEGERSCAN:", {
      name: error.name,
      message: error.message
    });

    stopCamera();

    if (["NotAllowedError", "PermissionDeniedError"].includes(error.name)) {
      cameraMessage.textContent =
        "Izin kamera ditolak. Izinkan kamera di pengaturan browser.";

    } else if (["NotFoundError", "DevicesNotFoundError"].includes(error.name)) {
      cameraMessage.textContent =
        "Kamera tidak ditemukan. Pilih foto dari galeri.";

    } else if (error.name === "NotReadableError") {
      cameraMessage.textContent =
        "Kamera sedang digunakan aplikasi lain atau tidak dapat diakses.";

    } else {
      cameraMessage.textContent =
        "Kamera belum bisa dibuka. Coba Kamera HP atau Pilih dari Galeri.";
    }

    return false;
  }
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

// Matikan kamera ketika halaman ditutup
window.addEventListener("pagehide", stopCamera);