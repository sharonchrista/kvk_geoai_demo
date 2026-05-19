"""
Seed Farmers Table
===================
Reads 10_farmers_data.xls (or farmers_data.xls) and creates a clean
`farmers` table in PostgreSQL with properly named columns.

Run once:
    python scripts/seed_farmers.py
"""

import sys
import os
import logging
from pathlib import Path
from datetime import datetime

import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SYNC_URL = os.getenv(
    "SYNC_DATABASE_URL",
    "postgresql+psycopg2://kvkuser:kvkpass@localhost:5432/kvk_db",
)

# Accept filename as argument or auto-detect
XLS_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if XLS_PATH is None:
    for candidate in [
        Path("src/data/10_farmers_data.xls"),
        Path("src/data/farmers_data.xls"),
    ]:
        if candidate.exists():
            XLS_PATH = candidate
            break

if XLS_PATH is None or not XLS_PATH.exists():
    logger.error("XLS file not found. Usage: python scripts/seed_farmers.py <path_to_file.xls>")
    sys.exit(1)

logger.info("Reading %s …", XLS_PATH)
df_raw = pd.read_excel(XLS_PATH, engine="xlrd", header=None)

# Data rows start at index 3 (0-based), columns 1–10
data = df_raw.iloc[3:13, 1:11].copy()
data.columns = [
    "sr_no",
    "name",
    "phone",
    "transplanted",
    "variety",
    "address",
    "coord_a",
    "coord_b",
    "coord_c",
    "coord_d",
]

# Clean up types
data["phone"] = data["phone"].astype(str).str.strip().str.split(".").str[0]
data["sr_no"] = data["sr_no"].astype(int)
data["variety"] = data["variety"].astype(str).str.strip()
data["name"] = data["name"].astype(str).str.strip()
data["address"] = data["address"].astype(str).str.strip()

# Normalise coord columns (strip to string)
for col in ["coord_a", "coord_b", "coord_c", "coord_d"]:
    data[col] = data[col].astype(str).str.strip()

# Normalise transplanted date
def fmt_date(v):
    if isinstance(v, datetime):
        return v.strftime("%d %b %Y")
    return str(v).strip()

data["transplanted"] = data["transplanted"].apply(fmt_date)

logger.info("Parsed %d farmer rows:", len(data))
for _, row in data.iterrows():
    logger.info("  [%d] %s — phone: %s", row.sr_no, row.name, row.phone)

engine = create_engine(SYNC_URL)

with engine.begin() as conn:
    conn.execute(text("DROP TABLE IF EXISTS farmers CASCADE"))

data.to_sql("farmers", engine, if_exists="replace", index=False)

with engine.begin() as conn:
    conn.execute(text("ALTER TABLE farmers ADD PRIMARY KEY (phone)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_farmers_sr_no ON farmers(sr_no)"))

logger.info("Done. farmers table created with %d rows.", len(data))
logger.info("Primary key: phone")
