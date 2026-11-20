import sqlite3
from pathlib import Path

SCHEMA="""
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS evidence(event_id TEXT PRIMARY KEY,kind TEXT NOT NULL,object_id TEXT NOT NULL,event_time TEXT NOT NULL,sha256 TEXT NOT NULL,body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publisher_authority(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),publisher TEXT NOT NULL,approval_no TEXT NOT NULL,valid_from TEXT NOT NULL,valid_until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edition_evidence(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),approval_no TEXT NOT NULL,edition TEXT NOT NULL,publisher TEXT NOT NULL,predecessor TEXT,document_hash TEXT NOT NULL,form TEXT NOT NULL,strength_mg INTEGER NOT NULL,markets TEXT NOT NULL,sections TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edition_withdrawal(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),approval_no TEXT NOT NULL,edition TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edition_decision(approval_no TEXT NOT NULL,edition TEXT NOT NULL,document_hash TEXT,decision TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(approval_no,edition));
CREATE TABLE IF NOT EXISTS section_translation(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),approval_no TEXT NOT NULL,edition TEXT NOT NULL,language TEXT NOT NULL,section_id TEXT NOT NULL,source_hash TEXT NOT NULL,translated_text TEXT NOT NULL,submitted_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS review_signature(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),approval_no TEXT NOT NULL,edition TEXT NOT NULL,language TEXT NOT NULL,reviewer TEXT NOT NULL,source_hash TEXT NOT NULL,valid_until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS language_release(approval_no TEXT NOT NULL,edition TEXT NOT NULL,language TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(approval_no,edition,language));
CREATE TABLE IF NOT EXISTS sku_evidence(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),sku TEXT NOT NULL,approval_no TEXT NOT NULL,edition TEXT NOT NULL,form TEXT NOT NULL,strength TEXT NOT NULL,market TEXT NOT NULL,language TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS label_binding(sku TEXT PRIMARY KEY,edition TEXT NOT NULL,document_hash TEXT,decision TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publication_receipt(id INTEGER PRIMARY KEY AUTOINCREMENT,sku TEXT NOT NULL,decision TEXT NOT NULL,edition TEXT NOT NULL,document_hash TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS inventory_label(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),lot TEXT NOT NULL,sku TEXT NOT NULL,printed_edition TEXT NOT NULL,units INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS replacement_plan(lot TEXT PRIMARY KEY,priority INTEGER NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_card(event_id TEXT PRIMARY KEY REFERENCES evidence(event_id),approval_no TEXT NOT NULL,edition TEXT NOT NULL,language TEXT NOT NULL,section_id TEXT NOT NULL,source_hash TEXT NOT NULL,rule_key TEXT NOT NULL,scope_json TEXT NOT NULL,action TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rule_card_verdict(approval_no TEXT NOT NULL,edition TEXT NOT NULL,language TEXT NOT NULL,decision TEXT NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(approval_no,edition,language));
"""

def connect(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path);db.row_factory=sqlite3.Row;db.executescript(SCHEMA);return db

def rows(db,sql,args=()):return [dict(r) for r in db.execute(sql,args).fetchall()]
