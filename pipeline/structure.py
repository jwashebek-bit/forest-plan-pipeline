"""
Structure Detection — Stage 3 of the pipeline.

Takes the flat list of OCR blocks (headings, body text, tables) and
reconstructs the document's hierarchical structure:
    Part > Chapter > Section > Subsection > Content

This is the most forest-plan-specific module. Different plans use
different numbering conventions, heading styles, and organizational
patterns. The logic here handles common patterns; the practitioner
review step catches the rest.

KEY DESIGN PRINCIPLE:
Over-detect rather than under-detect headings. It's easier for a
human reviewer to merge two incorrectly-split sections than to
find and create a section break the automation missed entirely.
"""

import re
import json
from typing import Optional


def detect_structure(pages: list) -> dict:
    """
    Process OCR output into a hierarchical document structure.
    
    Args:
        pages: List of page-level dicts from the OCR module,
               each containing a list of typed text blocks.
    
    Returns:
        {
            "sections": [list of section dicts with hierarchy],
            "content_blocks": [list of content blocks linked to sections],
            "tables": [list of detected tables],
            "diagnostics": {structure detection statistics}
        }
    """
    print("Stage 3: Detecting document structure...")
    
    # Step 1: Flatten all blocks across pages into a single ordered stream,
    # keeping track of which page each block came from
    all_blocks = []
    for page in pages:
        for block in page["blocks"]:
            block["source_page"] = page["page_number"]
            all_blocks.append(block)
    
    # Step 2: Filter out page numbers and other non-content blocks
    content_blocks = [
        b for b in all_blocks 
        if b["type"] not in ("page_number",)
        and b["text"].strip()  # Remove empty blocks
    ]
    
    # Step 3: Build the section hierarchy from heading blocks
    sections = _build_section_hierarchy(content_blocks)
    
    # Step 4: Assign non-heading content blocks to their parent sections
    content_assignments = _assign_content_to_sections(content_blocks, sections)
    
    # Step 5: Detect tables (blocks that look tabular even if not tagged as such)
    tables = _detect_tables(content_blocks)
    
    # Diagnostics
    diagnostics = {
        "total_blocks": len(all_blocks),
        "content_blocks": len(content_blocks),
        "sections_detected": len(sections),
        "heading_blocks": len([b for b in content_blocks if b["type"] == "heading"]),
        "body_blocks": len([b for b in content_blocks if b["type"] == "body"]),
        "table_blocks": len(tables),
        "pages_processed": len(pages),
    }
    
    print(f"  Detected {diagnostics['sections_detected']} sections across {diagnostics['pages_processed']} pages")
    
    return {
        "sections": sections,
        "content_blocks": content_assignments,
        "tables": tables,
        "diagnostics": diagnostics,
    }


def _build_section_hierarchy(blocks: list) -> list:
    """
    Extract heading blocks and build a tree structure.
    
    The algorithm:
    1. Collect all heading blocks with their levels
    2. Walk through them in order, maintaining a "stack" of 
       current section ancestors
    3. Each new heading either becomes a child of the current 
       section (if deeper level) or pops up the stack to become 
       a sibling/uncle
    
    This is the same algorithm used to build a table of contents
    from heading levels in a word processor.
    """
    sections = []
    
    # Stack tracks the current path from root to the deepest open section
    # Each entry: (section_index_in_list, heading_level)
    stack = []
    
    sort_counter = 0
    
    for block in blocks:
        if block["type"] != "heading" or block["heading_level"] is None:
            continue
        
        level = block["heading_level"]
        title = block["text"].strip()
        
        # Skip obviously wrong "headings" (single characters, etc.)
        if len(title) < 2:
            continue
        
        # Try to extract a section number from the title
        section_number = _extract_section_number(title)
        
        # Pop the stack until we find the parent for this heading level
        while stack and stack[-1][1] >= level:
            stack.pop()
        
        # Determine parent
        parent_idx = stack[-1][0] if stack else None
        
        # Determine depth (0-indexed from root)
        depth = len(stack)
        
        section = {
            "index": len(sections),     # Position in the flat sections list
            "parent_index": parent_idx,  # Index of parent section (None for root)
            "depth": depth,
            "sort_order": sort_counter,
            "title": title,
            "section_number": section_number,
            "heading_level": level,
            "start_page": block["source_page"],
            "end_page": block["source_page"],  # Updated later
        }
        
        sections.append(section)
        stack.append((section["index"], level))
        sort_counter += 1
    
    # Post-process: set end_page for each section 
    # (end_page = start_page of the next sibling or parent's end)
    _set_section_end_pages(sections)
    
    return sections


def _extract_section_number(title: str) -> Optional[str]:
    """
    Try to extract a section number from a heading title.
    
    Common patterns in forest plans:
    - "Chapter 3: Wildlife"         → "Chapter 3"
    - "3.1.2 Desired Conditions"    → "3.1.2"
    - "Part IV - Fire Management"   → "Part IV"
    - "SECTION 2.3"                 → "2.3"
    - "A. Timber Suitability"       → "A"
    """
    patterns = [
        # "Chapter 3" or "Chapter III"
        (r'^(Chapter\s+[IVXLCDM\d]+)', re.IGNORECASE),
        # "Part IV" or "Part 2"
        (r'^(Part\s+[IVXLCDM\d]+)', re.IGNORECASE),
        # "Section 2.3"
        (r'^(Section\s+[\d.]+)', re.IGNORECASE),
        # "3.1.2" at start of title
        (r'^([\d]+(?:\.[\d]+)+)', 0),
        # "A." or "B." at start (appendix-style)
        (r'^([A-Z])\.', 0),
    ]
    
    for pattern, flag in patterns:
        match = re.match(pattern, title.strip(), flag)
        if match:
            return match.group(1)
    
    return None


def _set_section_end_pages(sections: list) -> None:
    """
    Set end_page for each section based on where the next section starts.
    Walk backwards through sections to propagate end pages up the hierarchy.
    """
    for i in range(len(sections) - 1):
        # Each section's end page is at least the page before the next section starts
        next_start = sections[i + 1]["start_page"]
        sections[i]["end_page"] = next_start
    
    # Last section: end_page stays as start_page (will be updated when page count is known)


def _assign_content_to_sections(blocks: list, sections: list) -> list:
    """
    Assign each non-heading content block to the section it belongs to.
    
    Logic: a content block belongs to the most recently encountered heading
    that precedes it in document order. This is the same assumption a
    human reader makes — text after a heading belongs to that heading's section.
    """
    assignments = []
    current_section_idx = None
    
    for block in blocks:
        if block["type"] == "heading" and block["heading_level"] is not None:
            # Find which section this heading corresponds to
            for section in sections:
                if (section["title"] == block["text"].strip() and 
                    section["start_page"] == block["source_page"]):
                    current_section_idx = section["index"]
                    break
            continue
        
        # Non-heading block: assign to current section
        if block["text"].strip():
            assignments.append({
                "section_index": current_section_idx,
                "type": block["type"],
                "text": block["text"],
                "source_page": block["source_page"],
                "confidence": block.get("confidence"),
            })
    
    return assignments


def _detect_tables(blocks: list) -> list:
    """
    Identify table content in the block stream.
    
    Tables are detected either by:
    1. The OCR engine explicitly tagging them (type == "table")
    2. Heuristics: blocks with lots of tab/column-aligned spacing,
       or blocks with repeated delimiter patterns
    
    This is intentionally conservative — we flag possible tables
    for human review rather than trying to fully parse them automatically.
    """
    tables = []
    
    for i, block in enumerate(blocks):
        is_table = False
        detection_method = None
        
        # Method 1: OCR engine tagged it as a table
        if block["type"] == "table":
            is_table = True
            detection_method = "ocr_tagged"
        
        # Method 2: Heuristic — multiple tab characters or consistent column spacing
        elif block["type"] == "body" and _looks_like_table(block["text"]):
            is_table = True
            detection_method = "heuristic"
        
        if is_table:
            tables.append({
                "block_index": i,
                "source_page": block["source_page"],
                "raw_text": block["text"],
                "detection_method": detection_method,
                "table_data": block.get("table_data"),  # Structured data if OCR provided it
            })
    
    return tables


def _looks_like_table(text: str) -> bool:
    """
    Heuristic check for table-like content.
    
    Looks for:
    - Multiple lines with consistent column-like spacing
    - Lines with multiple tab characters
    - Repeated patterns of numbers separated by spaces
    """
    lines = text.strip().split('\n')
    if len(lines) < 3:
        return False
    
    # Check for tab-separated content
    tab_lines = sum(1 for line in lines if '\t' in line)
    if tab_lines > len(lines) * 0.5:
        return True
    
    # Check for consistent multi-space separation (pseudo-columns)
    multi_space_lines = sum(1 for line in lines if re.search(r'\S\s{3,}\S', line))
    if multi_space_lines > len(lines) * 0.5:
        return True
    
    return False
