# SmartReg Monitor — Ricoh Edition

> **Trabajo de Fin de Máster corporativo** desarrollado para Ricoh España.
> Chatbot RAG legal para consulta de normativa europea (RGPD, AI Act, NIS2).

---

## Índice

1. [Descripción del proyecto](#1-descripción-del-proyecto)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Stack tecnológico](#3-stack-tecnológico)
4. [Estructura de ficheros](#4-estructura-de-ficheros)
5. [Modelo de datos (Supabase)](#5-modelo-de-datos-supabase)
6. [Pipeline de ingesta — Arquitectura Medallón](#6-pipeline-de-ingesta--arquitectura-medallón)
7. [Instalación y configuración](#7-instalación-y-configuración)
8. [Arrancar la aplicación](#8-arrancar-la-aplicación)
9. [Gestión de usuarios](#9-gestión-de-usuarios)
10. [Perfiles de la aplicación](#10-perfiles-de-la-aplicación)
11. [Variables de entorno](#11-variables-de-entorno)

---

## 1. Descripción del proyecto

SmartReg Monitor es un sistema **RAG (Retrieval-Augmented Generation)** especializado en normativa europea para el equipo de Compliance de Ricoh. Permite consultar regulaciones complejas en lenguaje natural, obteniendo respuestas precisas y citadas directamente desde los documentos oficiales cargados en el sistema.

Características principales:

- Respuestas fundamentadas **exclusivamente** en los documentos indexados, sin alucinaciones.
- **Citación automática** de cada artículo relevante en el formato `[Documento - Artículo]`.
- **Historial de conversaciones** persistente por usuario, con posibilidad de reanudar chats anteriores.
- **Ingesta de documentos** con pipeline completo integrado: extracción → chunking semántico → embeddings → indexación vectorial.
- **Gestión de documentos** indexados desde la propia interfaz.
- Arquitectura **Vanilla RAG** sin frameworks intermediarios (sin LangChain ni LlamaIndex) para máximo control y mínima latencia.

---

## 2. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────┐
│                    USUARIO (Navegador)                  │
└──────────────────────────┬──────────────────────────────┘
                           │
                    Chainlit (Python)
                           │
           ┌───────────────┼───────────────┐
           │               │               │
     [Consultar]   [Subir docs]   [Gestionar docs]
           │               │               │
           │        Pipeline Medallón       │
           │        Bronze→Silver→Gold      │
           │               │               │
           └───────────────┴───────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
         Supabase                  Azure OpenAI
     (pgvector + chat          (GPT-4o + Embeddings)
        history)
              │
     Azure Blob Storage
     (bronze / silver / gold)
              │
     Azure Document Intelligence
     (extracción de texto PDF)
```

**Flujo de una consulta RAG:**

1. El usuario escribe una pregunta.
2. Se genera el embedding de la pregunta (`text-embedding-3-small`).
3. Se buscan los `TOP_K=5` chunks más similares en Supabase (`pgvector`).
4. Se construye el contexto con los chunks recuperados.
5. Se envía al LLM (`gpt-4o`) con el system prompt legal y el historial de conversación.
6. La respuesta se muestra con streaming y las fuentes citadas al final.

---

## 3. Stack tecnológico

| Capa | Tecnología |
|---|---|
| Interfaz de usuario | Python + Chainlit 2.11.x |
| LLM | GPT-4o (Azure OpenAI) |
| Embeddings | text-embedding-3-small (Azure OpenAI) |
| Vector Store | Supabase (PostgreSQL + pgvector) |
| Extracción de texto | Azure Document Intelligence (prebuilt-layout) |
| Almacenamiento de ficheros | Azure Blob Storage (contenedores bronze/silver/gold) |
| Autenticación | Supabase (tabla `app_users`, hash SHA-256) |

---

## 4. Estructura de ficheros

```
tfm-ricoh/
├── app.py                  # Aplicación principal (Chainlit)
├── data_layer.py           # Persistencia del historial en Supabase
├── registrar_usuario.py    # Script CLI para crear usuarios
├── chainlit.md             # Texto de bienvenida de Chainlit
├── requirements.txt        # Dependencias Python
├── .env                    # Variables de entorno (NO subir a Git)
├── .env.example            # Plantilla de variables de entorno
├── .chainlit/              # Configuración de Chainlit (tema, idioma)
├── public/                 # Assets estáticos (logo, CSS corporativo)
└── azure_function/         # Código de Azure Function (pipeline alternativo)
    ├── function_app.py
    ├── host.json
    └── local.settings.json
```

---

## 5. Modelo de datos (Supabase)

### `app_users` — Usuarios de la aplicación
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | uuid | Clave primaria |
| `username` | text | Nombre de usuario (único) |
| `password_hash` | text | Contraseña hasheada con SHA-256 |
| `created_at` | timestamptz | Fecha de creación |

### `legal_chunks` — Base de conocimiento vectorial
| Campo | Tipo | Descripción |
|---|---|---|
| `chunk_id` | text (PK) | Hash MD5 del chunk |
| `documento` | text | Nombre del documento (ej. "RGPD") |
| `capitulo` | text | Capítulo del artículo |
| `articulo` | text | Número de artículo |
| `titulo_articulo` | text | Título del artículo |
| `contenido` | text | Texto completo con cabecera |
| `contenido_sin_header` | text | Texto sin cabecera (para mostrar al usuario) |
| `embedding` | vector(1536) | Vector semántico del chunk |
| `jurisdiccion` | text | Jurisdicción ("UE") |
| `num_tokens_estimados` | int | Estimación de tokens |

### `threads` — Hilos de conversación
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | text (PK) | ID del hilo (generado por Chainlit) |
| `author` | text | Username del usuario |
| `name` | text | Título de la conversación |
| `updated_at` | timestamptz | Última actualización |

### `thread_messages` — Mensajes de cada conversación
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | text (PK) | ID del mensaje |
| `thread_id` | text (FK) | Hilo al que pertenece |
| `role` | text | "user" o "assistant" |
| `content` | text | Contenido del mensaje |
| `created_at` | timestamptz | Fecha del mensaje |

### `elements` — Fuentes citadas por mensaje
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | text (PK) | ID del elemento |
| `thread_id` | text (FK) | Hilo al que pertenece |
| `for_id` | text | ID del mensaje al que está asociado |
| `name` | text | Nombre de la fuente (ej. "RGPD — Artículo 5") |
| `content` | text | Contenido expandible de la fuente |

> **Función SQL necesaria en Supabase:**
> La búsqueda vectorial requiere la función `buscar_chunks` creada con `pgvector`:
> ```sql
> CREATE OR REPLACE FUNCTION buscar_chunks(
>     query_embedding vector(1536),
>     top_k int,
>     umbral_similitud float
> )
> RETURNS TABLE (
>     chunk_id text, documento text, capitulo text, articulo text,
>     titulo_articulo text, contenido_sin_header text,
>     referencia_legal text, similitud float
> )
> LANGUAGE sql AS $$
>     SELECT chunk_id, documento, capitulo, articulo, titulo_articulo,
>            contenido_sin_header, referencia_legal,
>            1 - (embedding <=> query_embedding) AS similitud
>     FROM legal_chunks
>     WHERE 1 - (embedding <=> query_embedding) > umbral_similitud
>     ORDER BY embedding <=> query_embedding
>     LIMIT top_k;
> $$;
> ```

---

## 6. Pipeline de ingesta — Arquitectura Medallón

Al subir un PDF desde el perfil **Subir documentos**, se ejecuta el siguiente pipeline de forma secuencial y bloqueante (el usuario ve el progreso en tiempo real):

```
PDF subido por el usuario
       │
       ▼
🥉 BRONZE  →  Almacena el PDF original en Azure Blob Storage (bronze/)
       │
       ▼
🥈 SILVER  →  Azure Document Intelligence extrae párrafos con roles
              (sectionHeading, pageHeader filtrado, etc.)
              Detecta automáticamente la página de inicio del contenido.
              Guarda el resultado como JSON en (silver/)
       │
       ▼
🥇 GOLD    →  Chunking semántico respetando la jerarquía legal:
              Capítulo → Artículo → Contenido
              Divide chunks >3200 caracteres con solapamiento de 400 chars.
              Añade metadatos (chunk_id, documento, referencia...).
              Guarda el resultado como JSON en (gold/)
       │
       ▼
💾 SUPABASE → Genera embeddings (text-embedding-3-small) para cada chunk
              y hace upsert en la tabla legal_chunks.
```

---

## 7. Instalación y configuración

### Requisitos previos

- Python 3.10 o superior
- Cuenta en Supabase con las tablas y función SQL creadas
- Recursos Azure: OpenAI, Document Intelligence y Blob Storage

### 1. Clonar el repositorio y crear entorno virtual

```bash
git clone <url-del-repositorio>
cd tfm-ricoh

python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

Si `requirements.txt` no está actualizado, instala manualmente:

```bash
pip install chainlit openai supabase azure-ai-documentintelligence azure-storage-blob python-dotenv
```

### 3. Configurar variables de entorno

Copia el fichero de ejemplo y rellena los valores:

```bash
cp .env.example .env
```

Edita `.env` con tus credenciales (ver sección [Variables de entorno](#11-variables-de-entorno)).

### 4. Crear el primer usuario

```bash
python registrar_usuario.py
```

Sigue las instrucciones en pantalla para crear un usuario con contraseña.

---

## 8. Arrancar la aplicación

```bash
chainlit run app.py
```

La aplicación estará disponible en `http://localhost:8000`.

Para arrancar en un puerto diferente:

```bash
chainlit run app.py --port 8080
```

Para desarrollo con recarga automática al guardar cambios:

```bash
chainlit run app.py --watch
```

---

## 9. Gestión de usuarios

SmartReg Monitor usa un sistema de autenticación propio sobre Supabase. Los usuarios **no se pueden registrar desde la UI** — deben ser creados por un administrador con el script CLI:

```bash
python registrar_usuario.py
```

El script solicita nombre de usuario y contraseña (mínimo 4 caracteres), comprueba que el usuario no exista y guarda el hash SHA-256 de la contraseña en la tabla `app_users`.

---

## 10. Perfiles de la aplicación

La aplicación tiene tres modos accesibles desde el selector de perfiles:

### 🔍 Consultar (por defecto)
Modo RAG principal. El usuario hace preguntas en lenguaje natural y el sistema responde citando los artículos relevantes de los documentos indexados. Solo este perfil guarda historial de conversaciones en Supabase.

### 📁 Subir documentos
Permite subir uno o varios PDFs para indexarlos en la base de conocimiento. El pipeline Medallón (Bronze → Silver → Gold → Supabase) se ejecuta de forma bloqueante, mostrando el progreso en tiempo real. Solo se aceptan ficheros PDF.

### 📂 Gestionar documentos
Muestra la lista de documentos indexados con el número de chunks de cada uno. Permite eliminar documentos con borrado en cascada (Blob Storage + Supabase).

**Comandos disponibles en este perfil:**

| Comando | Acción |
|---|---|
| `listar` | Refresca la lista de documentos indexados |
| `borrar [nombre]` | Elimina un documento (pide confirmación con botones) |

---

## 11. Variables de entorno

Todas las credenciales se configuran en el fichero `.env` en la raíz del proyecto:

```env
# Azure OpenAI
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://tu-recurso.openai.azure.com/
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_CHAT_DEPLOYMENT=gpt-4o

# Azure Document Intelligence
DOC_INTELLIGENCE_ENDPOINT=https://tu-recurso.cognitiveservices.azure.com/
DOC_INTELLIGENCE_KEY=...

# Azure Blob Storage
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...

# Supabase
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_KEY=...
```

> ⚠️ **Nunca subas el fichero `.env` al repositorio.** Está incluido en `.gitignore`.
> Usa `.env.example` como plantilla para documentar las variables sin valores reales.