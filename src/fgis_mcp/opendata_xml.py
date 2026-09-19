"""Streaming XML parser for official FGIS CS OpenData FSNB and FSBC distributions."""

import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO

from .network import SourceError
from .normalize import clean, number

XML_NORM_FAMILIES = {
    "ГЭСН.xml": "ГЭСН",
    "ГЭСНм.xml": "ГЭСНм",
    "ГЭСНр.xml": "ГЭСНр",
    "ГЭСНп.xml": "ГЭСНп",
    "ГЭСНмр.xml": "ГЭСНмр",
}

XML_FSBC_FILES = {
    "ФСБЦ_Мат&Оборуд.xml": "material",
    "ФСБЦ_Маш.xml": "machine",
}

ALL_KNOWN_XML = {**XML_NORM_FAMILIES, **XML_FSBC_FILES}

EXPECTED_FSNB_2022_XML = [
    "ГЭСН.xml",
    "ГЭСНм.xml",
    "ГЭСНр.xml",
    "ГЭСНп.xml",
    "ГЭСНмр.xml",
    "ФСБЦ_Мат&Оборуд.xml",
    "ФСБЦ_Маш.xml",
]


def decode_zip_filename(raw_name: str) -> str:
    """Decode zip filename handling Windows CP866 encoding when UTF-8 flag is absent."""
    try:
        return raw_name.encode("cp437").decode("cp866")
    except Exception:
        return raw_name


def extract_dates(text: str) -> dict[str, str | None]:
    """Extract approval_date, effective_from, and effective_to strictly from explicit legal text.

    Rules:
    - approval_date: date of decree or approving act, e.g. 'от 18.05.2022'.
    - effective_from: ONLY when explicitly stated: 'действует с DD.MM.YYYY',
      'вступает в силу с DD.MM.YYYY', 'вводится в действие с DD.MM.YYYY'.
    - effective_to: ONLY when explicitly stated: 'действует по DD.MM.YYYY',
      'действует до DD.MM.YYYY', 'утратил силу с DD.MM.YYYY'.
    - Never infer effective_from from approval_date!
    """
    approval_date = None
    effective_from = None
    effective_to = None

    if not text:
        return {
            "approval_date": None,
            "effective_from": None,
            "effective_to": None,
        }

    # Match approval date: 'от DD.MM.YYYY'
    app_match = re.search(r"\bот\s+(\d{2}\.\d{2}\.\d{4})\b", text, re.IGNORECASE)
    if not app_match:
        app_match = re.search(
            r"(?:приказ|акта?|письм[оа])\s+.*?от\s+(\d{2}\.\d{2}\.\d{4})", text, re.IGNORECASE
        )
    if app_match:
        approval_date = app_match.group(1)

    # Match effective_from: strictly 'действует с ...' or 'вступает в силу с ...' or 'вводится в действие с ...'
    eff_match = re.search(
        r"(?:действует|вступает\s+в\s+силу|вводится\s+в\s+действие)\s+с\s+(\d{2}\.\d{2}\.\d{4})",
        text,
        re.IGNORECASE,
    )
    if eff_match:
        effective_from = eff_match.group(1)

    # Match effective_to: 'действует (по|до) ...' or 'утратил силу с ...'
    to_match = re.search(
        r"(?:действует\s+(?:по|до)|утратил\s+силу\s+с)\s+(\d{2}\.\d{2}\.\d{4})",
        text,
        re.IGNORECASE,
    )
    if to_match:
        effective_to = to_match.group(1)

    return {
        "approval_date": approval_date,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }


def check_zip_safety(
    archive: zipfile.ZipFile,
    max_uncompressed_bytes: int = 1024 * 1024 * 1024,
    max_files: int = 1000,
) -> None:
    """Validate ZIP archive safety against path traversal and zip bomb attacks."""
    infolist = archive.infolist()
    if len(infolist) > max_files:
        raise SourceError(
            "TOO_MANY_FILES", f"Archive contains {len(infolist)} files, exceeding limit {max_files}"
        )
    total_size = 0
    for info in infolist:
        fname = info.filename
        norm_name = fname.replace("\\", "/")
        if norm_name.startswith("/") or ".." in norm_name.split("/"):
            raise SourceError("SECURITY_ERROR", f"Archive contains unsafe path traversal: {fname}")
        total_size += info.file_size
    if total_size > max_uncompressed_bytes:
        raise SourceError(
            "TOO_LARGE",
            f"Expanded archive size ({total_size} bytes) exceeds limit ({max_uncompressed_bytes} bytes)",
        )


def extract_snapshot_id(filename_or_name: str) -> str:
    """Extract standard snapshot ID (e.g. 20260812) from archive filename."""
    match = re.search(r"data-(\d{8})-structure", filename_or_name)
    if match:
        return match.group(1)
    match_any_date = re.search(r"(?<!\d)(20\d{6})(?!\d)", filename_or_name)
    if match_any_date:
        return match_any_date.group(1)
    match_ru_date = re.search(r"(\d{2})\.(\d{2})\.(20\d{2})", filename_or_name)
    if match_ru_date:
        d, m, y = match_ru_date.groups()
        return f"{y}{m}{d}"
    # Clean fallback alphanumeric identifier
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", filename_or_name).strip("_")
    return cleaned[:32] if cleaned else "unknown_snapshot"


def parse_base_xml_stream(
    stream: io.BufferedIOBase | BinaryIO,
    snapshot_id: str,
    family: str | None = None,
    decree_override: str | None = None,
    effective_from_override: str | None = None,
    approval_date_override: str | None = None,
    xml_filename: str | None = None,
    xml_sha256: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream parse a GOSN/GESN base XML file yielding normalized norm cards.

    Uses xml.etree.ElementTree.iterparse with elem.clear() to guarantee O(1) memory usage.
    """
    context = ET.iterparse(stream, events=("start", "end"))

    section_stack: list[dict[str, str]] = []
    current_decrees: list[str] = []
    base_price_level = ""
    base_type = ""
    base_name = ""
    creation_date = ""
    program_name = ""
    current_name_group = ""

    for event, elem in context:
        tag = elem.tag

        if event == "start":
            if tag == "base":
                base_price_level = clean(elem.attrib.get("PriceLevel") or "")
                base_type = clean(elem.attrib.get("BaseType") or "")
                base_name = clean(elem.attrib.get("BaseName") or "")
                creation_date = clean(elem.attrib.get("CreationDate") or "")
                program_name = clean(elem.attrib.get("ProgramName") or "")
            elif tag == "Decree":
                d_name = clean(elem.attrib.get("Name") or elem.text or "")
                if d_name:
                    current_decrees.append(d_name)
            elif tag == "Section":
                sec_type = clean(elem.attrib.get("Type") or "")
                sec_code = clean(elem.attrib.get("Code") or "")
                sec_name = clean(elem.attrib.get("Name") or "")
                section_stack.append({"type": sec_type, "code": sec_code, "name": sec_name})
            elif tag == "NameGroup":
                current_name_group = clean(elem.attrib.get("BeginName") or "")

        elif event == "end":
            if tag == "Work":
                eff_family = family or base_type or "ГЭСН"
                code = clean(elem.attrib.get("Code") or "")
                end_name = clean(elem.attrib.get("EndName") or "")
                unit = clean(elem.attrib.get("MeasureUnit") or "")

                if current_name_group and end_name:
                    full_name = f"{current_name_group.rstrip(': ')} {end_name}".strip()
                else:
                    full_name = end_name or current_name_group

                # Extract work steps (состав работ)
                work_steps: list[str] = []
                for item in elem.findall(".//Content/Item"):
                    st = clean(item.attrib.get("Text") or item.text or "")
                    if st:
                        work_steps.append(st)

                # Extract resources (сметные ресурсы)
                resources: list[dict[str, Any]] = []
                for r in elem.findall(".//Resources/*"):
                    r_tag = r.tag
                    r_code = clean(r.attrib.get("Code") or "")
                    r_name = clean(r.attrib.get("EndName") or r.attrib.get("Name") or "")
                    r_unit = clean(r.attrib.get("MeasureUnit") or "")
                    r_qty_str = clean(r.attrib.get("Quantity") or "")
                    num_qty = number(r_qty_str)

                    r_dict: dict[str, Any] = {
                        "code": r_code,
                        "name": r_name,
                        "quantity": num_qty if num_qty is not None else r_qty_str,
                        "raw_quantity": r_qty_str,
                        "unit": r_unit,
                        "tag": r_tag,
                    }

                    if r_tag == "AbstractResource":
                        r_dict["type"] = "abstract"
                        r_dict["technology_groups"] = clean(r.attrib.get("TechnologyGroups") or "")
                    elif r_tag == "ServiceResource":
                        r_dict["type"] = "service"
                        r_dict["category"] = clean(r.attrib.get("Category") or "")
                        r_dict["service_type"] = clean(r.attrib.get("Type") or "")
                    else:
                        r_dict["type"] = "basic"

                    resources.append(r_dict)

                # Extract overhead and profit rates (НР и СП)
                nr_sp: list[dict[str, str]] = []
                for nr_elem in elem.findall(".//NrSp/ReasonItem"):
                    nr_sp.append(
                        {
                            "nr": clean(nr_elem.attrib.get("Nr") or ""),
                            "sp": clean(nr_elem.attrib.get("Sp") or ""),
                        }
                    )

                # Extract Massa (масса оборудования для ГЭСНм)
                massa = None
                m_elem = elem.find(".//Massa")
                if m_elem is not None:
                    massa = {
                        "mass": clean(m_elem.attrib.get("Mass") or ""),
                        "mass_name": clean(m_elem.attrib.get("MassName") or "Масса"),
                        "unit": clean(m_elem.attrib.get("MeasureUnit") or ""),
                    }

                # Resolve hierarchy from section stack
                collection_code, collection_name = "", ""
                section_code, section_name = "", ""
                table_code, table_name = "", ""

                for s in section_stack:
                    stype = s["type"]
                    if stype == "Сборник":
                        collection_code = s["code"]
                        collection_name = s["name"]
                    elif stype in ("Раздел", "Отдел"):
                        section_code = s["code"]
                        section_name = s["name"]
                    elif stype == "Таблица":
                        table_code = s["code"]
                        table_name = s["name"]

                decree_text = decree_override or ("; ".join(current_decrees) if current_decrees else "")

                # Invariant: strict legal date extraction
                extracted_dates = extract_dates(decree_text)
                approval_date = extracted_dates["approval_date"] or approval_date_override
                effective_from = extracted_dates["effective_from"] or effective_from_override
                effective_to = extracted_dates["effective_to"]
                pub_date = creation_date or None

                card = {
                    "norm_id": f"{snapshot_id}:{eff_family}:{code}",
                    "code": code,
                    "family": eff_family,
                    "name": full_name,
                    "unit": unit,
                    "work_steps": work_steps,
                    "resources": resources,
                    "nr_sp": nr_sp,
                    "massa": massa,
                    "collection_code": collection_code,
                    "collection_name": collection_name,
                    "section_code": section_code,
                    "section_name": section_name,
                    "table_code": table_code,
                    "table_name": table_name,
                    "snapshot_id": snapshot_id,
                    "base_level": base_price_level,
                    "decree": decree_text,
                    "approval_date": approval_date,
                    "publication_date": pub_date,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "xml_filename": xml_filename or f"{eff_family}.xml",
                    "xml_sha256": xml_sha256,
                    "source": {
                        "dataset_number": "7707082071-fsnb",
                        "snapshot_id": snapshot_id,
                        "xml_file": xml_filename or f"{eff_family}.xml",
                        "xml_filename": xml_filename or f"{eff_family}.xml",
                        "xml_sha256": xml_sha256,
                    },
                    "raw_metadata": {
                        "base_name": base_name,
                        "program_name": program_name,
                        "creation_date": creation_date,
                    },
                    "warnings": [],
                }
                elem.clear()
                yield card

            elif tag == "Section":
                if section_stack:
                    section_stack.pop()
                elem.clear()
            elif tag in ("NameGroup", "Decrees", "ResourcesDirectory", "ResourceCategory"):
                elem.clear()


def parse_fsbc_xml_stream(
    stream: io.BufferedIOBase | BinaryIO,
    snapshot_id: str,
    resource_type: str | None = None,
    effective_from_override: str | None = None,
    approval_date_override: str | None = None,
    xml_filename: str | None = None,
    xml_sha256: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream parse an FSBC catalog XML file yielding normalized base resource items.

    Uses xml.etree.ElementTree.iterparse with elem.clear() to guarantee O(1) memory usage.
    """
    context = ET.iterparse(stream, events=("start", "end"))

    section_stack: list[dict[str, str]] = []
    approving_act_number = ""
    approving_act_date = ""
    detected_category = ""

    for event, elem in context:
        tag = elem.tag

        if event == "start":
            if tag == "ResourceCategory":
                detected_category = clean(elem.attrib.get("Type") or "")
            elif tag == "ApprovingActNumber":
                approving_act_number = clean(elem.text or "")
            elif tag == "ApprovingActDate":
                approving_act_date = clean(elem.text or "")
            elif tag == "Section":
                sec_type = clean(elem.attrib.get("Type") or "")
                sec_code = clean(elem.attrib.get("Code") or "")
                sec_name = clean(elem.attrib.get("Name") or "")
                section_stack.append({"type": sec_type, "code": sec_code, "name": sec_name})

        elif event == "end":
            if tag == "Resource":
                code = clean(elem.attrib.get("Code") or "")
                name = clean(elem.attrib.get("Name") or "")
                unit = clean(elem.attrib.get("MeasureUnit") or "")

                eff_type = resource_type
                if not eff_type:
                    eff_type = "machine" if "Машин" in detected_category else "material"

                # Extract prices
                prices: list[dict[str, Any]] = []
                cost_val: float | None = None
                opt_cost_val: float | None = None

                for p_elem in elem.findall(".//Prices/Price"):
                    p_dict = dict(p_elem.attrib)
                    if "Cost" in p_dict:
                        cost_val = number(p_dict["Cost"])
                        p_dict["cost"] = cost_val
                    if "OptCost" in p_dict:
                        opt_cost_val = number(p_dict["OptCost"])
                        p_dict["opt_cost"] = opt_cost_val
                    if "SalaryMach" in p_dict:
                        p_dict["salary_mach"] = number(p_dict["SalaryMach"])
                    if "LabourMach" in p_dict:
                        p_dict["labour_mach"] = number(p_dict["LabourMach"])
                    if "PriceCostWithoutSalary" in p_dict:
                        p_dict["price_cost_without_salary"] = number(p_dict["PriceCostWithoutSalary"])
                    prices.append(p_dict)

                # Extract expendable materials (for machines)
                materials: list[dict[str, Any]] = []
                for m_elem in elem.findall(".//ExpendableMaterials/Material"):
                    materials.append(dict(m_elem.attrib))

                # Resolve hierarchy from section stack
                book_code, book_name = "", ""
                group_code, group_name = "", ""

                for s in section_stack:
                    stype = s["type"]
                    if stype == "Книга":
                        book_code = s["code"]
                        book_name = s["name"]
                    elif stype in ("Часть", "Раздел", "Группа"):
                        group_code = s["code"]
                        group_name = s["name"]

                app_date = approving_act_date or approval_date_override or None

                entry = {
                    "fsbc_id": f"{snapshot_id}:{code}",
                    "code": code,
                    "name": name,
                    "unit": unit,
                    "resource_type": eff_type,
                    "cost": cost_val,
                    "opt_cost": opt_cost_val,
                    "prices": prices,
                    "expendable_materials": materials,
                    "book_code": book_code,
                    "book_name": book_name,
                    "group_code": group_code,
                    "group_name": group_name,
                    "decree_number": approving_act_number,
                    "decree_date": approving_act_date,
                    "approval_date": app_date,
                    "publication_date": None,
                    "effective_from": effective_from_override,
                    "effective_to": None,
                    "snapshot_id": snapshot_id,
                    "xml_filename": xml_filename
                    or ("ФСБЦ_Маш.xml" if eff_type == "machine" else "ФСБЦ_Мат&Оборуд.xml"),
                    "xml_sha256": xml_sha256,
                    "source": {
                        "dataset_number": "7707082071-fsnb",
                        "snapshot_id": snapshot_id,
                        "resource_type": eff_type,
                        "xml_file": xml_filename
                        or ("ФСБЦ_Маш.xml" if eff_type == "machine" else "ФСБЦ_Мат&Оборуд.xml"),
                        "xml_filename": xml_filename
                        or ("ФСБЦ_Маш.xml" if eff_type == "machine" else "ФСБЦ_Мат&Оборуд.xml"),
                        "xml_sha256": xml_sha256,
                    },
                }
                elem.clear()
                yield entry

            elif tag == "Section":
                if section_stack:
                    section_stack.pop()
                elem.clear()
            elif tag in ("Decrees", "ResourcesDirectory", "ResourceCategory"):
                elem.clear()


class FsnbArchiveReader:
    """Safe, stream-oriented reader for FSNB OpenData ZIP distribution archives."""

    def __init__(self, zip_path: Path | str, snapshot_id: str | None = None):
        self.zip_path = Path(zip_path)
        if not self.zip_path.is_file():
            raise FileNotFoundError(f"FSNB archive file not found: {zip_path}")
        self.snapshot_id = snapshot_id or extract_snapshot_id(self.zip_path.name)
        self.archive_sha256 = self._calculate_archive_sha256()
        self.approval_date: str | None = None
        self.effective_from: str | None = None
        self.effective_to: str | None = None
        self._xml_inventory: dict[str, dict[str, Any]] = {}
        self._parsed_xml: set[str] = set()
        self._failed_xml: set[str] = set()
        self.parser_errors: list[str] = []
        self._inspect()

    def _calculate_archive_sha256(self) -> str:
        """Stream calculate archive SHA-256 in 1 MiB chunks without loading file into RAM."""
        h = hashlib.sha256()
        with self.zip_path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    def _inspect(self) -> None:
        with zipfile.ZipFile(self.zip_path) as archive:
            check_zip_safety(archive)

            # Scan internal paths (with CP866 fallback) for dates and snapshot ID
            for info in archive.infolist():
                decoded_name = decode_zip_filename(info.filename)
                dates = extract_dates(decoded_name)
                if dates["approval_date"] and not self.approval_date:
                    self.approval_date = dates["approval_date"]
                if dates["effective_from"] and not self.effective_from:
                    self.effective_from = dates["effective_from"]
                if dates["effective_to"] and not self.effective_to:
                    self.effective_to = dates["effective_to"]

                if not re.fullmatch(r"\d{8}", self.snapshot_id):
                    cand = extract_snapshot_id(decoded_name)
                    if re.fullmatch(r"\d{8}", cand):
                        self.snapshot_id = cand

            for info in archive.infolist():
                base_name = Path(info.filename).name
                if base_name in ALL_KNOWN_XML:
                    # Calculate SHA-256 of the internal XML for provenance in 1 MiB chunks
                    with archive.open(info.filename) as stream:
                        h = hashlib.sha256()
                        while chunk := stream.read(1024 * 1024):
                            h.update(chunk)
                    self._xml_inventory[base_name] = {
                        "internal_path": info.filename,
                        "file_size": info.file_size,
                        "sha256": h.hexdigest(),
                        "type": "norm" if base_name in XML_NORM_FAMILIES else "fsbc",
                    }

            # If approval date was not in folder paths, inspect decree headers of XML files
            if not self.approval_date:
                for base in ("ФСБЦ_Мат&Оборуд.xml", "ГЭСН.xml"):
                    if base in self._xml_inventory:
                        path_in_zip = self._xml_inventory[base]["internal_path"]
                        with archive.open(path_in_zip) as f:
                            head = f.read(4096).decode("utf-8", errors="ignore")
                            m_date = re.search(r"<ApprovingActDate>(.*?)</ApprovingActDate>", head)
                            if m_date and m_date.group(1).strip():
                                self.approval_date = m_date.group(1).strip()
                                break
                            dates = extract_dates(head)
                            if dates.get("approval_date"):
                                self.approval_date = dates["approval_date"]
                                break

    @property
    def inventory(self) -> dict[str, dict[str, Any]]:
        return self._xml_inventory

    def iter_norms(self, families: list[str] | None = None) -> Iterator[dict[str, Any]]:
        """Yield normalized norm cards across all norm XML files in the archive."""
        with zipfile.ZipFile(self.zip_path) as archive:
            for base_name, family_name in XML_NORM_FAMILIES.items():
                if families and family_name not in families:
                    continue
                if base_name not in self._xml_inventory:
                    continue
                internal_path = self._xml_inventory[base_name]["internal_path"]
                xml_sha = self._xml_inventory[base_name].get("sha256")
                try:
                    with archive.open(internal_path) as stream:
                        yield from parse_base_xml_stream(
                            stream,
                            snapshot_id=self.snapshot_id,
                            family=family_name,
                            effective_from_override=self.effective_from,
                            approval_date_override=self.approval_date,
                            xml_filename=base_name,
                            xml_sha256=xml_sha,
                        )
                    self._parsed_xml.add(base_name)
                except Exception as exc:
                    self._failed_xml.add(base_name)
                    self.parser_errors.append(f"{base_name}: {exc}")
                    raise

    def iter_fsbc(self, resource_types: list[str] | None = None) -> Iterator[dict[str, Any]]:
        """Yield normalized FSBC base resources across catalog XML files in the archive."""
        with zipfile.ZipFile(self.zip_path) as archive:
            for base_name, res_type in XML_FSBC_FILES.items():
                if resource_types and res_type not in resource_types:
                    continue
                if base_name not in self._xml_inventory:
                    continue
                internal_path = self._xml_inventory[base_name]["internal_path"]
                xml_sha = self._xml_inventory[base_name].get("sha256")
                try:
                    with archive.open(internal_path) as stream:
                        yield from parse_fsbc_xml_stream(
                            stream,
                            snapshot_id=self.snapshot_id,
                            resource_type=res_type,
                            effective_from_override=self.effective_from,
                            approval_date_override=self.approval_date,
                            xml_filename=base_name,
                            xml_sha256=xml_sha,
                        )
                    self._parsed_xml.add(base_name)
                except Exception as exc:
                    self._failed_xml.add(base_name)
                    self.parser_errors.append(f"{base_name}: {exc}")
                    raise

    def evaluate_proof(
        self,
        total_norms: int,
        total_fsbc: int,
        duplicate_norm_ids: int = 0,
        duplicate_fsbc_ids: int = 0,
        parser_errors: list[str] | None = None,
        expected_xml: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compute strict completeness proof for this snapshot archive."""
        expected = expected_xml or EXPECTED_FSNB_2022_XML
        found = sorted(list(self._xml_inventory.keys()))
        missing = [f for f in expected if f not in self._xml_inventory]
        errors = list(self.parser_errors) + (parser_errors or [])
        failed = sorted(list(self._failed_xml))

        is_fsnb_standard = set(expected) == set(EXPECTED_FSNB_2022_XML)

        if missing:
            proof = "partial"
            status = "failed" if (total_norms == 0 and total_fsbc == 0) else "partial"
        elif errors or failed or duplicate_norm_ids > 0 or duplicate_fsbc_ids > 0:
            proof = "partial"
            status = "failed"
        elif total_norms > 0 and total_fsbc > 0 and not missing and not failed:
            proof = "complete_verified" if is_fsnb_standard else "complete_unverified"
            status = "complete"
        elif total_norms > 0 or total_fsbc > 0:
            proof = "complete_unverified"
            status = "complete"
        else:
            proof = "unknown"
            status = "failed"

        archive_size = self.zip_path.stat().st_size if self.zip_path.is_file() else 0

        return {
            "archive_sha256": self.archive_sha256,
            "archive_size": archive_size,
            "expected_xml_files": expected,
            "found_xml_files": found,
            "parsed_xml_files": sorted(list(self._parsed_xml)),
            "missing_xml_files": missing,
            "failed_xml_files": failed,
            "total_norms": total_norms,
            "total_fsbc": total_fsbc,
            "duplicate_norm_ids": duplicate_norm_ids,
            "duplicate_fsbc_ids": duplicate_fsbc_ids,
            "parser_errors": errors,
            "status": status,
            "proof": proof,
            "approval_date": self.approval_date,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
        }


def compare_norm_editions(v1: dict[str, Any], v2: dict[str, Any]) -> dict[str, Any]:
    """Compare two editions of the same norm and compute a structured diff."""
    code = v1.get("code") or v2.get("code")
    name1, name2 = v1.get("name", ""), v2.get("name", "")
    unit1, unit2 = v1.get("unit", ""), v2.get("unit", "")
    v1_snap = v1.get("snapshot_id", "v1")
    v2_snap = v2.get("snapshot_id", "v2")

    # Step diff
    steps1 = v1.get("work_steps", [])
    steps2 = v2.get("work_steps", [])
    set_steps1 = set(steps1)
    set_steps2 = set(steps2)
    added_steps = [s for s in steps2 if s not in set_steps1]
    removed_steps = [s for s in steps1 if s not in set_steps2]

    # Resource diff
    res1_by_code = {r["code"]: r for r in v1.get("resources", []) if r.get("code")}
    res2_by_code = {r["code"]: r for r in v2.get("resources", []) if r.get("code")}

    added_resources = []
    removed_resources = []
    modified_resources = []

    for rcode, r2 in res2_by_code.items():
        if rcode not in res1_by_code:
            added_resources.append(r2)
        else:
            r1 = res1_by_code[rcode]
            qty1 = r1.get("quantity")
            qty2 = r2.get("quantity")
            name_changed = r1.get("name") != r2.get("name")
            qty_changed = qty1 != qty2
            if qty_changed or name_changed:
                modified_resources.append(
                    {
                        "code": rcode,
                        "name_v1": r1.get("name"),
                        "name_v2": r2.get("name"),
                        "quantity_v1": qty1,
                        "quantity_v2": qty2,
                        "unit": r2.get("unit") or r1.get("unit"),
                        "qty_changed": qty_changed,
                        "name_changed": name_changed,
                    }
                )

    for rcode, r1 in res1_by_code.items():
        if rcode not in res2_by_code:
            removed_resources.append(r1)

    is_identical = (
        name1 == name2
        and unit1 == unit2
        and not added_steps
        and not removed_steps
        and not added_resources
        and not removed_resources
        and not modified_resources
    )

    return {
        "code": code,
        "v1_snapshot_id": v1_snap,
        "v2_snapshot_id": v2_snap,
        "identical": is_identical,
        "name_changed": name1 != name2,
        "name_v1": name1,
        "name_v2": name2,
        "unit_changed": unit1 != unit2,
        "unit_v1": unit1,
        "unit_v2": unit2,
        "decree_v1": v1.get("decree", ""),
        "decree_v2": v2.get("decree", ""),
        "steps_diff": {
            "added": added_steps,
            "removed": removed_steps,
            "count_v1": len(steps1),
            "count_v2": len(steps2),
        },
        "resources_diff": {
            "added": added_resources,
            "removed": removed_resources,
            "modified": modified_resources,
            "total_resources_v1": len(res1_by_code),
            "total_resources_v2": len(res2_by_code),
        },
    }


def compare_fsnb_editions(
    v1_norms_by_code: dict[str, dict[str, Any]],
    v2_norms_by_code: dict[str, dict[str, Any]],
    v1_snapshot_id: str = "v1",
    v2_snapshot_id: str = "v2",
) -> dict[str, Any]:
    """Calculate aggregate and code-level diff between two FSNB snapshot sets."""
    codes1 = set(v1_norms_by_code.keys())
    codes2 = set(v2_norms_by_code.keys())

    added_codes = sorted(list(codes2 - codes1))
    removed_codes = sorted(list(codes1 - codes2))
    shared_codes = sorted(list(codes1 & codes2))

    modified_codes = []
    identical_codes = []
    sample_diffs = []

    for code in shared_codes:
        diff = compare_norm_editions(v1_norms_by_code[code], v2_norms_by_code[code])
        if diff["identical"]:
            identical_codes.append(code)
        else:
            modified_codes.append(code)
            if len(sample_diffs) < 10:
                sample_diffs.append(diff)

    return {
        "v1_snapshot_id": v1_snapshot_id,
        "v2_snapshot_id": v2_snapshot_id,
        "v1_total_norms": len(codes1),
        "v2_total_norms": len(codes2),
        "added_count": len(added_codes),
        "removed_count": len(removed_codes),
        "modified_count": len(modified_codes),
        "identical_count": len(identical_codes),
        "added_codes_sample": added_codes[:20],
        "removed_codes_sample": removed_codes[:20],
        "modified_codes_sample": modified_codes[:20],
        "sample_detailed_diffs": sample_diffs,
    }
