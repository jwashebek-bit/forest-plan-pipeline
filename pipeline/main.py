"""
Forest Plan Pipeline - Main Orchestrator

Processes a forest plan PDF through five stages:
1. PDF Analysis    -> Examine the PDF and gather metadata
2. OCR/Extraction  -> Extract text with layout information
3. Structure       -> Reconstruct document hierarchy
4. Classification  -> Identify plan component types (EIS-aware)
5. Database Write  -> Store everything in SQLite

Updated: Detects EIS/Plan boundary and tags all content accordingly.

USAGE:
    py -3.12 pipeline\\main.py "plan.pdf" --forest "Tahoe National Forest"
    py -3.12 pipeline\\main.py "plan.pdf" --engine pymupdf --force-ocr
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.ocr import get_pdf_info, run_ocr
from pipeline.structure import detect_structure
from pipeline.classifier import (
    classify_content_blocks,
    extract_component_id,
    extract_resource_area,
    detect_plan_body_start,
)
from db.database import (
    init_database,
    create_plan,
    insert_page,
    insert_section,
    insert_component,
    insert_table,
    log_processing,
    get_plan_summary,
)


def run_pipeline(
    pdf_path: str,
    plan_name: str = None,
    forest_name: str = None,
    engine: str = "pymupdf",
    output_dir: str = None,
    db_path: str = None,
    dpi: int = 300,
    page_range: tuple = None,
    force_ocr: bool = False,
    plan_body_page: int = None,
) -> dict:
    """
    Run the full pipeline on a forest plan PDF.
    
    Args:
        pdf_path:       Path to the source PDF file
        plan_name:      Human-readable plan name
        forest_name:    Forest name
        engine:         'pymupdf' (born-digital), 'tesseract', or 'marker'
        output_dir:     Where to save intermediate outputs
        db_path:        Where to save the SQLite database
        dpi:            Resolution for page rendering (default: 300)
        page_range:     Optional (start, end) for partial processing
        force_ocr:      If True, re-run extraction even if cached output exists
        plan_body_page: Override auto-detection of plan body start page
    
    Returns:
        dict with processing results and summary statistics
    """
    start_time = time.time()
    
    # SETUP
    pdf_path = os.path.abspath(pdf_path)
    filename_stem = Path(pdf_path).stem
    
    if output_dir is None:
        output_dir = os.path.join(PROJECT_ROOT, "outputs", filename_stem)
    os.makedirs(output_dir, exist_ok=True)
    
    if db_path is None:
        db_path = os.path.join(PROJECT_ROOT, "outputs", f"{filename_stem}.db")
    
    print("=" * 60)
    print("FOREST PLAN PIPELINE")
    print("=" * 60)
    print(f"Source PDF:  {pdf_path}")
    print(f"Output dir:  {output_dir}")
    print(f"Database:    {db_path}")
    print(f"OCR Engine:  {engine}")
    print(f"DPI:         {dpi}")
    if page_range:
        print(f"Page range:  {page_range[0]+1}-{page_range[1]}")
    print("=" * 60)
    
    # ============================================================
    # STAGE 1: PDF Analysis
    # ============================================================
    print(f"\n{'~'*40}")
    print("STAGE 1: Analyzing PDF...")
    print(f"{'~'*40}")
    
    pdf_info = get_pdf_info(pdf_path)
    
    print(f"  Pages: {pdf_info['page_count']}")
    print(f"  File size: {pdf_info['file_size_mb']} MB")
    print(f"  Scanned: {'Yes' if pdf_info['is_scanned'] else 'No (born-digital)'}")
    print(f"  Avg chars/page: {pdf_info['avg_chars_per_page']}")
    
    # Initialize database (delete existing to prevent duplicates on re-run)
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"  Removed existing database at {db_path}")
    conn = init_database(db_path)
    
    # Create plan record (plan_body_start_page set after detection)
    plan_id = create_plan(
        conn,
        name=plan_name or filename_stem.replace("_", " ").title(),
        forest_name=forest_name,
        source_pdf_path=pdf_path,
        source_pdf_hash=pdf_info["sha256"],
        page_count=pdf_info["page_count"],
        ocr_engine=engine,
        processing_status="processing",
    )
    
    log_processing(conn, plan_id, "analysis", "completed",
                   f"PDF analyzed: {pdf_info['page_count']} pages",
                   json.dumps(pdf_info, default=str))
    
    # ============================================================
    # STAGE 2: OCR / Text Extraction
    # ============================================================
    print(f"\n{'~'*40}")
    print("STAGE 2: Extracting text...")
    print(f"{'~'*40}")
    
    ocr_output_path = os.path.join(output_dir, "ocr_output.json")
    
    log_processing(conn, plan_id, "ocr", "started", f"Engine: {engine}")
    
    try:
        if os.path.exists(ocr_output_path) and not force_ocr:
            print(f"  Found existing output at {ocr_output_path}")
            print(f"  Loading from cache (use --force-ocr to re-run)")
            with open(ocr_output_path, "r") as f:
                pages_ocr = json.load(f)
            print(f"  Loaded {len(pages_ocr)} pages from cache")
        else:
            pages_ocr = run_ocr(pdf_path, engine=engine, output_dir=output_dir, dpi=dpi)
            with open(ocr_output_path, "w") as f:
                json.dump(pages_ocr, f, indent=2, default=str)
            print(f"  Saved to {ocr_output_path}")
        
        # Filter to page range if specified
        if page_range:
            pages_ocr = [
                p for p in pages_ocr
                if page_range[0] < p["page_number"] <= page_range[1]
            ]
        
        # ============================================================
        # BOUNDARY DETECTION: Find where Plan body starts
        # ============================================================
        print(f"\n  Detecting EIS/Plan boundary...")
        
        if plan_body_page:
            plan_body_start = plan_body_page
            print(f"  Using manually specified plan body start: page {plan_body_start}")
        else:
            plan_body_start = detect_plan_body_start(pages_ocr)
            if plan_body_start:
                print(f"  Auto-detected plan body start: page {plan_body_start}")
            else:
                print(f"  Could not auto-detect boundary. Treating entire document as plan.")
        
        # Update plan record with boundary
        if plan_body_start:
            conn.execute(
                "UPDATE plans SET plan_body_start_page = ? WHERE id = ?",
                (plan_body_start, plan_id)
            )
            conn.commit()
        
        # Store page records with document_section tagging
        for page_data in pages_ocr:
            pn = page_data["page_number"]
            if plan_body_start:
                doc_section = "plan" if pn >= plan_body_start else "eis"
            else:
                doc_section = "plan"
            
            raw_text = "\n".join(
                b["text"] for b in page_data.get("blocks", []) if b.get("text")
            )
            image_dir = os.path.join(output_dir, "page_images")
            image_path = os.path.join(image_dir, f"page_{pn:04d}.png")
            
            insert_page(
                conn, plan_id,
                page_number=pn,
                document_section=doc_section,
                image_path=image_path if os.path.exists(image_path) else None,
                raw_text=raw_text,
                ocr_confidence=page_data.get("ocr_confidence"),
            )
        
        log_processing(conn, plan_id, "ocr", "completed",
                       f"Processed {len(pages_ocr)} pages, boundary at page {plan_body_start}")
        
    except Exception as e:
        log_processing(conn, plan_id, "ocr", "failed", str(e))
        raise
    
    # ============================================================
    # STAGE 3: Structure Detection
    # ============================================================
    print(f"\n{'~'*40}")
    print("STAGE 3: Detecting document structure...")
    print(f"{'~'*40}")
    
    log_processing(conn, plan_id, "structure_detection", "started")
    
    structure = detect_structure(pages_ocr)
    
    structure_output_path = os.path.join(output_dir, "structure.json")
    with open(structure_output_path, "w") as f:
        json.dump(structure, f, indent=2, default=str)
    print(f"  Structure saved to {structure_output_path}")
    
    # Store sections with document_section tagging
    section_index_to_db_id = {}
    section_index_to_parent_db_id = {}
    
    for section in structure["sections"]:
        sp = section.get("start_page", 0)
        if plan_body_start:
            doc_section = "plan" if sp >= plan_body_start else "eis"
        else:
            doc_section = "plan"
        
        db_id = insert_section(
            conn, plan_id,
            title=section["title"],
            depth=section["depth"],
            sort_order=section["sort_order"],
            parent_id=None,
            section_number=section.get("section_number"),
            document_section=doc_section,
            start_page=section.get("start_page"),
            end_page=section.get("end_page"),
        )
        section_index_to_db_id[section["index"]] = db_id
        section_index_to_parent_db_id[section["index"]] = section.get("parent_index")
    
    # Set parent references
    for section_idx, parent_idx in section_index_to_parent_db_id.items():
        if parent_idx is not None and parent_idx in section_index_to_db_id:
            db_id = section_index_to_db_id[section_idx]
            parent_db_id = section_index_to_db_id[parent_idx]
            conn.execute("UPDATE sections SET parent_id = ? WHERE id = ?",
                         (parent_db_id, db_id))
    conn.commit()
    
    log_processing(conn, plan_id, "structure_detection", "completed",
                   json.dumps(structure["diagnostics"]))
    
    # ============================================================
    # STAGE 4: Classification (EIS-aware)
    # ============================================================
    print(f"\n{'~'*40}")
    print("STAGE 4: Classifying plan components...")
    print(f"{'~'*40}")
    
    log_processing(conn, plan_id, "classification", "started")
    
    classified_blocks = classify_content_blocks(
        structure["content_blocks"],
        structure["sections"],
        plan_body_start_page=plan_body_start,
    )
    
    # Store classified components
    component_count = 0
    for block in classified_blocks:
        section_idx = block.get("section_index")
        if section_idx is None or section_idx not in section_index_to_db_id:
            continue
        
        classification = block["classification"]
        section_db_id = section_index_to_db_id[section_idx]
        doc_section = classification.get("document_section", "unknown")
        
        section_title = ""
        for s in structure["sections"]:
            if s["index"] == section_idx:
                section_title = s["title"]
                break
        
        component_id = extract_component_id(block["text"])
        resource_area = extract_resource_area(block["text"], section_title)
        
        insert_component(
            conn, plan_id,
            section_id=section_db_id,
            component_type=classification["component_type"],
            component_text=block["text"],
            document_section=doc_section,
            component_id_in_plan=component_id,
            resource_area=resource_area,
            classification_confidence=classification["confidence"],
            source_page=block.get("source_page"),
        )
        component_count += 1
    
    log_processing(conn, plan_id, "classification", "completed",
                   f"Classified {component_count} components")
    
    # ============================================================
    # STAGE 5: Table Extraction
    # ============================================================
    print(f"\n{'~'*40}")
    print("STAGE 5: Extracting tables...")
    print(f"{'~'*40}")
    
    table_count = 0
    for table_data in structure.get("tables", []):
        sp = table_data.get("source_page", 0)
        doc_section = "plan" if (plan_body_start and sp >= plan_body_start) else "eis"
        insert_table(
            conn, plan_id,
            document_section=doc_section,
            source_page_start=sp,
            source_page_end=sp,
        )
        table_count += 1
    
    print(f"  Extracted {table_count} tables")
    
    # ============================================================
    # FINALIZE
    # ============================================================
    conn.execute(
        "UPDATE plans SET processing_status = 'ocr_complete' WHERE id = ?",
        (plan_id,)
    )
    conn.commit()
    
    summary = get_plan_summary(conn, plan_id)
    elapsed = time.time() - start_time
    
    print(f"\n{'='*60}")
    print("PIPELINE COMPLETE")
    print(f"{'='*60}")
    print(f"Time elapsed: {elapsed:.1f} seconds")
    print(f"Plan ID: {plan_id}")
    if plan_body_start:
        print(f"EIS/Plan boundary: page {plan_body_start}")
    print(f"Sections: {summary['section_count']}")
    print(f"Tables: {summary['table_count']}")
    print(f"\nComponents by type:")
    for comp_type, count in sorted(summary["components"].items(), key=lambda x: -x[1]):
        print(f"  {comp_type}: {count}")
    print(f"\nComponents by document section:")
    for ds, count in sorted(summary.get("components_by_document_section", {}).items()):
        print(f"  {ds}: {count}")
    print(f"\nDatabase saved to: {db_path}")
    
    conn.close()
    
    return {
        "plan_id": plan_id,
        "db_path": db_path,
        "output_dir": output_dir,
        "summary": summary,
        "elapsed_seconds": round(elapsed, 1),
        "plan_body_start_page": plan_body_start,
    }


# ============================================================
# COMMAND-LINE INTERFACE
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Process a forest plan PDF into a structured database."
    )
    parser.add_argument("pdf_path", help="Path to the forest plan PDF file")
    parser.add_argument("--name", help="Plan name", default=None)
    parser.add_argument("--forest", help="Forest name", default=None)
    parser.add_argument(
        "--engine", choices=["tesseract", "marker", "pymupdf"],
        default="pymupdf",
        help="Extraction engine (default: pymupdf for born-digital)"
    )
    parser.add_argument("--dpi", type=int, default=300, help="DPI for rendering")
    parser.add_argument("--pages", help="Page range (e.g., '1-10')", default=None)
    parser.add_argument("--output-dir", help="Output directory", default=None)
    parser.add_argument("--db", help="Database path", default=None)
    parser.add_argument("--force-ocr", action="store_true",
                        help="Re-run extraction even if cached output exists")
    parser.add_argument("--plan-body-page", type=int, default=None,
                        help="Override auto-detection: PDF page where plan body starts")
    
    args = parser.parse_args()
    
    page_range = None
    if args.pages:
        start, end = args.pages.split("-")
        page_range = (int(start) - 1, int(end))
    
    result = run_pipeline(
        pdf_path=args.pdf_path,
        plan_name=args.name,
        forest_name=args.forest,
        engine=args.engine,
        output_dir=args.output_dir,
        db_path=args.db,
        dpi=args.dpi,
        page_range=page_range,
        force_ocr=args.force_ocr,
        plan_body_page=args.plan_body_page,
    )
    
    return result


if __name__ == "__main__":
    main()
