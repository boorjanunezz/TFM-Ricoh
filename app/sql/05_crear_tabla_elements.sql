CREATE TABLE IF NOT EXISTS elements (
    id TEXT PRIMARY KEY,
    thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
    for_id TEXT,        -- ID del mensaje al que está asociado el elemento
    type TEXT DEFAULT 'text',
    name TEXT,
    display TEXT DEFAULT 'side',
    content TEXT
);

-- Índice para cargar elementos por hilo
CREATE INDEX IF NOT EXISTS idx_elements_thread ON elements(thread_id);