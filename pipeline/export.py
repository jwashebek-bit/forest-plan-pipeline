"""
Export Module — Generate human-readable outputs from the structured database.

This closes the loop: PDF → OCR → Structure → Database → Document.
The database is the canonical representation; exports are views.

Currently supports:
- Markdown (structured, with heading hierarchy and component type labels)
- Word document (.docx) — coming in Phase 2

The export demonstrates a key value proposition: once the plan is in the 
database, you can generate different views for different audiences.
A planner might want the full plan by section. A wildlife biologist 
might want all desired conditions and standards for wildlife across
the entire plan. A monitoring team might want just the monitoring
requirements. All from the same structured source.
"""

import os
import json
import sqlite3
from pathlib import Path


def export_markdown(db_path: str, plan_id: int = 1, 
                    output_path: str = None,
                    include_metadata: bool = True,
                    filter_component_type: str = None,
                    filter_resource_area: str = None) -> str:
    """
    Export a structured forest plan from the database as Markdown.
    
    Args:
        db_path: Path to the SQLite database
        plan_id: Which plan to export (default: 1, the first plan)
        output_path: Where to save the .md file (None = return as string)
        include_metadata: Whether to include processing metadata header
        filter_component_type: Only include components of this type
                               (e.g., 'desired_condition', 'standard')
        filter_resource_area: Only include components for this resource
                              (e.g., 'wildlife', 'fire')
    
    Returns:
        The Markdown string (also saves to file if output_path provided)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # Enables dict-like access to columns
    
    # Get plan info
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        raise ValueError(f"Plan ID {plan_id} not found in database")
    
    lines = []
    
    # ── Document Header ──
    lines.append(f"# {plan['name']}")
    lines.append("")
    
    if include_metadata:
        lines.append("---")
        if plan['forest_name']:
            lines.append(f"**Forest:** {plan['forest_name']}  ")
        if plan['plan_year']:
            lines.append(f"**Plan Year:** {plan['plan_year']}  ")
        if plan['region']:
            lines.append(f"**Region:** {plan['region']}  ")
        lines.append(f"**Source:** {Path(plan['source_pdf_path']).name}  ")
        lines.append(f"**Processing Status:** {plan['processing_status']}  ")
        
        if filter_component_type:
            lines.append(f"**Filter:** Component type = `{filter_component_type}`  ")
        if filter_resource_area:
            lines.append(f"**Filter:** Resource area = `{filter_resource_area}`  ")
        
        lines.append("---")
        lines.append("")
    
    # ── Build section tree ──
    sections = conn.execute(
        """SELECT * FROM sections WHERE plan_id = ? ORDER BY sort_order""",
        (plan_id,)
    ).fetchall()
    
    for section in sections:
        # Markdown heading level: depth 0 = ##, depth 1 = ###, etc.
        # (# is reserved for the plan title)
        heading_prefix = "#" * (section['depth'] + 2)
        
        section_header = section['title']
        if section['section_number']:
            section_header = f"{section['section_number']} — {section['title']}"
        
        lines.append(f"{heading_prefix} {section_header}")
        lines.append("")
        
        # Get components for this section
        query = """SELECT * FROM plan_components 
                   WHERE plan_id = ? AND section_id = ?"""
        params = [plan_id, section['id']]
        
        if filter_component_type:
            query += " AND component_type = ?"
            params.append(filter_component_type)
        
        if filter_resource_area:
            query += " AND resource_area = ?"
            params.append(filter_resource_area)
        
        query += " ORDER BY id"
        
        components = conn.execute(query, params).fetchall()
        
        for comp in components:
            # Label each component with its type for clarity
            type_label = comp['component_type'].replace('_', ' ').title()
            comp_id = f" `{comp['component_id_in_plan']}`" if comp['component_id_in_plan'] else ""
            
            # Confidence indicator for unverified auto-classifications
            confidence_note = ""
            if not comp['human_verified'] and comp['classification_confidence']:
                conf = comp['classification_confidence']
                if conf < 0.3:
                    confidence_note = " ⚠️ *low confidence*"
                elif conf < 0.6:
                    confidence_note = " ❓ *moderate confidence*"
            
            lines.append(f"**[{type_label}]{comp_id}**{confidence_note}")
            lines.append(f"{comp['component_text']}")
            lines.append("")
        
        # Get tables for this section
        tables = conn.execute(
            """SELECT * FROM plan_tables WHERE plan_id = ? AND section_id = ?""",
            (plan_id, section['id'])
        ).fetchall()
        
        for table in tables:
            if table['title']:
                lines.append(f"**Table: {table['title']}**")
            
            # Get cells and render as Markdown table
            cells = conn.execute(
                """SELECT * FROM table_cells WHERE table_id = ? 
                   ORDER BY row_index, col_index""",
                (table['id'],)
            ).fetchall()
            
            if cells:
                lines.extend(_render_markdown_table(cells, table['col_count']))
                lines.append("")
    
    conn.close()
    
    markdown_text = "\n".join(lines)
    
    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            f.write(markdown_text)
        print(f"Exported Markdown to {output_path}")
    
    return markdown_text


def _render_markdown_table(cells, col_count: int) -> list:
    """Convert table cells from the database into a Markdown table."""
    if not cells or not col_count:
        return []
    
    lines = []
    
    # Group cells by row
    rows = {}
    for cell in cells:
        row_idx = cell['row_index']
        if row_idx not in rows:
            rows[row_idx] = [''] * col_count
        col_idx = cell['col_index']
        if col_idx < col_count:
            rows[row_idx][col_idx] = cell['cell_text'] or ''
    
    # Render rows
    for row_idx in sorted(rows.keys()):
        row = rows[row_idx]
        lines.append("| " + " | ".join(row) + " |")
        
        # Add separator after first row (assumed header)
        if row_idx == 0:
            lines.append("| " + " | ".join(["---"] * col_count) + " |")
    
    return lines


def export_component_summary(db_path: str, plan_id: int = 1,
                              output_path: str = None) -> str:
    """
    Export a summary view organized by component type rather than 
    document structure. This is the "OBAM view" — it shows all 
    desired conditions together, all standards together, etc.
    
    This is the view that makes the structured database transformative:
    a planner can see ALL desired conditions for the entire plan in
    one place, regardless of which chapter they appear in.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    
    lines = []
    lines.append(f"# {plan['name']} — Component Summary")
    lines.append("")
    lines.append("*Organized by component type for outcomes-based review*")
    lines.append("")
    
    component_types = [
        ('desired_condition', 'Desired Conditions'),
        ('objective', 'Objectives'),
        ('standard', 'Standards'),
        ('guideline', 'Guidelines'),
        ('suitability', 'Suitability Determinations'),
        ('management_approach', 'Management Approaches'),
        ('monitoring_requirement', 'Monitoring Requirements'),
        ('goal', 'Goals'),
    ]
    
    for comp_type, type_label in component_types:
        components = conn.execute(
            """SELECT pc.*, s.title as section_title, s.section_number
               FROM plan_components pc
               JOIN sections s ON pc.section_id = s.id
               WHERE pc.plan_id = ? AND pc.component_type = ?
               ORDER BY pc.resource_area, s.sort_order""",
            (plan_id, comp_type)
        ).fetchall()
        
        if not components:
            continue
        
        lines.append(f"## {type_label} ({len(components)})")
        lines.append("")
        
        # Group by resource area
        current_resource = None
        for comp in components:
            resource = comp['resource_area'] or 'General'
            if resource != current_resource:
                lines.append(f"### {resource.replace('_', ' ').title()}")
                lines.append("")
                current_resource = resource
            
            comp_id = f"`{comp['component_id_in_plan']}` " if comp['component_id_in_plan'] else ""
            section_ref = f"*(Section: {comp['section_title']})*"
            
            lines.append(f"- {comp_id}{comp['component_text']} {section_ref}")
            lines.append("")
    
    conn.close()
    
    markdown_text = "\n".join(lines)
    
    if output_path:
        with open(output_path, "w") as f:
            f.write(markdown_text)
        print(f"Exported component summary to {output_path}")
    
    return markdown_text
