# Forest Plan Pipeline — Project Log

## Project Overview

**Repository:** `forest-plan-pipeline`
**Purpose:** Convert forest management plan PDFs into structured, queryable SQLite databases as core infrastructure for the Open Forest Plan Standard (OFPS).
**Status:** Phase 1 prototype — functional pipeline, classification accuracy needs improvement
**Last Updated:** 2026-03-25

---

## Architecture

The pipeline processes a forest plan PDF through five stages:

1. **PDF Analysis** — Examines the PDF, detects born-digital vs. scanned, gathers metadata
2. **Text Extraction / OCR** — Extracts text with layout information (font size, bold, position)
3. **Structure Detection** — Reconstructs document hierarchy from headings
4. **Component Classification** — Identifies plan component types (S&Gs, goals, DFCs, etc.)
5. **Database Write** — Stores everything in queryable SQLite

The system supports three extraction engines: `pymupdf` (born-digital PDFs, fast and accurate), `tesseract` (scanned PDFs via OCR), and `marker` (ML-based extraction, stubbed for future use).

A key architectural feature is **EIS/Plan boundary detection**: many 1980s-90s forest plans are a single PDF containing both the Environmental Impact Statement and the Plan itself. The pipeline auto-detects where the plan body begins and tags all content as either `eis` or `plan`, allowing queries scoped to management direction without EIS narrative noise.

### File Structure

```
forest-plan-pipeline/
├── pipeline/
│   ├── main.py          — Pipeline orchestrator and CLI
│   ├── ocr.py           — PDF rendering + extraction engines (pymupdf, tesseract, marker)
│   ├── structure.py     — Document hierarchy detection
│   ├── classifier.py    — Plan component classification (EIS-aware)
│   └── export.py        — Markdown export from database
├── db/
│   ├── schema.sql       — SQLite database schema
│   └── database.py      — Database helpers and query functions
├── api/                  — FastAPI backend (Phase 2, not yet built)
├── uploads/              — Source PDFs
├── outputs/              — Processed outputs (generated, not committed)
├── requirements.txt
└── README.md
```

### Database Schema

The schema captures three kinds of structure:

- **Document hierarchy** (`sections` table) — parent-child tree of headings
- **Semantic content** (`plan_components` table) — individual units of management direction, each tagged with component type, document section (EIS/plan), resource area, and management area
- **Tabular data** (`plan_tables`, `table_cells`) — structured table content

Component types supported include both 2012 Planning Rule types (`desired_condition`, `standard`, `guideline`, `objective`, `suitability`, `management_approach`, `monitoring_requirement`) and pre-2012 types (`goal`, `standard_and_guideline`, `management_prescription`, `management_practice`, `desired_future_condition`, `management_area_emphasis`), plus EIS content types (`eis_narrative`, `eis_alternative`, `eis_impact_analysis`).

---

## Development History

### Session 1 (2026-03-19/20): Initial Build

- Designed and implemented the complete pipeline architecture
- Built all five pipeline stages with Tesseract as the OCR engine
- Created the SQLite schema, database helpers, query functions, and Markdown export
- Tested classifier against sample forest plan language (4/5 correct, improved to 5/5)
- Packaged as a portable project with requirements.txt and CLI interface for local deployment on any machine

### Session 2 (2026-03-20): First Run on Tahoe Plan

- Installed Python 3.12 (Python 3.14 pre-release was causing package compatibility issues)
- Installed Tesseract OCR on Windows
- Fixed a bug in `structure.py` — regex pattern tuples had inconsistent formats causing a TypeError in `_extract_section_number`
- **Ran Tesseract OCR on the full 1,821-page Tahoe plan** — completed at 93% average confidence
- Pipeline crashed at Stage 3 (structure detection) due to the regex bug
- The fix was generated but not applied before the session ended

### Session 3 (2026-03-25): Breakthrough — EIS/Plan Discovery

Key findings and decisions made during exploratory analysis:

1. **The PDF is born-digital, not scanned.** Despite initial assessment, the pipeline detected 811.8 average characters per page of embedded text. Switched from Tesseract (OCR) to PyMuPDF (direct text extraction) — processing time went from hours to 50 seconds for all 1,821 pages.

2. **The first 1,174 pages are the EIS, not the plan.** The actual management direction begins at page 1175 ("MANAGEMENT DIRECTION — INTRODUCTION"). This explained why the classifier found almost nothing meaningful in initial runs — it was searching for management direction in environmental analysis narrative.

3. **The 1990 Tahoe plan uses pre-2012 component types.** Numbered S&Gs (1-71), Forest Goals organized by resource area, Desired Future Conditions (precursor to 2012 "desired conditions"), Management Practices ("Yellow Pages"), and Management Area Direction for 106 geographic management areas.

4. **The plan's internal structure was mapped:**
   - Page 1175: Management Direction Introduction
   - Page 1176-1178: How to use the plan (Forest Goals, Objectives, DFC, S&Gs explained)
   - Page 1179-1187: Forest Goals and Objectives by resource area
   - Page 1188-1220: Forestwide Standards & Guidelines (TAN PAGES), numbered S&G 1-71
   - Page 1221-1242: Management Practices (YELLOW PAGES)
   - Page 1243-end: Management Area Direction (106 MAs)

Actions taken:
- Added `pymupdf` born-digital extraction engine with font-size-based heading detection
- Added OCR cache/resume capability (reads from `ocr_output.json` if it exists)
- Implemented EIS/Plan boundary auto-detection
- Added `document_section` field to pages, sections, and plan_components tables
- Expanded component types to include pre-2012 plan language
- Rewrote classifier with separate EIS and Plan classification strategies
- Added `plan_body_start_page` to plans table metadata

---

## Current State (as of 2026-03-25)

### What Works

- Full pipeline runs on the 1,821-page Tahoe plan in ~38 seconds
- EIS/Plan boundary auto-detected correctly at page 1175
- 16,477 EIS components and 7,378 plan components properly tagged
- 554 items classified as `standard_and_guideline` in the plan body
- 78 management area emphasis statements detected
- 39 desired future conditions detected
- 14 goals detected
- SQLite database is queryable and exportable to Markdown

### Known Issues — Classification Accuracy

The classifier produces plausible counts but individual classifications are noisy:

1. **S&G false positives:** Many items classified as `standard_and_guideline` are introductory text *about* S&Gs (e.g., "Standards are principles requiring a specific level of attainment") rather than actual S&G directives. The actual numbered S&Gs (S&G 1 through S&G 71) have a distinctive format that the classifier doesn't yet exploit.

2. **DFC false positives:** Items classified as `desired_future_condition` include plan-structure descriptions ("This chapter provides direction for managers") rather than actual future-condition statements.

3. **Resource area misassignment:** Some components are tagged with a resource area based on secondary keyword matches rather than the dominant topic (e.g., a recreation goal tagged as "wildlife" because it mentions "wildlife viewing" in passing).

4. **9,582 sections detected (should be ~200-400).** The heading detector over-fires — it treats bold labels, short all-caps fragments, and figure captions as section headings.

5. **Zero tables extracted.** The born-digital extractor bypasses table detection. Tables are abundant in the plan (species viability matrices, acreage summaries, S&G reference tables).

### Known Issues — Technical

- Python 3.12 sqlite3 DeprecationWarning on datetime adapter (cosmetic, non-breaking)
- The `export.py` module hasn't been updated for the new schema fields
- No automated tests beyond the manual classifier tests run during development

---

## Next Steps

### Near-term (Phase 1 completion)

1. **Improve S&G detection** — The numbered S&G format (code number + title + directive text) is highly structured. A format-aware parser that recognizes "S&G [number]: [title]" blocks would dramatically improve accuracy over keyword matching.

2. **Fix heading over-detection** — Tighten the born-digital heading classifier. Require minimum font size differential from body text, add heuristics for figure/table labels, and implement TOC-page detection to skip table-of-contents entries.

3. **Add table extraction for born-digital PDFs** — PyMuPDF can detect table regions; needs implementation.

4. **Update export.py** — Add `document_section` filtering and the new component types.

5. **Publish to GitHub** — Repository ready for public access.

### Medium-term (Phase 2)

1. **Claude API integration for classification** — Send text blocks with context to Claude for component type classification. Expected to dramatically improve accuracy, especially for distinguishing actual S&G directives from text about S&Gs.

2. **React review interface** — Split-pane view with original PDF page alongside extracted structure. Practitioners verify and correct automated extraction. This is the adoption-critical feature.

3. **Configurable plan profiles** — Different plan eras (1982 rule vs. 2012 rule) and different forests use different conventions. A profile system would let the pipeline adapt its classification strategy.

### Long-term (Phase 3 — OFPS platform)

1. **Multi-plan database** — Cross-plan querying across digitized forest plans
2. **Integration with treatment-layer tools** — Connect to Planscape, Vibrant Planet, ForSys
3. **Version tracking** — Track plan amendments over time
4. **Normative layer architecture** — Distinguish plan-level desired conditions from treatment-layer optimization targets

---

## Technical Environment

- **Tested on:** Windows 10/11 (should work on macOS/Linux with minor path adjustments)
- **Python:** 3.12+ required (3.14 pre-release causes package incompatibility — avoid)
- **Key dependencies:** PyMuPDF 1.27.2, pytesseract 0.3.13, Pillow 12.1.1
- **Tesseract:** v5.5.0 (Windows users must add install directory to PATH)
- **Pipeline invocation:**
  ```
  # Windows (if Tesseract not permanently on PATH)
  set PATH=%PATH%;C:\Program Files\Tesseract-OCR
  
  cd path/to/forest-plan-pipeline
  py -3.12 pipeline/main.py "plan.pdf" --forest "Forest Name"
  ```

---

## Test Data

- **Source PDF:** ORIGINAL 1990 Tahoe National Forest Management Plan.pdf (108.2 MB, 1,821 pages)
- **Characteristics:** Born-digital (embedded text), combined EIS + Plan document
- **Plan body:** Pages 1175-1821 (647 pages of management direction)
- **EIS body:** Pages 1-1174 (environmental analysis, alternatives, public comments)

---

## Contributors

- **JR Washebek** — Project lead, architecture, domain expertise. Senior Fellow for AI and Ecosystem Management at the Environmental Policy Innovation Center (EPIC) and Digital Service for the Planet Fellow at New America.
