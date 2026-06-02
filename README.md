# SmartReg Monitor — Ricoh Edition 🚀

> **Trabajo de Fin de Máster Corporativo (TFM)** desarrollado para **Ricoh España**.
> Un asistente de cumplimiento legal (RAG Chatbot) para consultar y monitorizar normativa europea (RGPD, AI Act, NIS2) e Inteligencia Artificial de forma interactiva y en tiempo real.

---

## 📋 Resumen del Proyecto y Logros Recientes

Este repositorio contiene la base de código completa del proyecto **SmartReg Monitor**, estructurada de forma profesional e incluyendo las siguientes mejoras de nivel de producción implementadas recientemente:

1. **Estructura Git de Producción**:
   - Inicialización limpia de Git con un archivo `.gitignore` robusto a nivel de raíz que filtra archivos de configuración (`.env`), entornos virtuales (`venv/`), temporales de Chainlit (`.files/`) y archivos de sistema.
   - Subida y vinculación exitosa con la rama principal `main` en [GitHub](https://github.com/boorjanunezz/TFM-Ricoh).
2. **Saneamiento de Seguridad y Credenciales**:
   - Se detectó y saneó por completo el archivo `notebooks/ingesta.ipynb`, el cual contenía claves API hardcodeadas. Se reemplazaron por variables de entorno seguras (`os.environ.get`), asegurando que ningún secreto del proyecto sea filtrado en el repositorio público.
3. **Automatización de Inteligencia de Mercado (Colector Diario de Noticias)**:
   - Se diseñó e implementó un script colector de noticias autónomo (`app/news_collector.py`) que recopila noticias frescas sobre Inteligencia Artificial diariamente de forma gratuita a través de RSS sin necesidad de API keys.
   - Se configuró una pipeline serverless autónoma mediante **GitHub Actions** (`.github/workflows/daily_news_collector.yml`) que ejecuta el script todos los días, genera sus embeddings a través de Azure OpenAI y los indexa automáticamente en Supabase para que el RAG siempre esté actualizado.

---

## 🏛️ Arquitectura de Datos (Pipeline Medallón)

SmartReg Monitor implementa una arquitectura **Medallion** para procesar y consumir datos regulatorios crudos de forma estructurada:

```
                  [ PDF Regulatorio Crudo ]
                             │
                             ▼
🥉 BRONZE  ──► Almacenamiento directo del PDF en Azure Blob Storage (bronze/)
                             │
                             ▼
🥈 SILVER  ──► Extracción de texto y estructura con Azure Document Intelligence
               Almacenamiento de párrafos limpios en formato JSON (silver/)
                             │
                             ▼
🥇 GOLD    ──► Chunking semántico jerárquico por artículos legales (max 3200 chars)
               Almacenamiento de chunks enriquecidos en formato JSON (gold/)
                             │
                             ▼
💾 SUPABASE ─► Generación de embeddings con 'text-embedding-3-small' (1536d)
               Upsert en la tabla 'legal_chunks' (pgvector) para consultas RAG
```

---

## 📁 Estructura del Repositorio

```
tfm-ricoh/
├── .github/workflows/
│   └── daily_news_collector.yml # Workflow de automatización diaria de noticias (GitHub Actions)
├── .vscode/                     # Configuraciones compartidas del espacio de trabajo de VS Code
├── app/                         # Aplicación principal del chatbot
│   ├── .env.example             # Plantilla de configuración de variables de entorno
│   ├── app.py                   # Lógica e interfaz del Chatbot (Chainlit)
│   ├── data_layer.py            # Capa de datos para historial en Supabase
│   ├── news_collector.py        # Colector e indexador automático de noticias de IA
│   ├── registrar_usuario.py     # Script CLI para dar de alta usuarios autorizados
│   ├── requirements.txt         # Dependencias Python de la aplicación
│   └── public/                  # Hojas de estilo personalizadas e imágenes
├── json/                        # Chunks intermedios generados por la arquitectura
│   ├── base/                    # Datos crudos extraídos de regulaciones
│   └── chunk/                   # Chunks procesados por artículos listos para ingesta
├── notebooks/                   # Cuadernos de experimentación de RAG, Ingesta y Parsing
│   ├── ingesta.ipynb            # Flujo de ingesta y carga interactiva limpia
│   ├── chunking_semantico.ipynb # Experimentos de chunking jerárquico
│   └── recortar_pdfs.ipynb      # Procesamiento preliminar de documentos
└── pdfs/                        # Almacén local de documentos PDF originales y recortados
```

---

## 🛠️ Instalación y Configuración

### 1. Clonar el repositorio y preparar el entorno
```bash
git clone https://github.com/boorjanunezz/TFM-Ricoh.git
cd tfm-ricoh

# Crear e iniciar entorno virtual
python -m venv venv
# En Windows:
venv\Scripts\activate
# En macOS/Linux:
source venv/bin/activate

# Instalar dependencias requeridas
pip install -r app/requirements.txt
```

### 2. Configuración de Variables de Entorno
Crea un archivo `.env` en la carpeta `app/` copiando el archivo de ejemplo:
```bash
cp app/.env.example app/.env
```
Abre `app/.env` y completa las credenciales de tus recursos:
- **Azure OpenAI** (API Key, Endpoint, Despliegues para Chat y Embeddings).
- **Azure Document Intelligence** (Endpoint y API Key).
- **Azure Blob Storage** (Cadena de conexión).
- **Supabase** (URL del proyecto y API Key de rol de servicio/anon).

*Nota: El archivo `app/.env` está listado en `.gitignore` y jamás se subirá a Git.*

### 3. Crear tu primer usuario administrador
Para acceder al chatbot, debes crear al menos un usuario autorizado usando la línea de comandos:
```bash
python app/registrar_usuario.py
```

### 4. Ejecutar la Aplicación
Arranca el servidor local de Chainlit:
```bash
chainlit run app/app.py
```
Abre tu navegador en `http://localhost:8000` e inicia sesión con el usuario creado.

---

## 🤖 Colector Automático de Noticias de IA

Para mantener al asistente de cumplimiento legal al día sobre los últimos avances normativos, de mercado y tecnológicos en materia de IA, el sistema recopila noticias todos los días de manera autónoma.

### ¿Cómo funciona?
1. **Google News RSS (Free & Free-Key)**: Descarga periódicamente los últimos encabezados e introducciones en español sobre *"Inteligencia Artificial"*.
2. **Generación de Embeddings**: Pasa los resúmenes por el modelo `text-embedding-3-small` de tu recurso Azure OpenAI.
3. **Indexación Unificada**: Guarda los artículos en la misma tabla de vectores `legal_chunks` de Supabase bajo el documento `"Noticias IA"`.
4. **Respuesta Transparente**: Al preguntar en el chat de SmartReg Monitor (por ejemplo: *"¿Cuáles son las últimas noticias de IA hoy?"*), el RAG recuperará y citará estas noticias automáticamente.

### Automatización en GitHub Actions (Paso a Paso)
Para que el proceso corra gratis todas las mañanas en los servidores de GitHub sin necesidad de que dejes tu ordenador encendido:

1. Ve a tu repositorio en GitHub: **https://github.com/boorjanunezz/TFM-Ricoh**.
2. Entra en **Settings** (Configuración) > **Secrets and variables** > **Actions**.
3. Haz clic en **New repository secret** y añade las siguientes credenciales necesarias con los mismos valores de tu archivo `.env`:
   - `AZURE_OPENAI_API_KEY`
   - `AZURE_OPENAI_ENDPOINT`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`
4. *(Opcional)* Si tus modelos usan despliegues con nombres personalizados, añade también variables para `AZURE_EMBEDDING_DEPLOYMENT` y `AZURE_OPENAI_API_VERSION`.
5. ¡Listo! La tubería se disparará de manera programada todos los días a las **06:00 UTC (08:00 AM hora española)** y también puedes forzar su ejecución manual y ver los logs en la pestaña **Actions** de GitHub seleccionando el workflow *"Daily AI News Collector"*.
