"""
Database initialization and query helpers for the forest plan pipeline.
Updated to support document_section tagging (EIS vs Plan).
"""

import sqlite3
import os
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def init_database(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    with open(SCHEMA_PATH, "r") as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)
    return conn


def create_plan(conn, **kwargs):
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    cursor = conn.execute(
        f"INSERT INTO plans ({columns}) VALUES ({placeholders})",
        list(kwargs.values())
    )
    conn.commit()
    return cursor.lastrowid


def insert_page(conn, plan_id, page_number, document_section="unknown",
                image_path=None, raw_text=None, ocr_confidence=None):
    cursor = conn.execute(
        """INSERT INTO pages (plan_id, page_number, document_section, image_path, raw_text, ocr_confidence)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (plan_id, page_number, document_section, image_path, raw_text, ocr_confidence)
    )
    conn.commit()
    return cursor.lastrowid


def insert_section(conn, plan_id, title, depth, sort_order, parent_id=None,
                   section_number=None, document_section="unknown",
                   start_page=None, end_page=None):
    cursor = conn.execute(
        """INSERT INTO sections 
           (plan_id, parent_id, depth, sort_order, title, section_number,
            document_section, start_page, end_page)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, parent_id, depth, sort_order, title, section_number,
         document_section, start_page, end_page)
    )
    conn.commit()
    return cursor.lastrowid


def insert_component(conn, plan_id, section_id, component_type, component_text, **kwargs):
    all_fields = {
        "plan_id": plan_id,
        "section_id": section_id,
        "component_type": component_type,
        "component_text": component_text,
        **kwargs
    }
    columns = ", ".join(all_fields.keys())
    placeholders = ", ".join(["?"] * len(all_fields))
    cursor = conn.execute(
        f"INSERT INTO plan_components ({columns}) VALUES ({placeholders})",
        list(all_fields.values())
    )
    conn.commit()
    return cursor.lastrowid


def insert_table(conn, plan_id, section_id=None, title=None, table_type=None,
                 document_section="unknown", row_count=None, col_count=None,
                 source_page_start=None, source_page_end=None):
    cursor = conn.execute(
        """INSERT INTO plan_tables 
           (plan_id, section_id, title, table_type, document_section,
            row_count, col_count, source_page_start, source_page_end)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, section_id, title, table_type, document_section,
         row_count, col_count, source_page_start, source_page_end)
    )
    conn.commit()
    return cursor.lastrowid


def insert_table_cells(conn, table_id, cells):
    for cell in cells:
        conn.execute(
            """INSERT INTO table_cells 
               (table_id, row_index, col_index, cell_text, is_header, row_span, col_span)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (table_id, cell["row_index"], cell["col_index"],
             cell.get("cell_text", ""), cell.get("is_header", 0),
             cell.get("row_span", 1), cell.get("col_span", 1))
        )
    conn.commit()


def log_processing(conn, plan_id, stage, status, message=None, details=None):
    conn.execute(
        "INSERT INTO processing_log (plan_id, stage, status, message, details) VALUES (?, ?, ?, ?, ?)",
        (plan_id, stage, status, message, details)
    )
    conn.commit()


# ============================================================
# QUERY HELPERS
# ============================================================

def get_components_by_type(conn, plan_id, component_type, document_section=None):
    query = """SELECT pc.*, s.title as section_title, s.section_number
               FROM plan_components pc
               JOIN sections s ON pc.section_id = s.id
               WHERE pc.plan_id = ? AND pc.component_type = ?"""
    params = [plan_id, component_type]
    if document_section:
        query += " AND pc.document_section = ?"
        params.append(document_section)
    query += " ORDER BY s.sort_order, pc.id"
    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_components_by_resource(conn, plan_id, resource_area, document_section=None):
    query = """SELECT pc.*, s.title as section_title, s.section_number
               FROM plan_components pc
               JOIN sections s ON pc.section_id = s.id
               WHERE pc.plan_id = ? AND pc.resource_area = ?"""
    params = [plan_id, resource_area]
    if document_section:
        query += " AND pc.document_section = ?"
        params.append(document_section)
    query += " ORDER BY pc.component_type, s.sort_order"
    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_section_tree(conn, plan_id, document_section=None):
    query = "SELECT * FROM sections WHERE plan_id = ?"
    params = [plan_id]
    if document_section:
        query += " AND document_section = ?"
        params.append(document_section)
    query += " ORDER BY depth, sort_order"
    cursor = conn.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_plan_summary(conn, plan_id):
    summary = {}
    cursor = conn.execute(
        "SELECT component_type, COUNT(*) FROM plan_components WHERE plan_id = ? GROUP BY component_type",
        (plan_id,)
    )
    summary["components"] = dict(cursor.fetchall())

    cursor = conn.execute(
        "SELECT document_section, COUNT(*) FROM plan_components WHERE plan_id = ? GROUP BY document_section",
        (plan_id,)
    )
    summary["components_by_document_section"] = dict(cursor.fetchall())

    cursor = conn.execute("SELECT COUNT(*) FROM sections WHERE plan_id = ?", (plan_id,))
    summary["section_count"] = cursor.fetchone()[0]

    cursor = conn.execute("SELECT COUNT(*) FROM plan_tables WHERE plan_id = ?", (plan_id,))
    summary["table_count"] = cursor.fetchone()[0]

    cursor = conn.execute(
        "SELECT COUNT(*), SUM(human_verified) FROM plan_components WHERE plan_id = ?",
        (plan_id,)
    )
    row = cursor.fetchone()
    summary["verification"] = {
        "total_components": row[0],
        "human_verified": row[1] or 0,
        "percent_verified": round((row[1] or 0) / row[0] * 100, 1) if row[0] > 0 else 0
    }
    return summary


# ============================================================
# RELATIONSHIP HELPERS
# ============================================================

def insert_component_relationship(conn, plan_id, source_id, target_id,
                                   relationship_type, crosses_boundary=False,
                                   detection_method="manual", confidence=None,
                                   evidence_text=None, notes=None):
    """Insert a relationship between two components."""
    cursor = conn.execute(
        """INSERT INTO component_relationships
           (plan_id, source_component_id, target_component_id, relationship_type,
            crosses_boundary, detection_method, confidence, evidence_text, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, source_id, target_id, relationship_type,
         1 if crosses_boundary else 0, detection_method, confidence,
         evidence_text, notes)
    )
    conn.commit()
    return cursor.lastrowid


def insert_section_relationship(conn, plan_id, source_id, target_id,
                                 relationship_type, crosses_boundary=False,
                                 detection_method="manual", confidence=None,
                                 notes=None):
    """Insert a relationship between two sections."""
    cursor = conn.execute(
        """INSERT INTO section_relationships
           (plan_id, source_section_id, target_section_id, relationship_type,
            crosses_boundary, detection_method, confidence, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (plan_id, source_id, target_id, relationship_type,
         1 if crosses_boundary else 0, detection_method, confidence, notes)
    )
    conn.commit()
    return cursor.lastrowid


def get_component_relationships(conn, component_id, direction="both"):
    """
    Get all relationships for a component.
    direction: 'outgoing' (this component is source), 'incoming' (this is target), or 'both'
    """
    results = []
    if direction in ("outgoing", "both"):
        cursor = conn.execute(
            """SELECT cr.*, pc.component_text as target_text, pc.component_type as target_type,
                      pc.document_section as target_doc_section, pc.source_page as target_page
               FROM component_relationships cr
               JOIN plan_components pc ON cr.target_component_id = pc.id
               WHERE cr.source_component_id = ?""",
            (component_id,)
        )
        columns = [desc[0] for desc in cursor.description]
        results.extend([{**dict(zip(columns, row)), "direction": "outgoing"} for row in cursor])

    if direction in ("incoming", "both"):
        cursor = conn.execute(
            """SELECT cr.*, pc.component_text as source_text, pc.component_type as source_type,
                      pc.document_section as source_doc_section, pc.source_page as source_page
               FROM component_relationships cr
               JOIN plan_components pc ON cr.source_component_id = pc.id
               WHERE cr.target_component_id = ?""",
            (component_id,)
        )
        columns = [desc[0] for desc in cursor.description]
        results.extend([{**dict(zip(columns, row)), "direction": "incoming"} for row in cursor])

    return results


def get_accountability_chain(conn, plan_id, resource_area):
    """
    The OBAM query: for a given resource area, show the full accountability chain.
    Returns: EIS baseline → desired condition → S&Gs that constrain → monitoring that tracks.
    Components are grouped by their role in the chain.
    """
    chain = {}

    # Desired conditions / DFCs for this resource
    cursor = conn.execute(
        """SELECT * FROM plan_components
           WHERE plan_id = ? AND resource_area = ?
           AND component_type IN ('desired_condition', 'desired_future_condition')
           AND document_section = 'plan'""",
        (plan_id, resource_area)
    )
    columns = [desc[0] for desc in cursor.description]
    chain["desired_conditions"] = [dict(zip(columns, row)) for row in cursor]

    # S&Gs that constrain this resource
    cursor = conn.execute(
        """SELECT * FROM plan_components
           WHERE plan_id = ? AND resource_area = ?
           AND component_type IN ('standard_and_guideline', 'standard', 'guideline')
           AND document_section = 'plan'""",
        (plan_id, resource_area)
    )
    columns = [desc[0] for desc in cursor.description]
    chain["standards_and_guidelines"] = [dict(zip(columns, row)) for row in cursor]

    # Monitoring requirements for this resource
    cursor = conn.execute(
        """SELECT * FROM plan_components
           WHERE plan_id = ? AND resource_area = ?
           AND component_type = 'monitoring_requirement'
           AND document_section = 'plan'""",
        (plan_id, resource_area)
    )
    columns = [desc[0] for desc in cursor.description]
    chain["monitoring"] = [dict(zip(columns, row)) for row in cursor]

    # EIS analysis for this resource
    cursor = conn.execute(
        """SELECT * FROM plan_components
           WHERE plan_id = ? AND resource_area = ?
           AND component_type IN ('eis_narrative', 'eis_impact_analysis')
           AND document_section = 'eis'""",
        (plan_id, resource_area)
    )
    columns = [desc[0] for desc in cursor.description]
    chain["eis_analysis"] = [dict(zip(columns, row)) for row in cursor]

    # Cross-boundary relationships for this resource
    cursor = conn.execute(
        """SELECT cr.*, 
                  src.component_text as source_text, src.component_type as source_type,
                  tgt.component_text as target_text, tgt.component_type as target_type
           FROM component_relationships cr
           JOIN plan_components src ON cr.source_component_id = src.id
           JOIN plan_components tgt ON cr.target_component_id = tgt.id
           WHERE cr.plan_id = ? AND cr.crosses_boundary = 1
           AND (src.resource_area = ? OR tgt.resource_area = ?)""",
        (plan_id, resource_area, resource_area)
    )
    columns = [desc[0] for desc in cursor.description]
    chain["cross_boundary_links"] = [dict(zip(columns, row)) for row in cursor]

    return chain
