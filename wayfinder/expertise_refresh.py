"""Absolute-path entry point for hourly offline expertise refresh."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from wayfinder.db import db_manager
from wayfinder.derived.expertise_v2 import ExpertiseRefresh

if __name__=='__main__':
    print(ExpertiseRefresh(db_manager).refresh(),flush=True)
