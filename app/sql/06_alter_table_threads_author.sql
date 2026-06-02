ALTER TABLE threads
    ADD COLUMN IF NOT EXISTS author TEXT;

-- Índice para filtrar hilos por usuario eficientemente
CREATE INDEX IF NOT EXISTS idx_threads_author ON threads(author);