-- Skema database Supabase untuk metadata pending SegarScan
-- Jalankan di SQL Editor Supabase jika ingin menyimpan metadata ke PostgreSQL table.
-- Jika tabel ini belum dibuat, SegarScan secara otomatis tetap dapat menyimpan
-- foto dan metadata langsung di Supabase Storage Bucket ('pending').

-- 1. Buat bucket penyimpanan untuk foto jika belum ada
INSERT INTO storage.buckets (id, name, public)
VALUES ('pending', 'pending', false)
ON CONFLICT (id) DO NOTHING;

-- 2. Buat tabel metadata pending
CREATE TABLE IF NOT EXISTS public.pending_scans (
    id VARCHAR(64) PRIMARY KEY,
    status_review VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id VARCHAR(64),
    prediksi_sementara JSONB,
    foto_path TEXT,
    label_manusia JSONB,
    verified_at TIMESTAMPTZ,
    dataset TEXT
);

-- 3. Buat indeks untuk query antrean pending dan pencarian jenis
CREATE INDEX IF NOT EXISTS idx_pending_scans_status ON public.pending_scans (status_review);
CREATE INDEX IF NOT EXISTS idx_pending_scans_created ON public.pending_scans (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pending_scans_jenis ON public.pending_scans ((prediksi_sementara->>'jenis'));

