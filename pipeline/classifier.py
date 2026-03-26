"""
Component Classification - Stage 4 of the pipeline.

Classifies content blocks into plan component types, with awareness of:
1. Whether the content is in the EIS or Plan body (document_section)
2. Pre-2012 plan language conventions (numbered S&Gs, Forest Goals, DFC)
3. Post-2012 planning rule conventions (desired conditions, standards, guidelines)

The classifier applies different strategies depending on document_section:
- EIS content: tagged as eis_narrative, eis_alternative, or eis_impact_analysis
- Plan content: classified into specific management direction types
"""

import re
from typing import Optional


def classify_component(text: str, section_title: str = "",
                       document_section: str = "unknown") -> dict:
    """
    Classify a text block into a plan component type.
    
    Args:
        text: The content block text
        section_title: Title of the section this text lives in
        document_section: 'eis' or 'plan' — controls which classification
                          strategy is applied
    
    Returns:
        dict with component_type, confidence, scores, matched_patterns
    """
    if document_section == "eis":
        return _classify_eis_content(text, section_title)
    else:
        return _classify_plan_content(text, section_title)


def _classify_eis_content(text: str, section_title: str) -> dict:
    """
    Classify content from the EIS portion.
    Most EIS content is narrative analysis, not management direction.
    """
    combined = f"{section_title}\n{text}".lower()
    
    # Check for alternative descriptions
    alt_patterns = [
        r'alternative\s+(prf|cur|rpa|cmd|nmk|une)',
        r'under\s+this\s+alternative',
        r'this\s+alternative\s+(would|will|has)',
    ]
    for p in alt_patterns:
        if re.search(p, combined, re.IGNORECASE):
            return {
                "component_type": "eis_alternative",
                "confidence": 0.7,
                "scores": {},
                "matched_patterns": [p],
            }
    
    # Check for environmental consequences
    impact_patterns = [
        r'environmental\s+consequences',
        r'(direct|indirect|cumulative)\s+(effect|impact)',
        r'(would\s+result\s+in|impact\s+on|effect\s+on)',
    ]
    for p in impact_patterns:
        if re.search(p, combined, re.IGNORECASE):
            return {
                "component_type": "eis_impact_analysis",
                "confidence": 0.6,
                "scores": {},
                "matched_patterns": [p],
            }
    
    # Default EIS content
    return {
        "component_type": "eis_narrative",
        "confidence": 0.5,
        "scores": {},
        "matched_patterns": [],
    }


def _classify_plan_content(text: str, section_title: str) -> dict:
    """
    Classify content from the Plan body using both pre-2012 and post-2012 patterns.
    """
    combined = f"{section_title}\n{text}"
    scores = {}
    all_matches = {}
    
    for comp_type, patterns in PLAN_CLASSIFICATION_PATTERNS.items():
        score = 0.0
        matches = []
        weight = patterns.get("weight", 1.0)
        
        # Check strong signals in section title (highest weight)
        for p in patterns.get("title_signals", []):
            if re.search(p, section_title, re.IGNORECASE):
                score += 4.0 * weight
                matches.append(f"title: {p}")
        
        # Check strong signals in text
        for p in patterns.get("strong_signals", []):
            if re.search(p, combined, re.IGNORECASE):
                score += 2.0 * weight
                matches.append(f"strong: {p}")
        
        # Check text patterns
        for p in patterns.get("text_patterns", []):
            if re.search(p, text, re.IGNORECASE):
                score += 1.0 * weight
                matches.append(f"text: {p}")
        
        scores[comp_type] = score
        all_matches[comp_type] = matches
    
    if max(scores.values()) == 0:
        return {
            "component_type": "other",
            "confidence": 0.0,
            "scores": scores,
            "matched_patterns": [],
        }
    
    best_type = max(scores, key=scores.get)
    best_score = scores[best_type]
    confidence = min(best_score / 5.0, 1.0)
    
    return {
        "component_type": best_type,
        "confidence": round(confidence, 3),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "matched_patterns": all_matches[best_type],
    }


# ============================================================
# CLASSIFICATION PATTERNS
# Covers both pre-2012 (1982 rule) and post-2012 plan language
# ============================================================

PLAN_CLASSIFICATION_PATTERNS = {
    "standard_and_guideline": {
        "description": "Combined S&G (pre-2012 plans). Numbered in Tahoe plan as S&G 1-71.",
        "title_signals": [
            r'standards?\s+and\s+guidelines?',
            r'S&Gs?',
            r'forestwide\s+standards?\s+and\s+guidelines?',
            r'TAN\s+PAGES',
        ],
        "strong_signals": [
            r'S&G\s*\d+',
            r'Forestwide\s+S&G',
            r'Standard\s+&\s+Guideline',
        ],
        "text_patterns": [
            r'\bshall\b',
            r'\bshall\s+be\b',
            r'\bshall\s+not\b',
            r'\bmust\b(?!\s+be\s+considered)',
            r'\brequired\s+to\b',
            r'\bwill\s+be\s+(maintained|protected|managed)',
        ],
        "weight": 1.3,
    },
    
    "goal": {
        "description": "Forest Goals - broad management philosophy statements.",
        "title_signals": [
            r'forest\s+goals?',
            r'goals?\s+and\s+objectives?',
        ],
        "strong_signals": [
            r'^Goal\s*\d+',
        ],
        "text_patterns": [
            r'(provide|maintain|protect|enhance|promote)\s+(a\s+)?(broad\s+)?(spectrum|range|variety)',
            r'(manage|develop|protect).{0,30}(in\s+accordance|consistent\s+with)',
            r'(the\s+goal|management\s+goal)\s+(is|for)',
        ],
        "weight": 1.0,
    },
    
    "desired_future_condition": {
        "description": "Desired Future Condition - how the forest should look in the future.",
        "title_signals": [
            r'desired\s+future\s+condition',
            r'DFC',
        ],
        "strong_signals": [
            r'desired\s+future\s+condition',
        ],
        "text_patterns": [
            r'(in\s+the\s+year\s+20\d{2}|over\s+the\s+next\s+\d+\s+years)',
            r'(the\s+forest\s+will|the\s+forest\s+should)\s+(appear|look|be)',
            r'(will\s+be\s+characterized|will\s+consist\s+of)',
        ],
        "weight": 1.2,
    },
    
    # Post-2012 types (keep for generalizability)
    "desired_condition": {
        "description": "2012 rule desired conditions",
        "title_signals": [
            r'desired\s+condition(?!.*future)',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'(are|is)\s+(maintained|restored|functioning|resilient|diverse|connected)',
            r'(maintain|restore|support|provide|reflect)\s+(a\s+)?(natural|historic|desired)',
            r'(productivity|function|integrity|composition|structure).{0,20}(maintained|restored)',
        ],
        "weight": 1.0,
    },
    
    "standard": {
        "description": "Mandatory constraints (post-2012 separated standards)",
        "title_signals": [
            r'\bstandards?\b(?!\s+and\s+guideline)',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'\bshall\b',
            r'\bmust\b(?!\s+be\s+considered)',
            r'\bnot\s+(be\s+)?permitted\b',
            r'^(No|Do not|Prohibit)',
        ],
        "weight": 0.8,  # Lower weight than S&G to avoid competing with combined type
    },
    
    "guideline": {
        "description": "Conditional constraints (post-2012 separated guidelines)",
        "title_signals": [
            r'\bguidelines?\b(?!\s*(and|&))',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'\bshould\b',
            r'to\s+the\s+extent\s+(practicable|feasible)',
        ],
        "weight": 0.8,
    },
    
    "objective": {
        "description": "Measurable targets with timeframes.",
        "title_signals": [
            r'\bobjectives?\b',
            r'forest\s+objectives?',
        ],
        "strong_signals": [
            r'within\s+\d+\s+years',
            r'by\s+(the\s+year\s+)?\d{4}',
        ],
        "text_patterns": [
            r'\d+[\s,]*(acres?|miles?|stream\s*miles?|MMBF)',
            r'(annually|per\s+year|per\s+decade)',
            r'(restore|treat|improve|complete|reduce).{0,30}\d+',
        ],
        "weight": 1.0,
    },
    
    "management_area_emphasis": {
        "description": "The key management emphasis statement for each MA.",
        "title_signals": [
            r'management\s+(area\s+)?emphasis',
        ],
        "strong_signals": [
            r'the\s+emphasis\s+(of|for|in)\s+this\s+(management\s+)?area',
            r'this\s+area\s+will\s+be\s+managed\s+(primarily\s+)?for',
        ],
        "text_patterns": [
            r'(primary|principal)\s+(emphasis|objective|purpose)',
            r'(emphasis\s+is\s+on|managed\s+for)',
        ],
        "weight": 1.1,
    },
    
    "management_practice": {
        "description": "Specific tools and treatments (Yellow Pages).",
        "title_signals": [
            r'management\s+practices?',
            r'YELLOW\s+PAGES',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'(practice|treatment|technique|method)\s+(is|will\s+be)\s+(used|applied|implemented)',
        ],
        "weight": 0.9,
    },
    
    "management_prescription": {
        "description": "Area-specific direction packages.",
        "title_signals": [
            r'management\s+prescription',
            r'management\s+area\s+direction',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'prescription\s+(for|is|includes)',
            r'(this|the)\s+management\s+area',
        ],
        "weight": 0.9,
    },
    
    "suitability": {
        "description": "Land suitability determinations.",
        "title_signals": [
            r'suit(able|ability)',
        ],
        "strong_signals": [
            r'suit(able|ability)\s+for',
            r'(not\s+)?suitable\s+for\s+timber',
        ],
        "text_patterns": [
            r'(designated|identified|classified)\s+as\s+(suitable|not\s+suitable)',
            r'(lands?\s+are|areas?\s+are)\s+(suitable|unsuitable)',
        ],
        "weight": 1.0,
    },
    
    "monitoring_requirement": {
        "description": "What must be tracked and evaluated.",
        "title_signals": [
            r'monitor(ing)?',
        ],
        "strong_signals": [
            r'monitor(ing)?\s+(requirement|indicator|element|question)',
        ],
        "text_patterns": [
            r'(monitor|track|measure|assess|evaluat)',
            r'(indicator|metric|benchmark)',
            r'(frequency|interval).{0,20}(annual|year|periodic)',
        ],
        "weight": 0.9,
    },
    
    "management_approach": {
        "description": "Strategies and practices (not binding).",
        "title_signals": [
            r'management\s+(approach|strategy)',
        ],
        "strong_signals": [],
        "text_patterns": [
            r'(may|could|might)\s+(be\s+)?(used|applied|considered)',
            r'(tools?\s+include|methods?\s+include)',
        ],
        "weight": 0.7,
    },
}


def classify_content_blocks(content_blocks: list, sections: list,
                            plan_body_start_page: int = None) -> list:
    """
    Classify all content blocks, applying document_section awareness.
    
    Args:
        content_blocks: From structure detection
        sections: The section hierarchy
        plan_body_start_page: PDF page where plan body starts.
                              Pages before this are EIS.
    """
    print("Stage 4: Classifying plan components...")
    
    section_titles = {s["index"]: s["title"] for s in sections}
    classified = []
    type_counts = {}
    
    for block in content_blocks:
        if len(block["text"].strip()) < 10:
            continue
        
        section_idx = block.get("section_index")
        section_title = section_titles.get(section_idx, "")
        source_page = block.get("source_page", 0)
        
        # Determine document section based on page number
        if plan_body_start_page and source_page:
            doc_section = "plan" if source_page >= plan_body_start_page else "eis"
        else:
            doc_section = "unknown"
        
        classification = classify_component(block["text"], section_title, doc_section)
        classification["document_section"] = doc_section
        
        block["classification"] = classification
        classified.append(block)
        
        comp_type = classification["component_type"]
        type_counts[comp_type] = type_counts.get(comp_type, 0) + 1
    
    print(f"  Classified {len(classified)} content blocks:")
    for comp_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"    {comp_type}: {count}")
    
    return classified


def extract_component_id(text: str) -> Optional[str]:
    """
    Extract a plan-specific component identifier.
    Handles both post-2012 (DC-WILD-1) and pre-2012 (S&G 55) formats.
    """
    patterns = [
        # Pre-2012: S&G 55, S&G 46
        r'\b(S&G\s*\d+)\b',
        # Post-2012: DC-WILD-1, S-FIRE-3
        r'\b([A-Z]{1,3}-[A-Z]{2,6}-\d+)\b',
        r'\b([A-Z]{2,4}-[A-Z]{2,6}-\d+)\b',
        # Management area prefixed: MA2-DC-3
        r'\b(MA\d+-[A-Z]{1,3}-\d+)\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def extract_resource_area(text: str, section_title: str = "") -> Optional[str]:
    """Identify which resource area a component pertains to."""
    combined = f"{section_title} {text}".lower()
    
    resource_keywords = {
        "wildlife": ["wildlife", "habitat", "species", "den", "nest", "forage", "migration",
                      "spotted owl", "furbearer", "marten", "fisher", "deer"],
        "fisheries": ["fish", "aquatic", "stream", "riparian", "anadromous", "salmonid", "trout"],
        "fire": ["fire", "wildfire", "prescribed burn", "fuels", "suppression", "wildland fire"],
        "watershed": ["watershed", "water quality", "hydrology", "erosion", "sediment",
                       "flood", "cumulative watershed"],
        "timber": ["timber", "harvest", "logging", "silvicultur", "regeneration", "sawtimber",
                    "asq", "allowable sale", "mmbf"],
        "recreation": ["recreation", "trail", "campground", "visitor", "scenic", "interpretive",
                        "ros ", "ohv", "off-highway"],
        "wilderness": ["wilderness", "roadless", "primitive", "untrammeled", "solitude"],
        "botany": ["plant", "botanical", "rare plant", "sensitive plant", "invasive", "noxious"],
        "soils": ["soil", "compaction", "erosion", "productivity", "soil productivity"],
        "cultural": ["cultural", "heritage", "archaeological", "tribal", "historic"],
        "minerals": ["mineral", "mining", "geolog"],
        "range": ["range", "grazing", "livestock", "allotment"],
        "visual": ["visual", "vqo", "scenery", "scenic", "visual quality"],
        "diversity": ["diversity", "seral stage", "old growth", "old-growth", "snag", "down log"],
        "lands": ["land exchange", "special use", "easement", "right-of-way", "boundary",
                   "urban", "wildland interface"],
    }
    
    scores = {}
    for resource, keywords in resource_keywords.items():
        score = sum(1 for kw in keywords if kw in combined)
        if score > 0:
            scores[resource] = score
    
    if not scores:
        return None
    return max(scores, key=scores.get)


def detect_plan_body_start(pages_ocr: list) -> Optional[int]:
    """
    Scan OCR output to find the page where the Plan body begins.
    
    Looks for landmark phrases that typically mark the start of
    management direction in plans bound with their EIS:
    - "MANAGEMENT DIRECTION" as a chapter/section heading
    - "This chapter provides direction for managers"
    - "Forest Goals"
    
    Returns the page number, or None if not detected.
    """
    # Search patterns, in order of specificity
    landmarks = [
        # Very specific: the management direction introduction
        (r'MANAGEMENT\s+DIRECTION\s*\n\s*INTRODUCTION', 10),
        # Chapter heading for management direction
        (r'^\s*V\.\s*\n\s*MANAGEMENT\s+DIRECTION', 8),
        # General management direction heading  
        (r'MANAGEMENT\s+DIRECTION', 5),
        # Forest goals section
        (r'Forest\s+Goals.*reflect\s+the\s+overall\s+management\s+philosophy', 7),
    ]
    
    best_page = None
    best_score = 0
    
    for page in pages_ocr:
        page_num = page["page_number"]
        # Only search in the second half of the document
        # (plan body is always after the EIS)
        full_text = "\n".join(b.get("text", "") for b in page.get("blocks", []))
        
        for pattern, score in landmarks:
            if re.search(pattern, full_text):
                if score > best_score:
                    best_score = score
                    best_page = page_num
    
    return best_page
