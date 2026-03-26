-- Forest Plan Structured Database Schema
-- Designed for the Open Forest Plan Standard (OFPS) pipeline
-- Updated: supports EIS+Plan dual documents and pre-2012 component types.

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    forest_name TEXT,
    region TEXT,
    state TEXT,
    plan_year INTEGER,
    amendment_info TEXT,
    plan_body_start_page INTEGER,          -- PDF page where Plan body starts (NULL if whole doc is plan)
    source_pdf_path TEXT,
    source_pdf_hash TEXT,
    page_count INTEGER,
    ocr_engine TEXT,
    processing_status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    document_section TEXT DEFAULT 'unknown',
    image_path TEXT,
    ocr_confidence REAL,
    raw_text TEXT,
    UNIQUE(plan_id, page_number)
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    depth INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL,
    title TEXT NOT NULL,
    section_number TEXT,
    document_section TEXT DEFAULT 'unknown',
    start_page INTEGER,
    end_page INTEGER,
    auto_detected INTEGER DEFAULT 1,
    human_verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plan_components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    component_type TEXT NOT NULL CHECK (component_type IN (
        'desired_condition','objective','standard','guideline','suitability',
        'management_approach','monitoring_requirement',
        'goal','standard_and_guideline','management_prescription',
        'management_practice','desired_future_condition',
        'management_area_emphasis',
        'eis_narrative','eis_alternative','eis_impact_analysis',
        'other'
    )),
    document_section TEXT DEFAULT 'unknown',
    component_text TEXT NOT NULL,
    component_id_in_plan TEXT,
    management_area TEXT,
    resource_area TEXT,
    cross_references TEXT DEFAULT '[]',
    auto_classified INTEGER DEFAULT 1,
    classification_confidence REAL,
    human_verified INTEGER DEFAULT 0,
    source_page INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS plan_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    section_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    title TEXT,
    table_type TEXT,
    document_section TEXT DEFAULT 'unknown',
    row_count INTEGER,
    col_count INTEGER,
    source_page_start INTEGER,
    source_page_end INTEGER,
    auto_detected INTEGER DEFAULT 1,
    human_verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS table_cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL REFERENCES plan_tables(id) ON DELETE CASCADE,
    row_index INTEGER NOT NULL,
    col_index INTEGER NOT NULL,
    cell_text TEXT,
    is_header INTEGER DEFAULT 0,
    row_span INTEGER DEFAULT 1,
    col_span INTEGER DEFAULT 1,
    UNIQUE(table_id, row_index, col_index)
);

CREATE TABLE IF NOT EXISTS processing_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    details TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sections_plan ON sections(plan_id);
CREATE INDEX IF NOT EXISTS idx_sections_parent ON sections(parent_id);
CREATE INDEX IF NOT EXISTS idx_sections_docsection ON sections(document_section);
CREATE INDEX IF NOT EXISTS idx_components_plan ON plan_components(plan_id);
CREATE INDEX IF NOT EXISTS idx_components_type ON plan_components(component_type);
CREATE INDEX IF NOT EXISTS idx_components_resource ON plan_components(resource_area);
CREATE INDEX IF NOT EXISTS idx_components_ma ON plan_components(management_area);
CREATE INDEX IF NOT EXISTS idx_components_docsection ON plan_components(document_section);
CREATE INDEX IF NOT EXISTS idx_pages_plan ON pages(plan_id, page_number);
CREATE INDEX IF NOT EXISTS idx_pages_docsection ON pages(document_section);
CREATE INDEX IF NOT EXISTS idx_table_cells ON table_cells(table_id, row_index, col_index);
