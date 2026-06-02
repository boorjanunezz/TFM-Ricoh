-- Tabla de hilos de conversación
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Tabla de mensajes dentro de cada hilo
CREATE TABLE IF NOT EXISTS thread_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT REFERENCES threads(id) ON DELETE CASCADE,
    role TEXT NOT NULL,             -- 'user' o 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Índice para acelerar la carga de mensajes por hilo
CREATE INDEX IF NOT EXISTS idx_messages_thread ON thread_messages(thread_id, created_at);
