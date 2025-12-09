from pathlib import Path

from botpy import logging

_log = logging.get_logger()
name_id_ref = []
heros = []
heros_name = []
hero_excel_path = Path(f"{Path(__file__).resolve().parent}/res/hero_name.xlsx")
common_id_path = Path(f"{Path(__file__).resolve().parent}/res/name_id.json")
enable_ai = True
