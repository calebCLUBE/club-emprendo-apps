import csv
import io
import re
import unicodedata
import zipfile
from xml.etree import ElementTree


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMPORT_ROWS = 20000


def normalized_header(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _unique_headers(raw_headers: list) -> list[str]:
    headers = []
    counts = {}
    for index, raw in enumerate(raw_headers, start=1):
        base = str(raw or "").strip() or f"Column {index}"
        counts[base] = counts.get(base, 0) + 1
        headers.append(base if counts[base] == 1 else f"{base} ({counts[base]})")
    return headers


def _rectangular_dataset(raw_rows: list[list]) -> dict:
    if not raw_rows:
        raise ValueError("The file is empty.")
    width = max((len(row) for row in raw_rows), default=0)
    if not width:
        raise ValueError("The file has no columns.")
    headers = _unique_headers(list(raw_rows[0]) + [""] * (width - len(raw_rows[0])))
    rows = []
    for raw_row in raw_rows[1:]:
        row = [str(value or "").strip() for value in raw_row]
        row.extend([""] * (width - len(row)))
        if any(row):
            rows.append(row[:width])
    if len(rows) > MAX_IMPORT_ROWS:
        raise ValueError(f"The file has more than {MAX_IMPORT_ROWS:,} data rows.")
    return {"headers": headers, "rows": rows}


def _parse_csv(content: bytes) -> dict:
    text = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("The CSV text encoding could not be read.")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return _rectangular_dataset([list(row) for row in csv.reader(io.StringIO(text), dialect)])


def _xlsx_column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", cell_reference or "")
    if not letters:
        return 0
    value = 0
    for char in letters.group(0).upper():
        value = value * 26 + (ord(char) - 64)
    return max(0, value - 1)


def _parse_xlsx(content: bytes) -> dict:
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship_namespace = {
        "r": "http://schemas.openxmlformats.org/package/2006/relationships"
    }
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("The Excel file is not a valid .xlsx workbook.") from exc

    with archive:
        shared_strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", namespace):
                shared_strings.append("".join(node.text or "" for node in item.findall(".//m:t", namespace)))

        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib.get("Id", ""): rel.attrib.get("Target", "")
            for rel in relationships.findall("r:Relationship", relationship_namespace)
        }
        sheet = workbook.find("m:sheets/m:sheet", namespace)
        if sheet is None:
            raise ValueError("The Excel workbook has no worksheets.")
        relationship_id = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id", ""
        )
        target = targets.get(relationship_id, "")
        if not target:
            raise ValueError("The first Excel worksheet could not be found.")
        worksheet_path = target.lstrip("/")
        if not worksheet_path.startswith("xl/"):
            worksheet_path = f"xl/{worksheet_path}"
        worksheet = ElementTree.fromstring(archive.read(worksheet_path))

        raw_rows = []
        for row_node in worksheet.findall(".//m:sheetData/m:row", namespace):
            values = []
            for cell in row_node.findall("m:c", namespace):
                column_index = _xlsx_column_index(cell.attrib.get("r", ""))
                values.extend([""] * max(0, column_index + 1 - len(values)))
                cell_type = cell.attrib.get("t", "")
                value_node = cell.find("m:v", namespace)
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.findall(".//m:t", namespace))
                elif value_node is None:
                    value = ""
                elif cell_type == "s":
                    try:
                        value = shared_strings[int(value_node.text or "0")]
                    except (ValueError, IndexError):
                        value = ""
                elif cell_type == "b":
                    value = "TRUE" if value_node.text == "1" else "FALSE"
                else:
                    value = value_node.text or ""
                values[column_index] = value
            raw_rows.append(values)
    return _rectangular_dataset(raw_rows)


def parse_uploaded_table(uploaded_file) -> dict:
    filename = str(getattr(uploaded_file, "name", "") or "")
    extension = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if extension not in {"csv", "xlsx"}:
        raise ValueError("Use a .csv or .xlsx file.")
    content = uploaded_file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Each file must be 10 MB or smaller.")
    return _parse_csv(content) if extension == "csv" else _parse_xlsx(content)


FIELD_LABELS = [
    ("name", "Nombre"),
    ("email", "Email / Correo"),
    ("id", "Cedula / ID"),
    ("whatsapp", "WhatsApp"),
    ("country", "Pais / Residencia"),
    ("age", "Edad"),
    ("status", "Estatus"),
    ("info", "Info"),
    ("acta", "Acta"),
    ("website", "Website"),
    ("capacitacion", "Capacitacion"),
    ("certificacion", "Certificacion"),
    ("encuesta_inicial", "Encuesta inicial"),
    ("encuesta_final", "Encuesta final"),
    ("plazo_extra", "Plazo extra"),
    ("lanzamiento", "Lanzamiento"),
    ("wm", "W/M"),
    ("we", "W/E"),
]


FIELD_ALIASES = {
    "name": ("nombrecompleto", "fullname", "applicantname", "participantname", "nombresyapellidos", "nombreyapellido", "nombre"),
    "email": ("email", "correo", "correoelectronico", "mail"),
    "id": ("id", "cedula", "documento", "identificacion", "numerodedocumento"),
    "whatsapp": ("whatsapp", "telefono", "phone", "celular", "movil"),
    "country": ("paisdondereside", "paisdondevive", "paisresidencia", "countryresidence", "reside", "pais", "country"),
    "age": ("edad", "age", "agerange", "rangodeedad"),
    "status": ("estatus", "status", "estado"),
    "info": ("info", "informacion"),
    "acta": ("acta", "firmoacta", "contractsigned"),
    "website": ("website", "sitio", "web"),
    "capacitacion": ("capacitacion", "training"),
    "certificacion": ("certificacion", "certificado", "certificate"),
    "encuesta_inicial": ("encuestainicial", "initialsurvey"),
    "encuesta_final": ("encuestafinal", "finalsurvey"),
    "plazo_extra": ("plazoextra",),
    "lanzamiento": ("lanzamiento", "launch"),
    "wm": ("wm", "wmentora"),
    "we": ("we", "wemprendedora"),
}


def suggested_mapping(headers: list[str]) -> dict[str, str]:
    normalized = [(header, normalized_header(header)) for header in headers]
    mapping = {}
    for field_name, _label in FIELD_LABELS:
        aliases = FIELD_ALIASES.get(field_name, ())
        selected = ""
        for header, key in normalized:
            if key in aliases:
                selected = header
                break
        if not selected:
            for header, key in normalized:
                if field_name == "name" and any(
                    token in key for token in ("emprend", "negocio", "empresa", "business", "company")
                ):
                    continue
                if any(alias and alias in key for alias in aliases):
                    selected = header
                    break
        mapping[field_name] = selected
    return mapping
