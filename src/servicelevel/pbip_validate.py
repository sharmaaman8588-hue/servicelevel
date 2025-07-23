"""Static validation of the PBIP project against the exported CSVs and the metric registry.

Power BI cannot run in CI, so this module reads the TMDL and report JSON as text and checks that

* every TMDL column maps to a header that exists in the CSV its partition loads;
* every ``Table[Column]`` a measure reads, and every column or measure a visual binds, exists;
* the set of measure names equals the registry, and each expression equals the registry's DAX;
* no measure applies AVERAGE or AVERAGEX to a percentage column or measure (an average of
  interval percentages is exactly the mistake this project exists to expose);
* every relationship joins columns that exist;
* ``definition.pbir`` carries the version that matches the report layout present (1.0 for a
  single ``report.json``, 4.0 for the folder-based ``definition/`` layout);
* TMDL descriptions are ``///`` lines, never a ``description:`` property, and every
  ``queryGroup`` an object refers to is defined in the model.

It reports every problem it finds, each naming the table, column or measure involved.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from servicelevel import registry


@dataclass(frozen=True)
class Issue:
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


@dataclass
class _Table:
    name: str
    columns: dict[str, dict[str, str]] = field(default_factory=dict)  # name -> properties
    measures: dict[str, dict[str, str]] = field(default_factory=dict)  # name -> {expr, ...}
    csv_name: str | None = None


_PROPERTY = re.compile(r"^\s*(\w+):\s*(.*)$")
_CSV_REF = re.compile(r'DataFolder\s*&\s*"[\\/]?([^"]+\.csv)"')
_TABLE_COLUMN_REF = re.compile(r"(?:'([^']+)'|(\b[A-Za-z_]\w*))\[([^\]]+)\]")
_MEASURE_REF = re.compile(r"(?<![\w'\]])\[([^\]]+)\]")
_NAME = r"(?:'(?P<q>[^']+)'|(?P<b>[^\s=]+))"


def _unquote(raw: str) -> str:
    raw = raw.strip()
    return raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] == "'" else raw


def parse_tmdl_table(text: str) -> _Table:
    lines = text.splitlines()
    first = re.match(rf"^table\s+{_NAME}\s*$", lines[0]) if lines else None
    if not first:
        raise ValueError("TMDL table file must start with 'table <name>'")
    table = _Table(first.group("q") or first.group("b"))
    i = 1
    while i < len(lines):
        line = lines[i]
        m_col = re.match(rf"^\tcolumn\s+{_NAME}\s*$", line)
        m_meas = re.match(rf"^\tmeasure\s+{_NAME}\s*=\s*(.*)$", line)
        if m_col:
            name = m_col.group("q") or m_col.group("b")
            props: dict[str, str] = {}
            i += 1
            while i < len(lines) and (lines[i].startswith("\t\t") or not lines[i].strip()):
                mp = _PROPERTY.match(lines[i]) if lines[i].strip() else None
                if mp and lines[i].startswith("\t\t") and not lines[i].startswith("\t\t\t"):
                    props[mp.group(1)] = mp.group(2).strip()
                i += 1
            table.columns[name] = props
            continue
        if m_meas:
            name = m_meas.group("q") or m_meas.group("b")
            expr = m_meas.group(3).strip()
            props = {}
            i += 1
            while i < len(lines) and (lines[i].startswith("\t\t") or not lines[i].strip()):
                if not lines[i].strip():
                    i += 1
                    continue
                mp = _PROPERTY.match(lines[i])
                if mp and not lines[i].startswith("\t\t\t"):
                    props[mp.group(1)] = mp.group(2).strip()
                else:
                    expr = f"{expr} {lines[i].strip()}".strip()
                i += 1
            props["expression"] = expr
            table.measures[name] = props
            continue
        m_csv = _CSV_REF.search(line)
        if m_csv:
            table.csv_name = m_csv.group(1)
        i += 1
    return table


def _csv_header(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as fh:
        return next(csv.reader(fh), [])


def _balanced_args(expr: str, start: int) -> str:
    """Text inside the parentheses that open at ``start`` (index of the opening paren)."""
    depth = 0
    for k in range(start, len(expr)):
        if expr[k] == "(":
            depth += 1
        elif expr[k] == ")":
            depth -= 1
            if depth == 0:
                return expr[start + 1 : k]
    return expr[start + 1 :]


def _normalise(expr: str) -> str:
    return re.sub(r"\s+", "", expr)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _visual_fields(report: dict) -> list[tuple[str, str, str, str]]:
    """(page, visual, kind, 'Entity.Property') for every column or measure a visual selects."""
    out = []
    for section in report.get("sections", []):
        page = section.get("displayName", section.get("name", "?"))
        for container in section.get("visualContainers", []):
            cfg = json.loads(container["config"])
            single = cfg.get("singleVisual", {})
            aliases = {
                f["Name"]: f["Entity"] for f in single.get("prototypeQuery", {}).get("From", [])
            }
            for sel in single.get("prototypeQuery", {}).get("Select", []):
                for kind in ("Column", "Measure"):
                    if kind in sel:
                        src = sel[kind]["Expression"]["SourceRef"]["Source"]
                        entity = aliases.get(src, src)
                        out.append(
                            (page, cfg.get("name", "?"), kind, f"{entity}.{sel[kind]['Property']}")
                        )
    return out


PBIR_VERSIONS = {"legacy": "1.0", "folder": "4.0"}


def _check_pbir_version(report_dir: Path, pbir: dict) -> list[Issue]:
    """The pbir version must match the report layout on disk."""
    if (report_dir / "report.json").is_file():
        layout = "legacy"
    elif (report_dir / "definition").is_dir():
        layout = "folder"
    else:
        return [
            Issue("report-missing", f"{report_dir.name} has neither report.json nor definition/")
        ]
    expected = PBIR_VERSIONS[layout]
    found = pbir.get("version")
    if found != expected:
        return [
            Issue(
                "pbir-version",
                f"definition.pbir declares version {found!r} but the {layout} report layout "
                f"needs {expected!r}",
            )
        ]
    return []


def _check_tmdl_syntax(definition_dir: Path) -> list[Issue]:
    """Descriptions must be ``///`` lines; queryGroup references must resolve."""
    issues: list[Issue] = []
    defined: set[str] = set()
    referenced: list[tuple[str, str]] = []
    for path in sorted(definition_dir.rglob("*.tmdl")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(r"^\s+description:", line):
                issues.append(
                    Issue(
                        "tmdl-description",
                        f"{path.name}:{number} uses a 'description:' property; "
                        "write the description as a '///' line above the object",
                    )
                )
            m_def = re.match(r"^queryGroup\s+(.+?)\s*$", line)
            if m_def:
                defined.add(_unquote(m_def.group(1)))
            m_ref = re.match(r"^\s+queryGroup:\s*(.+?)\s*$", line)
            if m_ref:
                referenced.append((path.name, _unquote(m_ref.group(1))))
    for name, group in referenced:
        if group not in defined:
            issues.append(
                Issue("query-group", f"{name} refers to queryGroup '{group}', which is not defined")
            )
    return issues


def validate(pbip_file: Path, data_dir: Path) -> list[Issue]:
    """Return every problem found; an empty list means the project is consistent."""
    issues: list[Issue] = []
    pbip_file = Path(pbip_file)
    root = pbip_file.parent
    project = _read_json(pbip_file)
    report_dir = root / project["artifacts"][0]["report"]["path"]
    pbir = _read_json(report_dir / "definition.pbir")
    issues.extend(_check_pbir_version(report_dir, pbir))
    model_dir = (report_dir / pbir["datasetReference"]["byPath"]["path"]).resolve()
    tables_dir = model_dir / "definition" / "tables"
    if not tables_dir.is_dir():
        return [Issue("model-missing", f"no tables folder at {tables_dir.name}")]

    issues.extend(_check_tmdl_syntax(model_dir / "definition"))

    tables = {}
    for path in sorted(tables_dir.glob("*.tmdl")):
        t = parse_tmdl_table(path.read_text(encoding="utf-8"))
        tables[t.name] = t

    headers: dict[str, list[str]] = {}
    for t in tables.values():
        if not t.csv_name:
            issues.append(Issue("partition", f"table '{t.name}' has no CSV partition"))
            continue
        header = _csv_header(Path(data_dir) / t.csv_name)
        if header is None:
            issues.append(Issue("csv-missing", f"table '{t.name}': {t.csv_name} not found"))
            continue
        headers[t.name] = header
        for col, props in t.columns.items():
            source = props.get("sourceColumn", col)
            if source not in header:
                issues.append(
                    Issue(
                        "column-missing",
                        f"table '{t.name}' column '{col}' maps to '{source}', which is not in "
                        f"{t.csv_name} (columns: {', '.join(header)})",
                    )
                )

    def column_exists(table: str, column: str) -> bool:
        t = tables.get(table)
        if t is None or column not in t.columns:
            return False
        source = t.columns[column].get("sourceColumn", column)
        return table in headers and source in headers[table]

    def is_percentage_column(table: str, column: str) -> bool:
        fmt = (
            tables[table].columns.get(column, {}).get("formatString", "") if table in tables else ""
        )
        return column.endswith("_pct") or "%" in fmt

    # Measures: names, expressions, column references, AVERAGE misuse.
    measures = {m: props for t in tables.values() for m, props in t.measures.items()}
    percent_measures = {
        m for m, props in measures.items() if "%" in props.get("formatString", "")
    } | registry.percentage_measures()
    expected = registry.dax_measures()
    for name in sorted(set(expected) - set(measures)):
        issues.append(Issue("measure-missing", f"registry measure '{name}' is not in the model"))
    for name in sorted(set(measures) - set(expected)):
        issues.append(Issue("measure-unknown", f"model measure '{name}' is not in the registry"))
    for name, props in measures.items():
        expr = props["expression"]
        if name in expected and _normalise(expr) != _normalise(expected[name]):
            issues.append(
                Issue(
                    "measure-drift",
                    f"measure '{name}' is '{expr}', registry has '{expected[name]}'",
                )
            )
        for q, b, col in _TABLE_COLUMN_REF.findall(expr):
            table = q or b
            if table not in tables:
                issues.append(Issue("table-missing", f"measure '{name}' reads table '{table}'"))
            elif not column_exists(table, col):
                issues.append(
                    Issue(
                        "column-missing",
                        f"measure '{name}' reads {table}[{col}], which does not exist",
                    )
                )
        for ref in _MEASURE_REF.findall(re.sub(_TABLE_COLUMN_REF, "", expr)):
            if ref not in measures:
                issues.append(Issue("measure-missing", f"measure '{name}' refers to [{ref}]"))
        for fn in re.finditer(r"\bAVERAGE(?:X|A)?\s*\(", expr):
            args = _balanced_args(expr, fn.end() - 1)
            bad = [
                f"{(q or b)}[{col}]"
                for q, b, col in _TABLE_COLUMN_REF.findall(args)
                if (q or b) in tables and is_percentage_column(q or b, col)
            ]
            bad += [f"[{ref}]" for ref in _MEASURE_REF.findall(args) if ref in percent_measures]
            for ref in bad:
                issues.append(
                    Issue(
                        "average-of-percentage",
                        f"measure '{name}' averages {ref}; use a ratio of sums instead",
                    )
                )

    # Relationships.
    rel_file = model_dir / "definition" / "relationships.tmdl"
    if rel_file.is_file():
        for end in re.findall(r"(?:fromColumn|toColumn):\s*(\S+)", rel_file.read_text("utf-8")):
            table, _, col = end.partition(".")
            if not column_exists(table, col):
                issues.append(Issue("relationship", f"relationship column {end} does not exist"))

    # Visuals.
    report = _read_json(report_dir / "report.json")
    for page, visual, kind, ref in _visual_fields(report):
        table, _, prop = ref.partition(".")
        if kind == "Column" and not column_exists(table, prop):
            issues.append(
                Issue(
                    "visual-column",
                    f"page '{page}' visual '{visual}' binds {table}[{prop}], which does not exist",
                )
            )
        if kind == "Measure" and prop not in measures:
            issues.append(
                Issue(
                    "visual-measure",
                    f"page '{page}' visual '{visual}' binds measure '{prop}', "
                    "which is not in the model",
                )
            )
    return issues
