"""Load stepwise workbook rows into step records.

Reads the xlsx with the stdlib. Does not write the workbook.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# Column letters on the Test Execution sheet.
_COL_TEST_ID = "A"
_COL_OBJECTIVE = "B"
_COL_STEP = "C"
_COL_CONDITION = "D"
_COL_INPUT_VALUE = "E"
_COL_INPUT_UNIT = "F"
_COL_EXPECTED = "G"
_COL_DUT_UNIT = "H"
_COL_MIN = "I"
_COL_MAX = "J"

DEFAULT_WORKBOOK = (
    Path(__file__).resolve().parents[1] / "MV BCM Test Format - Stepwise_APD.xlsx"
)


@dataclass(frozen=True)
class Step:
    """One data row from the stepwise sheet."""

    row: int
    test_id: str
    objective: str
    step: str
    condition: str
    input_value: float | None
    input_unit: str
    expected: float | None
    dut_unit: str
    min_accepted: float | None
    max_accepted: float | None

    @property
    def step_number(self) -> int:
        try:
            return int(float(self.step))
        except (TypeError, ValueError):
            return 10**9


def objective_has_label(objective: str, label: str) -> bool:
    """True when ``label`` is its own token in the objective.

    ``TC1`` does not match ``TC10``. Matching uses the objective column
    only. The condition text names every channel on every row.
    """
    if not label:
        return False
    pattern = rf"(?<![A-Za-z0-9]){re.escape(label)}(?![A-Za-z0-9])"
    return re.search(pattern, objective or "", flags=re.IGNORECASE) is not None


def load_steps(path: Path | str = DEFAULT_WORKBOOK) -> list[Step]:
    """Return every data row that has a test id."""
    workbook = Path(path)
    if not workbook.is_file():
        raise FileNotFoundError(f"Workbook not found: {workbook}")

    with zipfile.ZipFile(workbook) as archive:
        strings = _shared_strings(archive)
        grid = _sheet_grid(archive, strings)

    steps: list[Step] = []
    max_row = max((row for _, row in grid), default=1)
    for row in range(2, max_row + 1):
        test_id = _text(grid.get((_COL_TEST_ID, row)))
        if not test_id:
            continue
        steps.append(
            Step(
                row=row,
                test_id=test_id,
                objective=_text(grid.get((_COL_OBJECTIVE, row))),
                step=_step_text(grid.get((_COL_STEP, row))),
                condition=_text(grid.get((_COL_CONDITION, row))),
                input_value=_number(grid.get((_COL_INPUT_VALUE, row))),
                input_unit=_text(grid.get((_COL_INPUT_UNIT, row))),
                expected=_number(grid.get((_COL_EXPECTED, row))),
                dut_unit=_text(grid.get((_COL_DUT_UNIT, row))),
                min_accepted=_number(grid.get((_COL_MIN, row))),
                max_accepted=_number(grid.get((_COL_MAX, row))),
            )
        )
    return steps


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall("m:si", _NS):
        strings.append("".join(node.text or "" for node in item.iter(f"{_MAIN}t")))
    return strings


def _sheet_grid(archive: zipfile.ZipFile, strings: list[str]) -> dict[tuple[str, int], str]:
    sheet_name = "xl/worksheets/sheet1.xml"
    if sheet_name not in archive.namelist():
        raise ValueError(f"{archive.filename} has no {sheet_name}")
    root = ET.fromstring(archive.read(sheet_name))
    grid: dict[tuple[str, int], str] = {}
    for cell in root.findall("m:sheetData/m:row/m:c", _NS):
        ref = cell.attrib.get("r", "")
        match = re.match(r"([A-Z]+)(\d+)", ref)
        if not match:
            continue
        column, row = match.group(1), int(match.group(2))
        grid[(column, row)] = _cell_text(cell, strings)
    return grid


def _cell_text(cell: ET.Element, strings: list[str]) -> str:
    kind = cell.attrib.get("t")
    value = cell.find("m:v", _NS)
    if kind == "s" and value is not None and value.text:
        index = int(value.text)
        return strings[index] if 0 <= index < len(strings) else ""
    inline = cell.find("m:is", _NS)
    if kind == "inlineStr" and inline is not None:
        return "".join(node.text or "" for node in inline.iter(f"{_MAIN}t"))
    if value is not None and value.text:
        return value.text
    return ""


def _text(raw: str | None) -> str:
    return (raw or "").strip()


def _number(raw: str | None) -> float | None:
    text = _text(raw)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _step_text(raw: str | None) -> str:
    text = _text(raw)
    number = _number(text)
    if number is not None and number.is_integer():
        return str(int(number))
    return text
