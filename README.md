# Forest Plan Pipeline

Converts scanned forest management plan PDFs into structured, queryable SQLite databases — a core component of the Open Forest Plan Standard (OFPS) infrastructure.

## What It Does

```
PDF (scanned) → OCR → Structure Detection → Component Classification → SQLite Database → Markdown / Word
```

The pipeline extracts not just text, but the **hierarchical structure** (Parts > Chapters > Sections) and **semantic meaning** (desired conditions, standards, guidelines, objectives) from forest plans. The database becomes a queryable representation of the plan that supports outcomes-based adaptive management (OBAM) analysis.

## Quick Start

### 1. Install Dependencies

```bash
# System dependencies (Ubuntu/Debian)
sudo apt-get install tesseract-ocr

# Python dependencies
pip install -r requirements.txt --break-system-packages
```

### 2. Process a Plan

```bash
# Full plan
python pipeline/main.py path/to/forest_plan.pdf --forest "Tahoe National Forest"

# Test on first 10 pages
python pipeline/main.py path/to/forest_plan.pdf --pages 1-10

# Use Marker engine (more accurate, requires more compute)
python pipeline/main.py path/to/forest_plan.pdf --engine marker
```

### 3. Explore the Results

```python
import sqlite3

conn = sqlite3.connect("outputs/forest_plan.db")

# How many desired conditions were found?
cursor = conn.execute(
    "SELECT COUNT(*) FROM plan_components WHERE component_type = 'desired_condition'"
)
print(f"Desired conditions: {cursor.fetchone()[0]}")

# All wildlife standards
cursor = conn.execute("""
    SELECT component_text, component_id_in_plan 
    FROM plan_components 
    WHERE component_type = 'standard' AND resource_area = 'wildlife'
""")
for row in cursor:
    print(f"  [{row[1]}] {row[0][:100]}...")
```

### 4. Export to Markdown

```python
from pipeline.export import export_markdown, export_component_summary

# Full plan as Markdown (preserving document structure)
export_markdown("outputs/forest_plan.db", output_path="outputs/plan.md")

# Component summary (organized by type — the OBAM view)
export_component_summary("outputs/forest_plan.db", output_path="outputs/summary.md")

# Just desired conditions for wildlife
export_markdown("outputs/forest_plan.db", 
    output_path="outputs/wildlife_dc.md",
    filter_component_type="desired_condition",
    filter_resource_area="wildlife")
```

## Project Structure

```
forest-plan-pipeline/
├── pipeline/
│   ├── main.py          # Pipeline orchestrator (run this)
│   ├── ocr.py           # PDF rendering + OCR engines
│   ├── structure.py     # Document hierarchy detection
│   ├── classifier.py    # Plan component classification
│   └── export.py        # Markdown/Word export from database
├── db/
│   ├── schema.sql       # SQLite database schema
│   └── database.py      # Database helpers and query functions
├── api/                  # FastAPI backend (Phase 2)
├── uploads/              # Source PDFs go here
├── outputs/              # Processed outputs land here
├── requirements.txt
└── README.md
```

## Pipeline Stages

| Stage | What It Does | Output |
|-------|-------------|--------|
| 1. PDF Analysis | Examines PDF, detects if scanned vs born-digital | Metadata |
| 2. OCR | Extracts text with layout information | `ocr_output.json` |
| 3. Structure Detection | Rebuilds document hierarchy from headings | `structure.json` |
| 4. Classification | Tags components as desired conditions, standards, etc. | Database records |
| 5. Database Write | Stores everything in queryable SQLite | `.db` file |

## Next Steps (Phase 2)

- **Review App**: React-based UI for practitioners to verify and correct automated extraction
- **Claude Integration**: AI-powered suggestions for component classification
- **Multi-plan queries**: Cross-plan analysis for OFPS
- **Word export**: Generate .docx from the structured database
