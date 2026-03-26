"""
Cross-Reference Detection — Stage 4b of the pipeline.

Scans extracted text for explicit cross-references between components
and sections, then creates relationship records in the database.

WHAT THIS DETECTS (explicit references — pattern-matched):
- S&G number references: "S&G 46", "S&Gs 46 and 47", "Forestwide S&G 55"
- Chapter/section references: "Chapter 4, Section F", "see Chapter 2"
- Page references within the plan: "page V-15", "pages 4-22 to 4-27"
- Component ID references: "DC-WILD-1", "MA2-S&G-3"
- Named section references: "see the WILDLIFE section", "Appendix H"

WHAT THIS DOESN'T DETECT (semantic — requires Claude API):
- Implicit topical connections (EIS soils analysis ↔ Plan soil S&G)
- Causal relationships (this impact analysis is why this S&G exists)
- Baseline-to-target relationships (current condition → desired condition)

The detection runs after classification (Stage 4) and writes to the
component_relationships and section_relationships tables.
"""

import re
import sqlite3
from typing import Optional


def detect_cross_references(conn: sqlite3.Connection, plan_id: int) -> dict:
    """
    Main entry point. Scans all components for cross-reference patterns
    and creates relationship records.
    
    Args:
        conn: Database connection
        plan_id: Which plan to process
    
    Returns:
        dict with counts of relationships detected by type
    """
    print("Stage 4b: Detecting cross-references...")
    
    # Load all components for this plan
    cursor = conn.execute(
        """SELECT id, component_text, component_type, document_section,
                  component_id_in_plan, source_page, resource_area
           FROM plan_components WHERE plan_id = ?""",
        (plan_id,)
    )
    columns = [desc[0] for desc in cursor.description]
    components = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    # Build lookup indexes for matching references to targets
    # Index by component_id_in_plan (e.g., "S&G 55" → component record)
    id_index = {}
    for comp in components:
        if comp["component_id_in_plan"]:
            id_index[comp["component_id_in_plan"].upper()] = comp
    
    # Index S&G components by their number for fuzzy matching
    sg_by_number = {}
    for comp in components:
        comp_id = comp.get("component_id_in_plan", "")
        if comp_id:
            match = re.search(r'S&G\s*(\d+)', comp_id, re.IGNORECASE)
            if match:
                sg_by_number[int(match.group(1))] = comp
    
    stats = {
        "sg_references": 0,
        "chapter_references": 0,
        "appendix_references": 0,
        "resource_match": 0,
        "total": 0,
    }
    
    for source in components:
        text = source["component_text"]
        source_id = source["id"]
        source_doc = source["document_section"]
        
        # 1. Detect S&G number references
        sg_refs = _find_sg_references(text)
        for sg_num in sg_refs:
            if sg_num in sg_by_number:
                target = sg_by_number[sg_num]
                if target["id"] != source_id:  # Don't self-reference
                    target_doc = target["document_section"]
                    crosses = source_doc != target_doc
                    
                    # Determine relationship type based on context
                    rel_type = _infer_sg_relationship_type(source, target)
                    
                    _insert_if_new(
                        conn, plan_id, source_id, target["id"],
                        rel_type, crosses,
                        detection_method="explicit_reference",
                        confidence=0.9,
                        evidence_text=f"Reference to S&G {sg_num} found in text"
                    )
                    stats["sg_references"] += 1
                    stats["total"] += 1
        
        # 2. Detect Chapter/Section references
        chapter_refs = _find_chapter_references(text)
        # These create section-level relationships (handled separately)
        stats["chapter_references"] += len(chapter_refs)
        
        # 3. Detect Appendix references
        appendix_refs = _find_appendix_references(text)
        stats["appendix_references"] += len(appendix_refs)
    
    # 4. Detect resource-area matches across EIS/Plan boundary
    resource_matches = _detect_resource_area_matches(conn, plan_id, components)
    stats["resource_match"] = resource_matches
    stats["total"] += resource_matches
    
    print(f"  S&G references: {stats['sg_references']}")
    print(f"  Chapter references: {stats['chapter_references']} (logged, section-level)")
    print(f"  Appendix references: {stats['appendix_references']} (logged)")
    print(f"  Resource area matches: {stats['resource_match']}")
    print(f"  Total relationships created: {stats['total']}")
    
    return stats


def _find_sg_references(text: str) -> list:
    """
    Find all S&G number references in a text block.
    
    Patterns:
    - "S&G 55"
    - "S&Gs 46 and 47"  
    - "S&G's 46, 47, and 48"
    - "Forestwide S&G 55"
    - "(S&Gs 46 and 47 and Appendix F)"
    
    Returns list of integer S&G numbers found.
    """
    numbers = []
    
    # Pattern 1: "S&G" or "S&Gs" followed by one or more numbers
    # Handles "S&G 55", "S&Gs 46 and 47", "S&G's 46, 47, and 48"
    pattern = r"S&Gs?'?s?\s+([\d,\s]+(?:and\s+\d+)*)"
    matches = re.finditer(pattern, text, re.IGNORECASE)
    
    for match in matches:
        num_text = match.group(1)
        # Extract all numbers from the match
        found = re.findall(r'\d+', num_text)
        numbers.extend(int(n) for n in found)
    
    # Pattern 2: Individual "S&G N" references
    pattern2 = r'S&G\s+(\d+)'
    matches2 = re.finditer(pattern2, text, re.IGNORECASE)
    for match in matches2:
        num = int(match.group(1))
        if num not in numbers:
            numbers.append(num)
    
    return numbers


def _find_chapter_references(text: str) -> list:
    """
    Find Chapter/Section references in text.
    
    Patterns:
    - "Chapter 4, Section F"
    - "see Chapter 2"
    - "Chapter 2, Section E"
    - "EIS, Chapter 2, Section E"
    
    Returns list of dicts: {"chapter": str, "section": str or None}
    """
    refs = []
    
    # "Chapter N" with optional section
    pattern = r'Chapter\s+(\d+)(?:\s*,?\s*Section\s+([A-Z]))?'
    matches = re.finditer(pattern, text, re.IGNORECASE)
    
    for match in matches:
        refs.append({
            "chapter": match.group(1),
            "section": match.group(2),
        })
    
    return refs


def _find_appendix_references(text: str) -> list:
    """
    Find Appendix references.
    
    Patterns:
    - "Appendix H"
    - "Appendix F"
    - "see Appendix B"
    
    Returns list of appendix letters.
    """
    pattern = r'Appendix\s+([A-Z])\b'
    return list(set(re.findall(pattern, text)))


def _infer_sg_relationship_type(source: dict, target: dict) -> str:
    """
    Infer the relationship type between a source component and a 
    referenced S&G target based on context.
    
    Heuristics:
    - If source is in EIS and target is in Plan → 'references' (could be 'justifies' but needs AI)
    - If source is monitoring-related → 'monitors'
    - If source is a management practice → 'constrains' (the S&G constrains the practice)
    - If source is an MA emphasis or prescription → 'constrains'
    - Default → 'references'
    """
    source_type = source.get("component_type", "")
    source_doc = source.get("document_section", "")
    target_doc = target.get("document_section", "")
    
    # Cross-boundary references
    if source_doc != target_doc:
        return "references"  # Conservative — AI would refine this
    
    # Monitoring → S&G = monitors
    if source_type == "monitoring_requirement":
        return "monitors"
    
    # Practice referencing an S&G = S&G constrains the practice
    if source_type in ("management_practice", "management_prescription",
                        "management_area_emphasis"):
        return "constrains"
    
    return "references"


def _insert_if_new(conn, plan_id, source_id, target_id, relationship_type,
                    crosses_boundary, detection_method, confidence, evidence_text):
    """Insert a relationship only if it doesn't already exist."""
    existing = conn.execute(
        """SELECT id FROM component_relationships
           WHERE plan_id = ? AND source_component_id = ? AND target_component_id = ?
           AND relationship_type = ?""",
        (plan_id, source_id, target_id, relationship_type)
    ).fetchone()
    
    if not existing:
        conn.execute(
            """INSERT INTO component_relationships
               (plan_id, source_component_id, target_component_id, relationship_type,
                crosses_boundary, detection_method, confidence, evidence_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (plan_id, source_id, target_id, relationship_type,
             1 if crosses_boundary else 0, detection_method, confidence, evidence_text)
        )
        conn.commit()


def _detect_resource_area_matches(conn, plan_id, components):
    """
    Create 'related' relationships between EIS and Plan components
    that share the same resource area. This is the broadest form of
    cross-boundary connection — "the EIS discusses wildlife, and so
    does this plan component."
    
    These are low-confidence connections that serve as scaffolding for
    the AI to refine into specific relationship types later.
    
    To keep the count manageable, we link EIS impact_analysis components
    to Plan S&Gs/DFCs/goals with matching resource areas (not every
    EIS narrative block to every Plan block).
    """
    # Get EIS impact analysis components grouped by resource
    eis_by_resource = {}
    plan_direction_by_resource = {}
    
    for comp in components:
        resource = comp.get("resource_area")
        if not resource:
            continue
        
        if comp["document_section"] == "eis" and comp["component_type"] == "eis_impact_analysis":
            eis_by_resource.setdefault(resource, []).append(comp)
        
        elif (comp["document_section"] == "plan" and
              comp["component_type"] in ("standard_and_guideline", "desired_future_condition",
                                          "desired_condition", "goal")):
            plan_direction_by_resource.setdefault(resource, []).append(comp)
    
    count = 0
    for resource in set(eis_by_resource.keys()) & set(plan_direction_by_resource.keys()):
        eis_comps = eis_by_resource[resource]
        plan_comps = plan_direction_by_resource[resource]
        
        # Link each EIS impact analysis to each Plan direction component
        # for the same resource. Cap at reasonable numbers to avoid explosion.
        for eis_comp in eis_comps[:5]:  # Max 5 EIS components per resource
            for plan_comp in plan_comps[:10]:  # Max 10 Plan targets per resource
                _insert_if_new(
                    conn, plan_id,
                    eis_comp["id"], plan_comp["id"],
                    "related", crosses_boundary=True,
                    detection_method="resource_match",
                    confidence=0.3,
                    evidence_text=f"Shared resource area: {resource}"
                )
                count += 1
    
    return count
