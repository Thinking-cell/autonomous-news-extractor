
# Autonomous News AI Ingestion & Binary Packing Pipeline
### Real-Time RSS Extractor, Local LLM Classifier, TTL Daemon & Binary Struct Serializer
**Author:** Ranvir Singh | B.Tech CSE (LEET), Amritsar Group of Colleges (AGC)  
**Language:** Python 3.11+ | **Local AI Engine:** Ollama (Zero-Shot JSON Classification)  
**Integration:** Native upstream binary producer for the **C++20 LDBMS Storage Engine**

---

## 1. Project Overview & Motivation

Modern data engineering pipelines ingest thousands of real-time articles every single day from international news wires. However, standard text-based formats (like raw CSV or verbose JSON) cause significant bottlenecks:
* High CPU parsing overhead scanning commas, unescaping quotes, and converting timestamps.
* Storage bloat caused by redundant JSON keys and ASCII numeric representations.
* Promotional clutter and banner widgets polluting the knowledge base.

This pipeline automates the complete data lifecycle:
1. Ingests live RSS feeds continuously from BBC News wires.
2. Filters out promotional widgets and newsletter subscription spam.
3. Classifies articles into 12 distinct categories using a locally hosted LLM (via **Ollama**) with strict JSON constraints.
4. Generates single-sentence, factual summaries strictly bounded between 100 and 250 characters.
5. Serializes clean records into contiguous, fixed-stride 1,188-byte binary structs compatible with the C++20 database engine, **cutting disk storage overhead by 65%**.

---

## 2. Architecture & Dataflow

```
   ┌─────────────────────────────────────────────────────────┐
   │             Live BBC RSS XML Feed Stream                │
   └──────────────────────────┬──────────────────────────────┘
                              │ feedparser
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │                  Article Deduplication                  │
   │           (seen_url.json & Memory Cache Check)          │
   └──────────────────────────┬──────────────────────────────┘
                              │ Unseen Articles Only
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │             Local LLM Inference (Ollama)                │
   │  • Zero-Shot 12-Class Categorization (JSON)             │
   │  • Promotional / Ad Detection Filter                    │
   │  • Strictly Bounded Summarization (100–250 Chars)       │
   └──────────────────────────┬──────────────────────────────┘
                              │ Clean Structured Records
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │           Binary Struct Packer (csv_to_bin_packer)      │
   │  • Formats into 1,188-Byte POD C-Struct Format          │
   │  • Writes staged_news.bin & master_archive.bin          │
   └──────────────────────────┬──────────────────────────────┘
                              │ Direct DMA Chunk Injection
                              ▼
   ┌─────────────────────────────────────────────────────────┐
   │             C++20 LDBMS Binary Storage Engine           │
   │  (Direct Stride Addressing, Introsort, Hole Recycling)  │
   └─────────────────────────────────────────────────────────┘
```

---

## 3. Core Subsystems

### A. Local LLM Classification & Summarization (`src/ai_agent/bbc_ai_agent.py`)
Rather than relying on expensive external cloud APIs, the pipeline communicates with a locally hosted LLM via **Ollama**. Strict prompt engineering enforces zero-shot structured JSON returns without conversational preamble:
* **Categorization Prompt:** Enforces classification into one of 12 distinct categories (`General`, `World`, `Politics`, `Business`, `Technology`, `Science`, `Health`, `Sports`, `Entertainment`, `Environment`, `Crime & Law`, `Lifestyle`).
* **Ad Detection Prompt:** Scans for newsletter signups, promotional hooks, and broadcast adverts to drop non-editorial content.
* **Bounded Summarization Prompt:** Produces factual summaries strictly constrained between 100 and 250 characters to fit within the fixed C++ struct summary buffer (`fixed_string<256>`).

### B. High-Performance Binary Struct Packing (`src/csv_to_bin_packer.py`)
Translates dynamic Python strings and timestamps into raw byte-aligned binary structures matching the C++ memory layout:
* 40-byte fixed header (ID, created/published/expires Unix timestamps, views, category ID, bitmask flags).
* Four fixed-capacity text buffers (`fixed_string` with 2-byte length markers).
* Generates `staged_news.bin` and `master_archive.bin` with exact 1,188-byte record strides.

### C. Background TTL Daemon & IPC Heartbeats (`src/ai_agent/daemons/`)
* **`ttl_daemon.py`:** Runs as an autonomous background daemon tracking article time-to-live (TTL) expiration timestamps.
* **`daemon_client_lib.py`:** Provides lightweight Inter-Process Communication (IPC) heartbeats, ensuring data integrity, graceful daemon restarts, and shared URL cache state.

---

## 4. Directory Structure

```
py_record_extracter/
├── category_map.h                  # C-header defining enum mappings
├── src/
│   ├── csv_to_bin_packer.py        # Binary struct serialization engine
│   └── ai_agent/
│       ├── bbc_ai_agent.py         # Main RSS ingestion & Ollama orchestration
│       ├── category_cleaner.py     # String sanitization & category normalizer
│       ├── archive/
│       │   └── master_archive.bin  # Historical binary storage archive
│       ├── daemons/
│       │   ├── ttl_daemon.py        # Background TTL expiration daemon
│       │   └── daemon_client_lib.py # IPC client library & heartbeat manager
│       └── json/
│           ├── category_lookup.json    # Category string-to-ID lookup
│           ├── master_categories.json  # Schema definition for categories
│           └── seen_url.json           # Deduplication registry of processed URLs
```

---

## 5. Setup & Usage

### Prerequisites
* Python 3.11+
* Local **Ollama** server running (`ollama serve`) with a local model loaded (e.g. `gemma:2b`, `llama3`, or `mistral`)
* Required Python packages:
  ```bash
  pip install feedparser ollama
  ```

### Running the AI Ingestion Pipeline:
```bash
cd src/ai_agent
python bbc_ai_agent.py
```

### Running the TTL Daemon:
```bash
python src/ai_agent/daemons/ttl_daemon.py
```

---

## 6. Synergistic Integration with C++ LDBMS

This Python pipeline was designed specifically to feed the **C++20 News Record Manager (LDBMS)**:
1. Python handles high-level RSS parsing and local LLM NLP classification.
2. Python packs the records into raw binary structs (`news_record`).
3. C++ reads those binary files via Direct Memory Access (DMA) chunk I/O, indexes them with binary search, and sorts them using hybrid Introsort.
