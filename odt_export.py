from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
import zipfile


ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
OTT_MIMETYPE = "application/vnd.oasis.opendocument.text-template"

ODF_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _xml_text(value):
    text = "" if value is None else str(value)
    # ODF line breaks inside text content
    return xml_escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<text:line-break/>")


def _replace_placeholders_in_text(text, placeholder_map):
    replacements = {f"{{{{{key}}}}}": _xml_text(value) for key, value in (placeholder_map or {}).items()}
    for token, replacement in replacements.items():
        text = text.replace(token, replacement)
    return text


def _apply_repeat_block(text, block_name, rows):
    start_token = f"{{{{#{block_name}}}}}"
    end_token = f"{{{{/{block_name}}}}}"
    pattern = re.escape(start_token) + r"(.*?)" + re.escape(end_token)
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Wiederholblock {block_name} nicht in Vorlage gefunden.")

    block_inner = match.group(1)
    rendered = []
    for row in (rows or []):
        rendered.append(_replace_placeholders_in_text(block_inner, row))
    return text[:match.start()] + "".join(rendered) + text[match.end():]


def render_odt_from_ott_template(template_path, placeholder_map):
    """Fill an ODT template (.ott) and return BytesIO of a downloadable .odt file."""
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(template_path)

    output = BytesIO()
    with zipfile.ZipFile(template_path, "r") as src, zipfile.ZipFile(output, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)

            if info.filename == "content.xml":
                text = data.decode("utf-8")
                text = _replace_placeholders_in_text(text, placeholder_map)
                data = text.encode("utf-8")

            elif info.filename == "mimetype":
                data = data.replace(OTT_MIMETYPE.encode("utf-8"), ODT_MIMETYPE.encode("utf-8"))

            elif info.filename == "META-INF/manifest.xml":
                data = data.replace(OTT_MIMETYPE.encode("utf-8"), ODT_MIMETYPE.encode("utf-8"))

            clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.comment = info.comment
            clone.extra = info.extra
            clone.internal_attr = info.internal_attr
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.flag_bits = info.flag_bits
            dst.writestr(clone, data)

    output.seek(0)
    return output


def render_odt_from_ott_template_with_repeat_blocks(template_path, placeholder_map, repeat_blocks):
    """Fill an ODT template (.ott) with normal placeholders and repeating blocks."""
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(template_path)

    output = BytesIO()
    with zipfile.ZipFile(template_path, "r") as src, zipfile.ZipFile(output, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)

            if info.filename == "content.xml":
                text = data.decode("utf-8")
                for block_name, rows in (repeat_blocks or {}).items():
                    text = _apply_repeat_block(text, block_name, rows)
                text = _replace_placeholders_in_text(text, placeholder_map)
                data = text.encode("utf-8")

            elif info.filename == "mimetype":
                data = data.replace(OTT_MIMETYPE.encode("utf-8"), ODT_MIMETYPE.encode("utf-8"))

            elif info.filename == "META-INF/manifest.xml":
                data = data.replace(OTT_MIMETYPE.encode("utf-8"), ODT_MIMETYPE.encode("utf-8"))

            clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.comment = info.comment
            clone.extra = info.extra
            clone.internal_attr = info.internal_attr
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.flag_bits = info.flag_bits
            dst.writestr(clone, data)

    output.seek(0)
    return output


def _as_bytes(payload):
    if isinstance(payload, BytesIO):
        return payload.getvalue()
    return bytes(payload)


def _rename_style_names_for_merge(automatic_styles_el, text_body_el, prefix):
    if automatic_styles_el is None:
        return

    style_name_attr = f"{{{ODF_NS['style']}}}name"
    ref_attr_locals = {
        "style-name",
        "parent-style-name",
        "next-style-name",
        "list-style-name",
        "data-style-name",
        "master-page-name",
        "page-layout-name",
    }
    list_ref_locals = {"class-names"}

    rename_map = {}
    for el in automatic_styles_el.iter():
        name = el.attrib.get(style_name_attr)
        if name:
            rename_map[name] = f"{prefix}{name}"

    if not rename_map:
        return

    def _rewrite_attrs(root):
        for el in root.iter():
            for attr_key, attr_value in list(el.attrib.items()):
                local_name = attr_key.split("}", 1)[-1]
                if local_name == "name" and attr_key == style_name_attr and attr_value in rename_map:
                    el.attrib[attr_key] = rename_map[attr_value]
                elif local_name in ref_attr_locals and attr_value in rename_map:
                    el.attrib[attr_key] = rename_map[attr_value]
                elif local_name in list_ref_locals and attr_value:
                    parts = attr_value.split()
                    updated = [rename_map.get(part, part) for part in parts]
                    el.attrib[attr_key] = " ".join(updated)

    _rewrite_attrs(automatic_styles_el)
    if text_body_el is not None:
        _rewrite_attrs(text_body_el)


def merge_odt_documents(primary_odt_bytes, secondary_odt_bytes):
    """
    Merge two rendered ODT documents into one ODT.

    The first document becomes the leading pages (e.g. Grundlagenblatt), the second is appended.
    This implementation merges automatic styles and appends document body content.
    """
    primary_payload = _as_bytes(primary_odt_bytes)
    secondary_payload = _as_bytes(secondary_odt_bytes)

    with zipfile.ZipFile(BytesIO(primary_payload), "r") as z1, zipfile.ZipFile(BytesIO(secondary_payload), "r") as z2:
        primary_infos = z1.infolist()
        primary_entries = {info.filename: z1.read(info.filename) for info in primary_infos}
        secondary_entries = {info.filename: z2.read(info.filename) for info in z2.infolist()}

    if "content.xml" not in primary_entries or "content.xml" not in secondary_entries:
        raise ValueError("ODT-Inhalt (content.xml) konnte nicht gelesen werden.")

    root1 = ET.fromstring(primary_entries["content.xml"])
    root2 = ET.fromstring(secondary_entries["content.xml"])

    auto1 = root1.find(f".//{{{ODF_NS['office']}}}automatic-styles")
    auto2 = root2.find(f".//{{{ODF_NS['office']}}}automatic-styles")
    text1 = root1.find(f".//{{{ODF_NS['office']}}}body/{{{ODF_NS['office']}}}text")
    text2 = root2.find(f".//{{{ODF_NS['office']}}}body/{{{ODF_NS['office']}}}text")
    if text1 is None or text2 is None:
        raise ValueError("ODT-Dokumentstruktur konnte nicht zusammengeführt werden.")

    _rename_style_names_for_merge(auto2, text2, prefix="m2_")

    if auto1 is not None and auto2 is not None:
        for child in list(auto2):
            auto1.append(child)

    # Simple separator between documents; layout/page break is template-controlled afterwards.
    text1.append(ET.Element(f"{{{ODF_NS['text']}}}p"))
    for child in list(text2):
        text1.append(child)

    primary_entries["content.xml"] = ET.tostring(root1, encoding="utf-8", xml_declaration=True)

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as dst:
        for info in primary_infos:
            data = primary_entries[info.filename]
            clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.comment = info.comment
            clone.extra = info.extra
            clone.internal_attr = info.internal_attr
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.flag_bits = info.flag_bits
            dst.writestr(clone, data)

    output.seek(0)
    return output


def convert_odt_bytes_to_pdf(odt_bytes, soffice_cmd="soffice", timeout_seconds=20):
    """Convert ODT bytes to PDF bytes using LibreOffice headless."""
    if isinstance(odt_bytes, BytesIO):
        odt_payload = odt_bytes.getvalue()
    else:
        odt_payload = bytes(odt_bytes)

    if not shutil.which(soffice_cmd):
        raise RuntimeError("LibreOffice (soffice) ist nicht installiert.")

    with tempfile.TemporaryDirectory(prefix="km_odt_pdf_") as tmpdir:
        tmp_path = Path(tmpdir)
        odt_path = tmp_path / "input.odt"
        pdf_path = tmp_path / "input.pdf"
        profile_dir = tmp_path / "lo-profile"
        runtime_dir = tmp_path / "xdg-runtime"
        profile_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        odt_path.write_bytes(odt_payload)

        env = dict(os.environ)
        env["HOME"] = str(tmp_path)
        env["XDG_RUNTIME_DIR"] = str(runtime_dir)

        proc = subprocess.run(
            [
                soffice_cmd,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nolockcheck",
                "--nofirststartwizard",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmp_path),
                str(odt_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
        if pdf_path.exists():
            return BytesIO(pdf_path.read_bytes())

        if proc.returncode != 0 or not pdf_path.exists():
            details = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"PDF-Konvertierung fehlgeschlagen. {details}".strip())
        raise RuntimeError("PDF-Konvertierung fehlgeschlagen.")


# ---------------------------------------------------------------------------
# Dokumente ohne Vorlage bauen
#
# Die vorlagenbasierten Exporte fuellen Platzhalter in einer .ott-Datei. Fuer
# ein Dokument aus mehreren Abschnitten unterschiedlicher und im Vorhinein
# unbekannter Laenge - die vollstaendige Schuelerakte - traegt das nicht: eine
# Vorlage mit festen Bloecken liesse Ueberschriften ohne Inhalt stehen.
# ---------------------------------------------------------------------------

_DOC_NAMESPACES = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"'
)

_DOC_STYLES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles {ns} office:version="1.2">
 <office:styles>
  <style:style style:name="Standard" style:family="paragraph">
   <style:text-properties style:font-name="Liberation Sans" fo:font-size="10pt"/>
  </style:style>
 </office:styles>
 <office:automatic-styles>
  <style:page-layout style:name="pm1">
   <style:page-layout-properties fo:page-width="21cm" fo:page-height="29.7cm"
     style:print-orientation="portrait" fo:margin-top="2cm" fo:margin-bottom="2cm"
     fo:margin-left="2cm" fo:margin-right="2cm"/>
  </style:page-layout>
 </office:automatic-styles>
 <office:master-styles>
  <style:master-page style:name="Standard" style:page-layout-name="pm1"/>
 </office:master-styles>
</office:document-styles>
""".format(ns=_DOC_NAMESPACES)

_DOC_AUTOMATIC_STYLES = """
  <style:style style:name="Titel" style:family="paragraph">
   <style:paragraph-properties fo:margin-bottom="0.2cm" fo:keep-with-next="always"/>
   <style:text-properties fo:font-size="18pt" fo:font-weight="bold"/>
  </style:style>
  <style:style style:name="H1" style:family="paragraph">
   <style:paragraph-properties fo:margin-top="0.7cm" fo:margin-bottom="0.2cm"
     fo:keep-with-next="always" fo:border-bottom="0.06pt solid #808080" fo:padding-bottom="0.1cm"/>
   <style:text-properties fo:font-size="13pt" fo:font-weight="bold"/>
  </style:style>
  <style:style style:name="H2" style:family="paragraph">
   <style:paragraph-properties fo:margin-top="0.4cm" fo:margin-bottom="0.1cm" fo:keep-with-next="always"/>
   <style:text-properties fo:font-size="11pt" fo:font-weight="bold"/>
  </style:style>
  <style:style style:name="Text" style:family="paragraph">
   <style:paragraph-properties fo:margin-bottom="0.15cm"/>
   <style:text-properties fo:font-size="10pt"/>
  </style:style>
  <style:style style:name="Klein" style:family="paragraph">
   <style:paragraph-properties fo:margin-bottom="0.15cm"/>
   <style:text-properties fo:font-size="8.5pt" fo:color="#5f7387"/>
  </style:style>
  <style:style style:name="Seitenumbruch" style:family="paragraph">
   <style:paragraph-properties fo:break-before="page"/>
   <style:text-properties fo:font-size="1pt"/>
  </style:style>
  <style:style style:name="Tab" style:family="table">
   <style:table-properties style:width="17cm" table:align="left" fo:margin-bottom="0.3cm"/>
  </style:style>
  <style:style style:name="TabSpalte" style:family="table-column">
   <style:table-column-properties style:use-optimal-column-width="true"/>
  </style:style>
  <style:style style:name="Zelle" style:family="table-cell">
   <style:table-cell-properties fo:border="0.06pt solid #b0bec5" fo:padding="0.12cm"/>
  </style:style>
  <style:style style:name="ZelleKopf" style:family="table-cell">
   <style:table-cell-properties fo:border="0.06pt solid #b0bec5" fo:padding="0.12cm"
     fo:background-color="#eef4f7"/>
  </style:style>
  <style:style style:name="ZellText" style:family="paragraph">
   <style:text-properties fo:font-size="9.5pt"/>
  </style:style>
  <style:style style:name="ZellKopfText" style:family="paragraph">
   <style:text-properties fo:font-size="9.5pt" fo:font-weight="bold"/>
  </style:style>
"""


def _doc_paragraph(text, style="Text"):
    return f'<text:p text:style-name="{style}">{_xml_text(text)}</text:p>'


def _doc_heading(text, level=1):
    style = {1: "H1", 2: "H2"}.get(level, "H2")
    return (
        f'<text:h text:style-name="{style}" text:outline-level="{level}">'
        f'{_xml_text(text)}</text:h>'
    )


def _doc_cell(value, head=False):
    cell_style = "ZelleKopf" if head else "Zelle"
    text_style = "ZellKopfText" if head else "ZellText"
    return (
        f'<table:table-cell table:style-name="{cell_style}" office:value-type="string">'
        f'<text:p text:style-name="{text_style}">{_xml_text(value)}</text:p>'
        f'</table:table-cell>'
    )


def _doc_table(rows, head=None, name="T"):
    if not rows and not head:
        return ""
    spalten = len(head) if head else max(len(row) for row in rows)
    teile = [f'<table:table table:name="{name}" table:style-name="Tab">']
    teile.append(
        f'<table:table-column table:style-name="TabSpalte" table:number-columns-repeated="{spalten}"/>'
    )
    if head:
        teile.append('<table:table-header-rows><table:table-row>')
        teile.extend(_doc_cell(zelle, head=True) for zelle in head)
        teile.append('</table:table-row></table:table-header-rows>')
    for row in rows:
        teile.append('<table:table-row>')
        # Kurze Zeilen auffuellen, sonst verschiebt sich die Tabelle.
        werte = list(row) + [''] * (spalten - len(row))
        teile.extend(_doc_cell(zelle) for zelle in werte[:spalten])
        teile.append('</table:table-row>')
    teile.append('</table:table>')
    return "".join(teile)


def build_odt_document(blocks):
    """Baut ein ODT aus einer Folge von Bloecken und gibt BytesIO zurueck.

    Unterstuetzte Bloecke, jeweils als dict mit 'type':

        heading    text, level (1 oder 2)
        paragraph  text, optional style ('Text', 'Klein', 'Titel')
        fields     rows als Liste von (Bezeichnung, Wert)
        table      head als Liste, rows als Liste von Listen
        pagebreak  erzwingt eine neue Seite, ohne weitere Angaben
    """
    body = []
    tabellen = 0

    for block in blocks or []:
        art = block.get("type")
        if art == "heading":
            body.append(_doc_heading(block.get("text", ""), block.get("level", 1)))
        elif art == "paragraph":
            body.append(_doc_paragraph(block.get("text", ""), block.get("style", "Text")))
        elif art == "fields":
            tabellen += 1
            zeilen = [
                [bezeichnung, wert] for bezeichnung, wert in (block.get("rows") or [])
            ]
            body.append(_doc_table(zeilen, name=f"F{tabellen}"))
        elif art == "table":
            tabellen += 1
            body.append(_doc_table(
                block.get("rows") or [], head=block.get("head"), name=f"T{tabellen}",
            ))
        elif art == "pagebreak":
            body.append('<text:p text:style-name="Seitenumbruch"/>')
        else:
            raise ValueError(f"Unbekannter Blocktyp: {art!r}")

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<office:document-content {_DOC_NAMESPACES} office:version="1.2">'
        f'<office:automatic-styles>{_DOC_AUTOMATIC_STYLES}</office:automatic-styles>'
        f'<office:body><office:text>{"".join(body)}</office:text></office:body>'
        '</office:document-content>'
    )

    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest'
        ' xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"'
        ' manifest:version="1.2">'
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{ODT_MIMETYPE}"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as dst:
        # mimetype muss der erste Eintrag und unkomprimiert sein, sonst erkennen
        # Leser das Format nicht.
        dst.writestr(
            zipfile.ZipInfo("mimetype"), ODT_MIMETYPE, compress_type=zipfile.ZIP_STORED,
        )
        dst.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
        dst.writestr("styles.xml", _DOC_STYLES_XML, compress_type=zipfile.ZIP_DEFLATED)
        dst.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)

    output.seek(0)
    return output
