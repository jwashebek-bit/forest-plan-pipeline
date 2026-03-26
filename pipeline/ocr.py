"""
OCR Module — Stage 1 & 2 of the pipeline.

Handles:
1. Rendering PDF pages to images (for the review UI and for OCR input)
2. Running OCR to extract text with positional/layout information
3. Extracting tables separately (they need special handling)

The OCR engine is pluggable — you can swap between Marker, Docling,
or Tesseract without changing the rest of the pipeline. Each engine
returns a standardized output format.

STANDARDIZED OUTPUT FORMAT (per page):
{
    "page_number": int,
    "blocks": [
        {
            "type": "heading" | "body" | "table" | "list" | "caption" | "page_number" | "other",
            "text": str,
            "confidence": float (0.0-1.0),
            "bbox": [x0, y0, x1, y1] or None,  # bounding box coordinates
            "heading_level": int or None,         # 1-6 for headings
            "table_data": [...] or None,          # structured table if type == "table"
        }
    ]
}
"""

import os
import json
import hashlib
from pathlib import Path
from typing import Generator


def get_pdf_info(pdf_path: str) -> dict:
    """
    Extract basic metadata from a PDF without full processing.
    Returns page count, file size, hash, and whether it's scanned or born-digital.
    
    This is the first thing the pipeline runs — it tells you what
    you're working with before committing to the full OCR process.
    """
    import fitz  # PyMuPDF — the library is called 'fitz' for historical reasons
    
    doc = fitz.open(pdf_path)
    
    # Calculate file hash for integrity tracking
    sha256 = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    
    # Check if born-digital by trying to extract text from first few pages
    # If we get substantial text, it's born-digital; if not, it's scanned
    sample_text = ""
    sample_pages = min(5, len(doc))
    for i in range(sample_pages):
        sample_text += doc[i].get_text()
    
    # Heuristic: if average text per page is < 50 characters, likely scanned
    avg_chars = len(sample_text) / sample_pages if sample_pages > 0 else 0
    is_scanned = avg_chars < 50
    
    info = {
        "page_count": len(doc),
        "file_size_mb": round(os.path.getsize(pdf_path) / (1024 * 1024), 1),
        "sha256": sha256.hexdigest(),
        "is_scanned": is_scanned,
        "avg_chars_per_page": round(avg_chars, 1),
        "metadata": doc.metadata,  # Title, author, etc. if present
    }
    
    doc.close()
    return info


def render_pages(pdf_path: str, output_dir: str, dpi: int = 300,
                 page_range: tuple = None) -> list:
    """
    Render PDF pages to PNG images.
    
    This serves two purposes:
    1. Input for OCR engines that need image files
    2. Source images for the review UI (so practitioners can see
       the original page alongside the extracted structure)
    
    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory to save page images
        dpi: Resolution for rendering. 300 is standard for OCR.
        page_range: Optional (start, end) tuple for partial processing.
                    Pages are 0-indexed. None = all pages.
    
    Returns:
        List of dicts with page_number and image_path for each rendered page.
    """
    import fitz
    
    os.makedirs(output_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    
    start = page_range[0] if page_range else 0
    end = page_range[1] if page_range else len(doc)
    
    rendered = []
    
    # The zoom factor converts DPI to PyMuPDF's internal units
    # PyMuPDF default is 72 DPI, so zoom = target_dpi / 72
    zoom = dpi / 72
    matrix = fitz.Matrix(zoom, zoom)
    
    for page_num in range(start, end):
        page = doc[page_num]
        
        # Render page to a pixmap (pixel map — basically an image in memory)
        pixmap = page.get_pixmap(matrix=matrix)
        
        # Save as PNG
        image_filename = f"page_{page_num + 1:04d}.png"  # 1-indexed, zero-padded
        image_path = os.path.join(output_dir, image_filename)
        pixmap.save(image_path)
        
        rendered.append({
            "page_number": page_num + 1,  # Convert to 1-indexed for human readability
            "image_path": image_path,
            "width": pixmap.width,
            "height": pixmap.height,
        })
        
        # Print progress every 10 pages
        if (page_num + 1) % 10 == 0:
            print(f"  Rendered page {page_num + 1}/{end}")
    
    doc.close()
    print(f"  Rendered {len(rendered)} pages to {output_dir}")
    return rendered


# ============================================================
# OCR ENGINE: Marker
# Primary engine for scanned documents. Uses deep learning
# models for text detection, recognition, and layout analysis.
# ============================================================

def ocr_with_marker(pdf_path: str, output_dir: str = None) -> list:
    """
    Run Marker on a PDF to get structured text output.
    
    Marker handles the full pipeline internally:
    - Page rendering
    - Text detection and recognition (via Surya models)
    - Layout analysis (headings, body, tables, figures)
    - Outputs structured Markdown
    
    We parse Marker's output into our standardized format.
    
    Returns:
        List of page-level dicts in standardized format.
    """
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except ImportError:
        raise ImportError(
            "Marker is not installed. Install with:\n"
            "  pip install marker-pdf --break-system-packages\n"
            "Note: This downloads several GB of ML models on first run."
        )
    
    print("  Loading Marker models (this takes a moment on first run)...")
    models = create_model_dict()
    
    converter = PdfConverter(artifact_dict=models)
    rendered = converter(pdf_path)
    
    # Marker returns a Document object with pages, blocks, etc.
    # We convert to our standardized format
    pages_output = []
    
    for page_idx, page in enumerate(rendered.pages):
        blocks = []
        for block in page.blocks:
            block_dict = {
                "type": _classify_marker_block(block),
                "text": block.text if hasattr(block, 'text') else str(block),
                "confidence": getattr(block, 'confidence', None),
                "bbox": getattr(block, 'bbox', None),
                "heading_level": _get_marker_heading_level(block),
                "table_data": None,  # Tables handled separately
            }
            blocks.append(block_dict)
        
        pages_output.append({
            "page_number": page_idx + 1,
            "blocks": blocks,
        })
    
    return pages_output


def _classify_marker_block(block) -> str:
    """Map Marker's block types to our standardized types."""
    block_type = type(block).__name__.lower()
    mapping = {
        "heading": "heading",
        "paragraph": "body",
        "table": "table",
        "list": "list",
        "caption": "caption",
        "pagenumber": "page_number",
    }
    return mapping.get(block_type, "other")


def _get_marker_heading_level(block) -> int:
    """Extract heading level from a Marker block."""
    if hasattr(block, 'heading_level'):
        return block.heading_level
    block_type = type(block).__name__
    if "Heading" in block_type:
        # Try to extract level from class name or attributes
        return getattr(block, 'level', 1)
    return None


# ============================================================
# OCR ENGINE: Tesseract (fallback / lightweight alternative)
# Open-source OCR engine. Less accurate than Marker for
# layout analysis but lighter and faster to install.
# ============================================================

def ocr_with_tesseract(pdf_path: str, image_dir: str, 
                       dpi: int = 300, lang: str = "eng") -> list:
    """
    Run Tesseract OCR on pre-rendered page images.
    
    Unlike Marker, Tesseract doesn't do layout classification
    (it doesn't know what's a heading vs. body text). It gives you
    text with bounding boxes, and you need to classify blocks yourself
    based on font size, position, and other heuristics.
    
    This is the fallback engine: simpler to install, works everywhere,
    but requires more downstream processing.
    
    Args:
        pdf_path: Path to the PDF (used for metadata only)
        image_dir: Directory containing pre-rendered page images
        dpi: DPI the images were rendered at (needed for coordinate math)
        lang: Tesseract language code
    
    Returns:
        List of page-level dicts in standardized format.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        raise ImportError(
            "Tesseract dependencies not installed. Install with:\n"
            "  pip install pytesseract Pillow --break-system-packages\n"
            "  sudo apt-get install tesseract-ocr"
        )
    
    # Get sorted list of page images
    image_files = sorted([
        f for f in os.listdir(image_dir) 
        if f.endswith(".png") and f.startswith("page_")
    ])
    
    pages_output = []
    
    for img_file in image_files:
        page_num = int(img_file.split("_")[1].split(".")[0])  # Extract page number from filename
        img_path = os.path.join(image_dir, img_file)
        
        # Open image
        image = Image.open(img_path)
        
        # Run Tesseract with detailed output (gives bounding boxes per text block)
        # tsv output format gives us: level, page, block, paragraph, line, word,
        # plus bounding box (left, top, width, height) and confidence
        tsv_data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
        
        # Also get the page-level confidence
        page_confidence = _calculate_page_confidence(tsv_data)
        
        # Group words into text blocks using Tesseract's block numbering
        blocks = _group_tesseract_blocks(tsv_data, dpi)
        
        pages_output.append({
            "page_number": page_num,
            "blocks": blocks,
            "ocr_confidence": page_confidence,
        })
        
        if page_num % 10 == 0:
            print(f"  OCR'd page {page_num} (confidence: {page_confidence:.2f})")
    
    return pages_output


def _calculate_page_confidence(tsv_data: dict) -> float:
    """Calculate average OCR confidence for a page, ignoring empty detections."""
    confidences = [
        c for c, text in zip(tsv_data["conf"], tsv_data["text"])
        if c > 0 and text.strip()  # Ignore empty/whitespace detections
    ]
    return sum(confidences) / len(confidences) / 100.0 if confidences else 0.0


def _group_tesseract_blocks(tsv_data: dict, dpi: int) -> list:
    """
    Group Tesseract's word-level output into text blocks.
    
    Tesseract assigns each word a block number and paragraph number.
    We group by block, then apply heuristics to classify each block
    as heading, body, etc. based on:
    - Text height (larger = more likely a heading)
    - Position on page (top/bottom = more likely header/footer)
    - Content patterns (page numbers, bullet points, etc.)
    """
    from collections import defaultdict
    
    # Group words by (block_num, par_num)
    block_groups = defaultdict(list)
    for i in range(len(tsv_data["text"])):
        text = tsv_data["text"][i].strip()
        if not text:
            continue
        
        key = (tsv_data["block_num"][i], tsv_data["par_num"][i])
        block_groups[key].append({
            "text": text,
            "left": tsv_data["left"][i],
            "top": tsv_data["top"][i],
            "width": tsv_data["width"][i],
            "height": tsv_data["height"][i],
            "conf": tsv_data["conf"][i],
        })
    
    # Convert groups to standardized blocks
    blocks = []
    for (block_num, par_num), words in sorted(block_groups.items()):
        # Reconstruct full text for this block
        full_text = " ".join(w["text"] for w in words)
        
        # Calculate bounding box encompassing all words
        x0 = min(w["left"] for w in words)
        y0 = min(w["top"] for w in words)
        x1 = max(w["left"] + w["width"] for w in words)
        y1 = max(w["top"] + w["height"] for w in words)
        
        # Average word height (used for heading detection heuristic)
        avg_height = sum(w["height"] for w in words) / len(words)
        
        # Average confidence
        avg_conf = sum(w["conf"] for w in words) / len(words) / 100.0
        
        # Classify this block using heuristics
        block_type, heading_level = _classify_tesseract_block(
            full_text, avg_height, y0, dpi
        )
        
        blocks.append({
            "type": block_type,
            "text": full_text,
            "confidence": round(avg_conf, 3),
            "bbox": [x0, y0, x1, y1],
            "heading_level": heading_level,
            "table_data": None,
            "_avg_text_height": avg_height,  # Keep for debugging
        })
    
    return blocks


def _classify_tesseract_block(text: str, avg_height: float, 
                               y_position: float, dpi: int) -> tuple:
    """
    Classify a text block using heuristics.
    
    This is where forest-plan-specific knowledge helps:
    - "Chapter", "Part", "Section" in text → heading
    - Very short text + large font → heading
    - Numbered patterns like "3.1.2" at start → heading
    - Text matching page number patterns → page_number
    
    Returns:
        (block_type, heading_level) tuple
    
    NOTE: These heuristics work for "typical" forest plans but will
    need tuning. This is exactly where the practitioner review step
    catches what automation misses.
    """
    import re
    
    text_stripped = text.strip()
    
    # Page number detection (just a number, possibly with dashes)
    if re.match(r'^[\-—–]?\s*\d{1,4}\s*[\-—–]?$', text_stripped):
        return ("page_number", None)
    
    # Heading detection based on text patterns common in forest plans
    # Level 1: Part/Chapter-level headings
    if re.match(r'^(PART|CHAPTER|SECTION)\s+[IVXLCDM\d]+', text_stripped, re.IGNORECASE):
        return ("heading", 1)
    
    # Level 2: Major section headings (often ALL CAPS in forest plans)
    if text_stripped.isupper() and len(text_stripped) < 100 and len(text_stripped) > 3:
        return ("heading", 2)
    
    # Level 3: Numbered subsections
    if re.match(r'^\d+\.\d+', text_stripped) and len(text_stripped) < 150:
        return ("heading", 3)
    
    # Font size heuristic: if average character height is notably large
    # (at 300 DPI, standard 12pt body text is ~50px tall; 
    #  headings at 14-18pt would be ~58-75px)
    height_threshold = dpi * 0.2  # ~60px at 300 DPI
    if avg_height > height_threshold and len(text_stripped) < 100:
        return ("heading", 2)
    
    # Default to body text
    return ("body", None)


# ============================================================
# TEXT EXTRACTION: Born-digital (no OCR needed)
# Uses PyMuPDF to extract text directly from the PDF with
# font size, position, and style information. Much faster
# and more accurate than OCR for PDFs with embedded text.
# ============================================================

def extract_born_digital(pdf_path: str, output_dir: str = None, 
                         dpi: int = 300) -> list:
    """
    Extract text directly from a born-digital PDF using PyMuPDF.
    
    Instead of rendering pages to images and running OCR, this reads
    the text layer embedded in the PDF along with font metadata
    (size, bold, italic, font name). Font size is the primary signal
    for heading detection — larger text = more likely a heading.
    
    This is the preferred engine when the PDF has embedded text
    (avg chars/page > 50 in the PDF analysis stage).
    
    Returns:
        List of page-level dicts in standardized format.
    """
    import fitz
    
    doc = fitz.open(pdf_path)
    pages_output = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Extract text blocks with detailed info using "dict" output
        # This gives us: spans with text, font name, font size, color,
        # and bounding box — everything we need for heading detection.
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        
        blocks = []
        
        for block in page_dict.get("blocks", []):
            # Skip image blocks (type 1 = image, type 0 = text)
            if block.get("type") != 0:
                continue
            
            # Each block contains "lines", each line contains "spans"
            # A span is a run of text with consistent formatting
            block_text_parts = []
            font_sizes = []
            is_bold = False
            
            for line in block.get("lines", []):
                line_text_parts = []
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        line_text_parts.append(text)
                        font_sizes.append(span.get("size", 12))
                        # Check for bold: font name often contains "Bold"
                        # or the flags field has bit 4 set
                        font_name = span.get("font", "")
                        flags = span.get("flags", 0)
                        if "bold" in font_name.lower() or "Bold" in font_name or (flags & 16):
                            is_bold = True
                
                if line_text_parts:
                    block_text_parts.append(" ".join(line_text_parts))
            
            full_text = "\n".join(block_text_parts)
            if not full_text.strip():
                continue
            
            # Calculate average font size for this block
            avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
            max_font_size = max(font_sizes) if font_sizes else 12
            
            # Get bounding box
            bbox = block.get("bbox")  # (x0, y0, x1, y1)
            
            # Classify block based on font size and formatting
            block_type, heading_level = _classify_born_digital_block(
                full_text, avg_font_size, max_font_size, is_bold, bbox, page.rect.height
            )
            
            blocks.append({
                "type": block_type,
                "text": full_text,
                "confidence": 1.0,  # Born-digital text is exact (no OCR uncertainty)
                "bbox": list(bbox) if bbox else None,
                "heading_level": heading_level,
                "table_data": None,
                "_avg_font_size": round(avg_font_size, 1),
                "_max_font_size": round(max_font_size, 1),
                "_is_bold": is_bold,
            })
        
        pages_output.append({
            "page_number": page_num + 1,
            "blocks": blocks,
            "ocr_confidence": 1.0,
        })
        
        if (page_num + 1) % 100 == 0:
            print(f"  Extracted page {page_num + 1}/{len(doc)}")
    
    doc.close()
    print(f"  Extracted text from {len(pages_output)} pages")
    return pages_output


def _classify_born_digital_block(text: str, avg_font_size: float, 
                                   max_font_size: float, is_bold: bool,
                                   bbox: tuple, page_height: float) -> tuple:
    """
    Classify a text block from born-digital extraction.
    
    Uses font size as the primary heading signal — this is far more
    reliable than the heuristics needed for OCR output, because we
    know the actual font size rather than guessing from pixel height.
    
    Typical forest plan font sizes:
    - Body text: 10-12pt
    - Subsection headings: 12-14pt (often bold)
    - Section headings: 14-16pt
    - Chapter headings: 16-20pt+
    """
    import re
    
    text_stripped = text.strip()
    
    # Page number detection
    if re.match(r'^[\-—–]?\s*\d{1,4}\s*[\-—–]?$', text_stripped):
        return ("page_number", None)
    
    # TOC page markers (e.g., "Table of Contents - Page - 1")
    if re.match(r'^table\s+of\s+contents', text_stripped, re.IGNORECASE):
        return ("other", None)
    
    # Chapter/Part level headings — detected by keyword regardless of font
    if re.match(r'^(PART|CHAPTER|SECTION)\s+[IVXLCDM\d]+', text_stripped, re.IGNORECASE):
        return ("heading", 1)
    
    # Font-size-based heading detection
    # Large font (16pt+) = Level 1 heading
    if max_font_size >= 16 and len(text_stripped) < 200:
        return ("heading", 1)
    
    # Medium-large font (13-16pt) or bold with elevated size = Level 2
    if max_font_size >= 13 and len(text_stripped) < 200:
        return ("heading", 2)
    
    # Bold text at standard body size with short length = Level 3
    if is_bold and avg_font_size >= 10 and len(text_stripped) < 150:
        # But only if it's relatively short — a bold paragraph isn't a heading
        line_count = text_stripped.count('\n') + 1
        if line_count <= 3:
            return ("heading", 3)
    
    # Numbered subsections at body font size
    if re.match(r'^\d+\.\d+', text_stripped) and len(text_stripped) < 150:
        return ("heading", 3)
    
    # ALL CAPS short text — likely a heading, but be more conservative
    # than with OCR. Require it to be genuinely short (not a full paragraph).
    if (text_stripped.isupper() and len(text_stripped) < 60 
        and len(text_stripped) > 3 and '\n' not in text_stripped):
        return ("heading", 2)
    
    # Default to body text
    return ("body", None)


# ============================================================
# ENGINE SELECTOR — choose which OCR engine to use
# ============================================================

def run_ocr(pdf_path: str, engine: str = "tesseract", 
            output_dir: str = "outputs", dpi: int = 300, **kwargs) -> list:
    """
    Main entry point for OCR. Selects and runs the appropriate engine.
    
    Args:
        pdf_path: Path to the PDF file
        engine: "marker", "tesseract", "pymupdf", or "docling"
        output_dir: Base directory for outputs
        dpi: Resolution for page rendering
    
    Returns:
        List of page-level dicts in standardized format
    """
    print(f"Starting extraction with engine: {engine}")
    
    if engine == "marker":
        return ocr_with_marker(pdf_path, output_dir)
    
    elif engine == "tesseract":
        # Tesseract needs pre-rendered images
        image_dir = os.path.join(output_dir, "page_images")
        print("Stage 1: Rendering PDF pages to images...")
        render_pages(pdf_path, image_dir, dpi=dpi)
        print("Stage 2: Running Tesseract OCR...")
        return ocr_with_tesseract(pdf_path, image_dir, dpi=dpi)
    
    elif engine == "pymupdf":
        # Direct text extraction — no OCR needed
        print("Stage 2: Extracting text directly (born-digital mode)...")
        return extract_born_digital(pdf_path, output_dir, dpi=dpi)
    
    elif engine == "docling":
        raise NotImplementedError("Docling engine coming soon")
    
    else:
        raise ValueError(f"Unknown engine: {engine}. Use 'pymupdf', 'tesseract', or 'marker'.")
