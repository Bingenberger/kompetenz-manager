from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile
from xml.sax.saxutils import escape as xml_escape

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, func

from db_utils import get_or_404_session
from extensions import db
from odt_export import convert_odt_bytes_to_pdf
from models import (
    Beobachtung,
    ClassTaskLibrary,
    ClassTaskTemplate,
    ClassTaskTemplateCompetency,
    Foerderplan,
    Item,
    Schueler,
    SystemKonfiguration,
    WorkPlan,
    WorkPlanTask,
    WorkPlanTaskAttachment,
    WorkPlanTaskCompetency,
    WorkPlanTaskEvaluation,
)
from student_selection import get_grouped_student_choices_for_user, get_prioritized_students_for_user, get_user_klassenkontext
from school_year import active_school_year_start, observation_period_start
from time_utils import utc_now
from uploads import speichere_upload_bild, speichere_upload_workplan_bild


workplan_bp = Blueprint('workplan', __name__)
WORKPLAN_STATUS_IN_PLANUNG = 'in_planung'
WORKPLAN_STATUS_AKTIV = 'aktiv'
WORKPLAN_STATUS_GESCHLOSSEN = 'geschlossen'
WORKPLAN_ODT_MIMETYPE = "application/vnd.oasis.opendocument.text"
WORKPLAN_PDF_MIMETYPE = "application/pdf"
BOOTSTRAP_ICONS_SPRITE_PATH = Path(__file__).resolve().parents[1] / "static" / "vendor" / "bootstrap-icons" / "bootstrap-icons.svg"
WORKPLAN_OTT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "odt_templates" / "Arbeitsplan.ott"
WORKPLAN_ODT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "odt_templates" / "Arbeitsplan.odt"
OTT_MIMETYPE = "application/vnd.oasis.opendocument.text-template"


def _normalize_workplan_status(raw_value, default=WORKPLAN_STATUS_IN_PLANUNG):
    raw = (raw_value or '').strip().lower()
    mapping = {
        'draft': WORKPLAN_STATUS_IN_PLANUNG,
        'in_planung': WORKPLAN_STATUS_IN_PLANUNG,
        'active': WORKPLAN_STATUS_AKTIV,
        'aktiv': WORKPLAN_STATUS_AKTIV,
        'closed': WORKPLAN_STATUS_GESCHLOSSEN,
        'evaluated': WORKPLAN_STATUS_GESCHLOSSEN,
        'geschlossen': WORKPLAN_STATUS_GESCHLOSSEN,
    }
    return mapping.get(raw, default)


def _icon_to_label(icon_name):
    icon = (icon_name or '').strip()
    if not icon:
        return '-'
    symbol_map = {
        'book': '📘',
        'pencil': '✏️',
        'journal-text': '📝',
        'calculator': '🧮',
        'alphabet': '🔤',
        'chat-dots': '💬',
        'lightbulb': '💡',
        'stars': '⭐',
        'check2-square': '☑️',
        'mortarboard': '🎓',
    }
    symbol = symbol_map.get(icon)
    if symbol:
        return f"{symbol} ({icon})"
    return icon


def _extract_bootstrap_icon_svg(icon_name):
    icon = (icon_name or '').strip()
    if not icon:
        return None
    if not BOOTSTRAP_ICONS_SPRITE_PATH.exists():
        return None

    sprite = BOOTSTRAP_ICONS_SPRITE_PATH.read_text(encoding="utf-8")
    symbol_match = re.search(
        rf"(<symbol\b[^>]*\bid=\"{re.escape(icon)}\"[^>]*>)(.*?)</symbol>",
        sprite,
        flags=re.DOTALL,
    )
    if not symbol_match:
        return None

    open_tag = symbol_match.group(1)
    inner = symbol_match.group(2)
    viewbox_match = re.search(r'viewBox="([^"]+)"', open_tag)
    view_box = viewbox_match.group(1) if viewbox_match else "0 0 16 16"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}">'
        f'{inner}'
        f'</svg>'
    )


def _xml_text(value):
    text = "" if value is None else str(value)
    return xml_escape(text).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<text:line-break/>")


def _replace_odt_placeholders(text, placeholder_map, raw_keys=None):
    raw_keys = raw_keys or set()
    for key, value in (placeholder_map or {}).items():
        token = f"{{{{{key}}}}}"
        replacement = value if key in raw_keys else _xml_text(value)
        text = text.replace(token, replacement)
    return text


def _apply_odt_repeat_block(text, block_name, rows, raw_keys=None):
    start_token = f"{{{{#{block_name}}}}}"
    end_token = f"{{{{/{block_name}}}}}"
    pattern = re.escape(start_token) + r"(.*?)" + re.escape(end_token)
    match = re.search(pattern, text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"Wiederholblock {block_name} nicht in Arbeitsplan-Vorlage gefunden.")

    block_inner = match.group(1)
    rendered = []
    for row in (rows or []):
        rendered.append(_replace_odt_placeholders(block_inner, row, raw_keys=raw_keys))
    return text[:match.start()] + "".join(rendered) + text[match.end():]


def _build_workplan_odt(plan):
    template_path = WORKPLAN_OTT_TEMPLATE_PATH if WORKPLAN_OTT_TEMPLATE_PATH.exists() else WORKPLAN_ODT_TEMPLATE_PATH
    if not template_path.exists():
        raise FileNotFoundError("Keine Arbeitsplan-Vorlage gefunden (Arbeitsplan.ott/.odt).")

    global_map = {
        'Vorname': plan.student.vorname or '',
        'Nachname': plan.student.nachname or '',
        'start_date': plan.period_start.strftime('%d.%m.%Y') if plan.period_start else '',
        'end_date': plan.period_end.strftime('%d.%m.%Y') if plan.period_end else '',
        'Hinweis für das Kind': plan.notes_for_child or '',
    }

    repeat_rows = []
    picture_files = {}
    ordered_tasks = sorted(plan.tasks, key=lambda t: (t.sort_order, t.created_at or utc_now()))
    for idx, task in enumerate(ordered_tasks, start=1):
        icon_svg = _extract_bootstrap_icon_svg(task.icon_name)
        icon_markup = _icon_to_label(task.icon_name)
        if icon_svg:
            picture_name = f"icon_{idx}_{(task.icon_name or 'icon').replace('-', '_')}.svg"
            picture_path = f"Pictures/{picture_name}"
            picture_files[picture_path] = icon_svg.encode("utf-8")
            icon_markup = (
                f"<draw:frame draw:name=\"Icon{idx}\" text:anchor-type=\"as-char\" svg:width=\"0.55cm\" svg:height=\"0.55cm\">"
                f"<draw:image xlink:href=\"{picture_path}\" xlink:type=\"simple\" xlink:show=\"embed\" xlink:actuate=\"onLoad\"/>"
                "</draw:frame>"
            )
        repeat_rows.append({
            'ICON': icon_markup,
            'TITEL': task.title or '',
            'LERNBEREICH': task.learning_area or '',
            'Aufgabenbeschreibung': task.instructions or '',
            'Material': task.materials or '',
            'Lernziel_Kinder': task.child_goal or '',
        })

    if not repeat_rows:
        repeat_rows.append({
            'ICON': '',
            'TITEL': '',
            'LERNBEREICH': '',
            'Aufgabenbeschreibung': '',
            'Material': '',
            'Lernziel_Kinder': '',
        })

    buffer = BytesIO()
    with zipfile.ZipFile(template_path, "r") as src, zipfile.ZipFile(buffer, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)

            if info.filename == "content.xml":
                content = data.decode("utf-8")
                content = _apply_odt_repeat_block(content, 'AUFGABE', repeat_rows, raw_keys={'ICON'})
                content = _replace_odt_placeholders(content, global_map)
                data = content.encode("utf-8")

            elif info.filename == "mimetype":
                data = data.replace(OTT_MIMETYPE.encode("utf-8"), WORKPLAN_ODT_MIMETYPE.encode("utf-8"))
                clone_mime = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                clone_mime.compress_type = zipfile.ZIP_STORED
                clone_mime.comment = info.comment
                clone_mime.extra = info.extra
                clone_mime.internal_attr = info.internal_attr
                clone_mime.external_attr = info.external_attr
                clone_mime.create_system = info.create_system
                clone_mime.flag_bits = info.flag_bits
                dst.writestr(clone_mime, data)
                continue

            elif info.filename == "META-INF/manifest.xml":
                manifest = data.decode("utf-8").replace(OTT_MIMETYPE, WORKPLAN_ODT_MIMETYPE)
                for picture_path in picture_files.keys():
                    if picture_path not in manifest:
                        manifest = manifest.replace(
                            "</manifest:manifest>",
                            f'  <manifest:file-entry manifest:media-type="image/svg+xml" manifest:full-path="{picture_path}"/>\n</manifest:manifest>'
                        )
                data = manifest.encode("utf-8")

            clone = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            clone.compress_type = info.compress_type
            clone.comment = info.comment
            clone.extra = info.extra
            clone.internal_attr = info.internal_attr
            clone.external_attr = info.external_attr
            clone.create_system = info.create_system
            clone.flag_bits = info.flag_bits
            dst.writestr(clone, data)

        for picture_path, payload in picture_files.items():
            dst.writestr(picture_path, payload)

    buffer.seek(0)
    return buffer


def _merge_pdf_buffers(pdf_buffers):
    normalized = []
    for payload in (pdf_buffers or []):
        if isinstance(payload, BytesIO):
            normalized.append(payload.getvalue())
        else:
            normalized.append(bytes(payload))

    if not normalized:
        raise ValueError("Keine PDF-Dateien zum Zusammenführen vorhanden.")
    if len(normalized) == 1:
        single = BytesIO(normalized[0])
        single.seek(0)
        return single

    with tempfile.TemporaryDirectory(prefix="km_workplan_pdf_merge_") as tmpdir:
        tmp_path = Path(tmpdir)
        input_paths = []
        for idx, payload in enumerate(normalized, start=1):
            p = tmp_path / f"in_{idx:03d}.pdf"
            p.write_bytes(payload)
            input_paths.append(p)

        output_path = tmp_path / "merged.pdf"
        proc = subprocess.run(
            ["pdfunite", *[str(p) for p in input_paths], str(output_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not output_path.exists():
            raise RuntimeError("PDF-Zusammenführung mit pdfunite fehlgeschlagen.")

        merged = BytesIO(output_path.read_bytes())
        merged.seek(0)
        return merged


def _is_admin(user):
    return bool(user and getattr(user, 'username', None) == 'admin')


def _safe_next_url(candidate, fallback_url):
    value = (candidate or '').strip()
    if value.startswith('/') and not value.startswith('//'):
        return value
    return fallback_url


def _wants_json_response():
    if (request.args.get('format') or '').strip().lower() == 'json':
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    if request.is_json:
        return True
    return False


def _response(payload=None, status=200, redirect_to=None, flash_message=None):
    if _wants_json_response():
        if payload is None:
            return ('', status)
        return jsonify(payload), status

    if flash_message:
        flash(flash_message)
    return redirect(redirect_to or request.referrer or url_for('workplan.workplan_list_page'))


def _teacher_can_access_student(user, student):
    if not user or not student:
        return False
    if _is_admin(user) or not student.is_active:
        return True

    kontext = get_user_klassenkontext(user)
    klassenleitung = kontext.get('klassenleitung')
    fachklassen = kontext.get('fachklassen') or set()
    if not student.klasse:
        return False
    return student.klasse == klassenleitung or student.klasse in fachklassen


def _workplan_query_for_user(user):
    query = WorkPlan.query
    if _is_admin(user):
        return query
    accessible_student_ids = [
        student.id
        for student in get_prioritized_students_for_user(user, include_archived=True)
        if _teacher_can_access_student(user, student)
    ]
    if not accessible_student_ids:
        return query.filter(WorkPlan.id.is_(None))
    return query.filter(WorkPlan.student_id.in_(accessible_student_ids))


def _accessible_classes_for_user(user):
    if _is_admin(user):
        rows = db.session.query(Schueler.klasse).filter(Schueler.klasse.isnot(None)).distinct().all()
        return sorted({(row[0] or '').strip() for row in rows if (row[0] or '').strip()}, key=lambda x: x.lower())

    kontext = get_user_klassenkontext(user)
    classes = set()
    if kontext.get('klassenleitung'):
        classes.add(kontext.get('klassenleitung'))
    classes.update(kontext.get('fachklassen') or set())
    return sorted({(c or '').strip() for c in classes if (c or '').strip()}, key=lambda x: x.lower())


def _can_manage_library(library):
    if _is_admin(current_user):
        return True
    return bool(library and library.created_by_user_id == current_user.id)


def _ensure_template_view_access_or_404(template_id):
    template = db.session.get(ClassTaskTemplate, template_id)
    if not template or not template.library:
        abort(404)

    if _is_admin(current_user):
        return template

    accessible = set(_accessible_classes_for_user(current_user))
    if template.library.class_name not in accessible:
        abort(404)
    return template


def _ensure_student_access_or_403(student_id):
    student = get_or_404_session(Schueler, int(student_id))
    if not _teacher_can_access_student(current_user, student):
        abort(403)
    return student


def _ensure_plan_access_or_404(plan_id):
    plan = db.session.get(WorkPlan, plan_id)
    if not plan:
        abort(404)

    if _is_admin(current_user):
        return plan

    if not _teacher_can_access_student(current_user, plan.student):
        abort(404)

    return plan


def _ensure_task_in_plan_or_404(plan, task_id):
    task = db.session.get(WorkPlanTask, task_id)
    if not task or task.work_plan_id != plan.id:
        abort(404)
    return task


def _parse_date(value, fallback=None):
    raw = (value or '').strip()
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except ValueError:
        return fallback


def _get_workplan_window_weeks():
    config = SystemKonfiguration.query.order_by(SystemKonfiguration.id.asc()).first()
    weeks = getattr(config, 'workplan_suggestions_weeks', None)
    try:
        weeks_int = int(weeks) if weeks is not None else 12
    except (TypeError, ValueError):
        weeks_int = 12
    return min(max(weeks_int, 1), 52)


def _eval_to_beobachtung_wert(value):
    raw = (value or '').strip().lower()
    mapping = {
        'good': 3,
        'partial': 2,
        'bad': 1,
        '++': 4,
        '+': 3,
        'check': 3,
        'o': 2,
        '-': 1,
        '4': 4,
        '3': 3,
        '2': 2,
        '1': 1,
    }
    return mapping.get(raw)


def _serialize_item(item):
    if not item:
        return None
    return {
        'id': item.id,
        'text': item.text,
        'bereich': item.bereich,
        'bogen': item.bogen.titel if item.bogen else '',
    }


def _serialize_task(task):
    competency_items = [
        _serialize_item(link.item)
        for link in sorted(task.competency_links, key=lambda x: x.item_id)
        if link.item
    ]
    attachments = [
        {
            'id': attachment.id,
            'filePath': attachment.file_path,
            'fileUrl': url_for('system.media_workplan_attachment', attachment_id=attachment.id),
            'caption': attachment.caption,
            'createdAt': attachment.created_at.isoformat() if attachment.created_at else None,
        }
        for attachment in sorted(task.attachments, key=lambda x: x.created_at or utc_now())
    ]
    evaluation = None
    if task.evaluation:
        evaluation = {
            'id': task.evaluation.id,
            'rating': task.evaluation.rating,
            'comment': task.evaluation.comment,
            'evaluatedAt': task.evaluation.evaluated_at.isoformat() if task.evaluation.evaluated_at else None,
            'reProposal': bool(task.evaluation.re_proposal),
        }

    return {
        'id': task.id,
        'title': task.title,
        'learningArea': task.learning_area,
        'iconName': task.icon_name,
        'instructions': task.instructions,
        'materials': task.materials,
        'childGoal': task.child_goal,
        'sourceType': task.source_type,
        'copiedFromTaskId': task.copied_from_task_id,
        'sortOrder': task.sort_order,
        'competencyLinks': competency_items,
        'attachments': attachments,
        'evaluation': evaluation,
    }


def _serialize_plan(plan):
    sorted_tasks = sorted(plan.tasks, key=lambda t: (t.sort_order, t.created_at or utc_now()))
    return {
        'id': plan.id,
        'studentId': plan.student_id,
        'createdByUserId': plan.created_by_user_id,
        'periodStart': plan.period_start.isoformat() if plan.period_start else None,
        'periodEnd': plan.period_end.isoformat() if plan.period_end else None,
        'status': plan.status,
        'notesForChild': plan.notes_for_child,
        'notesForTeacher': plan.notes_for_teacher,
        'createdAt': plan.created_at.isoformat() if plan.created_at else None,
        'updatedAt': plan.updated_at.isoformat() if plan.updated_at else None,
        'tasks': [_serialize_task(task) for task in sorted_tasks],
    }


def _latest_o_minus_competencies(student_id, weeks):
    stichtag = observation_period_start(utc_now() - timedelta(weeks=weeks))
    latest_rows = (
        db.session.query(
            Beobachtung.item_id.label('item_id'),
            func.max(Beobachtung.datum).label('latest_datum'),
        )
        .filter(
            Beobachtung.schueler_id == student_id,
            Beobachtung.datum >= stichtag,
            Beobachtung.item_id.isnot(None),
        )
        .group_by(Beobachtung.item_id)
        .all()
    )

    o_minus_items = []
    for row in latest_rows:
        latest_obs = (
            Beobachtung.query
            .filter(
                Beobachtung.schueler_id == student_id,
                Beobachtung.item_id == row.item_id,
                Beobachtung.datum == row.latest_datum,
            )
            .order_by(Beobachtung.id.desc())
            .first()
        )
        if latest_obs and latest_obs.wert in (1, 2):
            item = db.session.get(Item, row.item_id)
            if item:
                o_minus_items.append({
                    'item': _serialize_item(item),
                    'latestWert': latest_obs.wert,
                    'latestDatum': latest_obs.datum.isoformat() if latest_obs.datum else None,
                    'latestKommentar': latest_obs.kommentar,
                })

    o_minus_items.sort(key=lambda x: ((x['item']['bogen'] or '').lower(), (x['item']['bereich'] or '').lower(), (x['item']['text'] or '').lower()))
    return o_minus_items


def _active_foerderplan_goals(student_id):
    plan = (
        Foerderplan.query
        .filter(
            Foerderplan.schueler_id == student_id,
            Foerderplan.status == 'aktiv',
        )
        .order_by(Foerderplan.datum_erstellung.desc())
        .first()
    )
    if not plan:
        return []

    goals = []
    for inhalt in plan.inhalte:
        goals.append({
            'planId': plan.id,
            'ziel': inhalt.foerderziel,
            'massnahmen': inhalt.massnahmen,
            'sollZustand': inhalt.soll_zustand,
        })
    return goals


def _weak_competencies(student_id, weeks):
    stichtag = observation_period_start(utc_now() - timedelta(weeks=weeks))
    rows = (
        db.session.query(
            Beobachtung.item_id.label('item_id'),
            func.avg(Beobachtung.wert).label('avg_wert'),
            func.count(Beobachtung.id).label('anzahl'),
        )
        .filter(
            Beobachtung.schueler_id == student_id,
            Beobachtung.datum >= stichtag,
            Beobachtung.item_id.isnot(None),
            Beobachtung.wert.isnot(None),
        )
        .group_by(Beobachtung.item_id)
        .having(func.avg(Beobachtung.wert) <= 1.4)
        .all()
    )

    result = []
    for row in rows:
        item = db.session.get(Item, row.item_id)
        if not item:
            continue
        result.append({
            'item': _serialize_item(item),
            'avgWert': round(float(row.avg_wert), 2),
            'anzahl': int(row.anzahl or 0),
        })

    result.sort(key=lambda x: ((x['item']['bogen'] or '').lower(), (x['item']['bereich'] or '').lower(), (x['item']['text'] or '').lower()))
    return result


def _reproposal_competencies(student_id):
    latest_plan = (
        _workplan_query_for_user(current_user)
        .filter(
            WorkPlan.student_id == student_id,
            WorkPlan.status.in_([WORKPLAN_STATUS_GESCHLOSSEN, 'closed', 'evaluated']),
        )
        .order_by(WorkPlan.updated_at.desc())
        .first()
    )
    if not latest_plan:
        return []

    item_map = {}
    for task in latest_plan.tasks:
        if not task.evaluation or task.evaluation.rating == 'good':
            continue
        for link in task.competency_links:
            if link.item_id and link.item_id not in item_map:
                item_map[link.item_id] = link.item

    serialized = [_serialize_item(item) for item in item_map.values() if item]
    serialized.sort(key=lambda x: ((x['bogen'] or '').lower(), (x['bereich'] or '').lower(), (x['text'] or '').lower()))
    return serialized


def _library_templates_for_student(student):
    if not student or not student.klasse:
        return []

    templates = (
        ClassTaskTemplate.query
        .join(ClassTaskLibrary, ClassTaskTemplate.library_id == ClassTaskLibrary.id)
        .filter(ClassTaskLibrary.class_name == student.klasse)
        .order_by(
            func.coalesce(ClassTaskTemplate.learning_area, 'zzz').asc(),
            ClassTaskTemplate.updated_at.desc(),
            ClassTaskTemplate.title.asc(),
        )
        .limit(20)
        .all()
    )

    result = []
    for template in templates:
        result.append({
            'id': template.id,
            'title': template.title,
            'learningArea': template.learning_area,
            'instructions': template.instructions,
            'competencyLinks': [
                _serialize_item(link.item)
                for link in template.competency_links
                if link.item
            ],
        })
    return result


def _compute_suggestions(student):
    weeks = _get_workplan_window_weeks()
    return {
        'windowWeeks': weeks,
        'weakCompetencies': _weak_competencies(student.id, weeks),
        'oMinusCompetencies': _latest_o_minus_competencies(student.id, weeks),
        'activePlanGoals': _active_foerderplan_goals(student.id),
        'reProposalCompetencies': _reproposal_competencies(student.id),
        'libraryMatches': _library_templates_for_student(student),
    }


@workplan_bp.route('/api/work-plan-suggestions')
@login_required
def api_work_plan_suggestions():
    student_id = (request.args.get('studentId') or '').strip()
    if not student_id:
        return jsonify({'error': 'studentId erforderlich'}), 400

    try:
        student = _ensure_student_access_or_403(int(student_id))
    except ValueError:
        return jsonify({'error': 'ungueltige studentId'}), 400

    return _response(_compute_suggestions(student))


@workplan_bp.route('/api/work-plans')
@login_required
def api_work_plans_list():
    student_id = (request.args.get('studentId') or '').strip()
    query = _workplan_query_for_user(current_user)
    if student_id:
        try:
            student = _ensure_student_access_or_403(int(student_id))
        except ValueError:
            return jsonify({'error': 'ungueltige studentId'}), 400
        query = query.filter(WorkPlan.student_id == student.id)

    plans = query.order_by(WorkPlan.created_at.desc()).all()
    return _response({'plans': [_serialize_plan(plan) for plan in plans]})


@workplan_bp.route('/api/work-plans', methods=['POST'])
@login_required
def api_work_plans_create():
    student_id = (request.form.get('studentId') or '').strip()
    if not student_id:
        return jsonify({'error': 'studentId erforderlich'}), 400

    try:
        student = _ensure_student_access_or_403(int(student_id))
    except ValueError:
        return jsonify({'error': 'ungueltige studentId'}), 400

    start = _parse_date(request.form.get('periodStart'), fallback=utc_now().date())
    end = _parse_date(request.form.get('periodEnd'), fallback=start + timedelta(days=7))
    plan = WorkPlan(
        student_id=student.id,
        created_by_user_id=current_user.id,
        period_start=start,
        period_end=end,
        status=WORKPLAN_STATUS_IN_PLANUNG,
        notes_for_child=(request.form.get('notesForChild') or '').strip() or None,
        notes_for_teacher=(request.form.get('notesForTeacher') or '').strip() or None,
    )
    db.session.add(plan)
    db.session.commit()

    return _response(
        {'workPlan': _serialize_plan(plan)},
        status=201,
        redirect_to=url_for('workplan.workplan_edit_page', plan_id=plan.id),
        flash_message='Arbeitsplan erstellt.',
    )


@workplan_bp.route('/api/work-plans/<string:plan_id>', methods=['PUT', 'POST'])
@login_required
def api_work_plans_update(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)

    student_id = (request.form.get('studentId') or '').strip()
    if student_id:
        try:
            student = _ensure_student_access_or_403(int(student_id))
        except ValueError:
            return jsonify({'error': 'ungueltige studentId'}), 400
        plan.student_id = student.id

    start = _parse_date(request.form.get('periodStart'), fallback=plan.period_start)
    end = _parse_date(request.form.get('periodEnd'), fallback=plan.period_end)
    if start and end and start > end:
        return jsonify({'error': 'periodStart darf nicht nach periodEnd liegen'}), 400

    plan.period_start = start
    plan.period_end = end

    if 'status' in request.form:
        plan.status = _normalize_workplan_status(request.form.get('status'), default=plan.status)

    if 'notesForChild' in request.form:
        plan.notes_for_child = (request.form.get('notesForChild') or '').strip() or None
    if 'notesForTeacher' in request.form:
        plan.notes_for_teacher = (request.form.get('notesForTeacher') or '').strip() or None

    redirect_to = _safe_next_url(
        request.form.get('redirectTo'),
        url_for('workplan.workplan_edit_page', plan_id=plan.id),
    )
    db.session.commit()
    return _response(
        {'workPlan': _serialize_plan(plan)},
        redirect_to=redirect_to,
        flash_message='Arbeitsplan gespeichert.',
    )


@workplan_bp.route('/api/work-plans/<string:plan_id>', methods=['DELETE'])
@login_required
def api_work_plans_delete(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task_ids = [task.id for task in plan.tasks]
    if task_ids:
        (
            WorkPlanTask.query
            .filter(WorkPlanTask.copied_from_task_id.in_(task_ids))
            .update({'copied_from_task_id': None}, synchronize_session=False)
        )
    db.session.delete(plan)
    db.session.commit()
    return _response(status=204, flash_message='Arbeitsplan gelöscht.')


@workplan_bp.route('/arbeitsplan/<string:plan_id>/delete', methods=['POST'])
@login_required
def workplan_delete_page(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    student_id = plan.student_id
    tab = (request.form.get('tab') or '').strip()
    view = (request.form.get('view') or '').strip()
    klasse_von = (request.form.get('klasse_von') or '').strip()
    klasse_bis = (request.form.get('klasse_bis') or '').strip()

    task_ids = [task.id for task in plan.tasks]
    if task_ids:
        (
            WorkPlanTask.query
            .filter(WorkPlanTask.copied_from_task_id.in_(task_ids))
            .update({'copied_from_task_id': None}, synchronize_session=False)
        )

    db.session.delete(plan)
    db.session.commit()
    flash('Arbeitsplan gelöscht.')
    return redirect(url_for(
        'workplan.workplan_list_page',
        tab=tab or None,
        view=view or None,
        schueler_id=student_id,
        klasse_von=klasse_von or None,
        klasse_bis=klasse_bis or None,
    ))


@workplan_bp.route('/api/work-plans/<string:plan_id>/copy-basics', methods=['POST'])
@login_required
def api_work_plan_copy_basics(plan_id):
    source_plan = _ensure_plan_access_or_404(plan_id)

    target_student_ids_raw = request.form.getlist('targetStudentIds')
    if not target_student_ids_raw:
        return jsonify({'error': 'targetStudentIds erforderlich'}), 400

    parsed_target_ids = []
    for raw in target_student_ids_raw:
        try:
            sid = int((raw or '').strip())
        except ValueError:
            continue
        if sid not in parsed_target_ids and sid != source_plan.student_id:
            parsed_target_ids.append(sid)

    if not parsed_target_ids:
        return jsonify({'error': 'keine gültigen Zielkinder'}), 400

    updated_plan_ids = []
    for sid in parsed_target_ids:
        target_student = _ensure_student_access_or_403(sid)
        target_plan = (
            _workplan_query_for_user(current_user)
            .filter(
                WorkPlan.student_id == target_student.id,
                WorkPlan.status.in_([WORKPLAN_STATUS_IN_PLANUNG, WORKPLAN_STATUS_AKTIV, 'draft', 'active']),
            )
            .order_by(WorkPlan.created_at.desc())
            .first()
        )
        if not target_plan:
            target_plan = WorkPlan(
                student_id=target_student.id,
                created_by_user_id=current_user.id,
                period_start=source_plan.period_start,
                period_end=source_plan.period_end,
                status=WORKPLAN_STATUS_IN_PLANUNG,
                notes_for_child=source_plan.notes_for_child,
                notes_for_teacher=source_plan.notes_for_teacher,
            )
            db.session.add(target_plan)
            db.session.flush()
        else:
            target_plan.period_start = source_plan.period_start
            target_plan.period_end = source_plan.period_end
            target_plan.notes_for_child = source_plan.notes_for_child
            target_plan.notes_for_teacher = source_plan.notes_for_teacher
        updated_plan_ids.append(target_plan.id)

    db.session.commit()
    response_payload = {'targetWorkPlanIds': updated_plan_ids}
    if len(updated_plan_ids) == 1:
        response_payload['targetWorkPlanId'] = updated_plan_ids[0]
    return _response(
        response_payload,
        redirect_to=url_for('workplan.workplan_edit_page', plan_id=source_plan.id),
        flash_message=f'Grunddaten für {len(updated_plan_ids)} Kind(er) übernommen.',
    )


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks', methods=['POST'])
@login_required
def api_work_plan_add_task(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)

    title = (request.form.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title erforderlich'}), 400

    source_type = (request.form.get('sourceType') or 'manual').strip().lower()
    if source_type not in {'manual', 'library', 'copied'}:
        source_type = 'manual'

    sort_order_raw = (request.form.get('sortOrder') or '').strip()
    try:
        sort_order = int(sort_order_raw) if sort_order_raw else (len(plan.tasks) + 1)
    except ValueError:
        sort_order = len(plan.tasks) + 1

    task = WorkPlanTask(
        work_plan_id=plan.id,
        title=title,
        learning_area=(request.form.get('learningArea') or '').strip() or None,
        icon_name=(request.form.get('iconName') or '').strip() or None,
        instructions=(request.form.get('instructions') or '').strip() or None,
        materials=(request.form.get('materials') or '').strip() or None,
        child_goal=(request.form.get('childGoal') or '').strip() or None,
        source_type=source_type,
        copied_from_task_id=(request.form.get('copiedFromTaskId') or '').strip() or None,
        sort_order=sort_order,
    )
    db.session.add(task)
    db.session.flush()

    competency_ids = request.form.getlist('competencyIds')
    for comp_id in competency_ids:
        try:
            item_id = int(comp_id)
        except (TypeError, ValueError):
            continue
        if db.session.get(Item, item_id):
            db.session.add(WorkPlanTaskCompetency(task_id=task.id, item_id=item_id))

    db.session.commit()
    return _response({'task': _serialize_task(task)}, status=201, flash_message='Aufgabe hinzugefügt.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>', methods=['PUT', 'POST'])
@login_required
def api_work_plan_update_task(plan_id, task_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)

    if 'title' in request.form:
        title = (request.form.get('title') or '').strip()
        if not title:
            return jsonify({'error': 'title darf nicht leer sein'}), 400
        task.title = title

    if 'instructions' in request.form:
        task.instructions = (request.form.get('instructions') or '').strip() or None

    if 'learningArea' in request.form:
        task.learning_area = (request.form.get('learningArea') or '').strip() or None

    if 'iconName' in request.form:
        task.icon_name = (request.form.get('iconName') or '').strip() or None

    if 'materials' in request.form:
        task.materials = (request.form.get('materials') or '').strip() or None

    if 'childGoal' in request.form:
        task.child_goal = (request.form.get('childGoal') or '').strip() or None

    if 'sortOrder' in request.form:
        try:
            task.sort_order = int(request.form.get('sortOrder'))
        except (TypeError, ValueError):
            pass

    if 'sourceType' in request.form:
        source_type = (request.form.get('sourceType') or '').strip().lower()
        if source_type in {'manual', 'library', 'copied'}:
            task.source_type = source_type

    if 'competencyIds' in request.form or 'competencyId' in request.form:
        WorkPlanTaskCompetency.query.filter_by(task_id=task.id).delete()
        selected_ids = []
        if 'competencyId' in request.form:
            selected_ids.append(request.form.get('competencyId'))
        else:
            selected_ids.extend(request.form.getlist('competencyIds'))
        for comp_id in selected_ids[:1]:
            try:
                item_id = int(comp_id)
            except (TypeError, ValueError):
                continue
            if db.session.get(Item, item_id):
                db.session.add(WorkPlanTaskCompetency(task_id=task.id, item_id=item_id))

    db.session.commit()
    return _response({'task': _serialize_task(task)}, flash_message='Aufgabe gespeichert.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/delete', methods=['POST'])
@login_required
def api_work_plan_delete_task(plan_id, task_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)
    db.session.delete(task)
    db.session.commit()
    return _response({'ok': True}, flash_message='Aufgabe gelöscht.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/move', methods=['POST'])
@login_required
def api_work_plan_move_task(plan_id, task_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)

    direction = (request.form.get('direction') or '').strip().lower()
    if direction not in {'up', 'down'}:
        return jsonify({'error': 'direction muss up oder down sein'}), 400

    ordered = sorted(plan.tasks, key=lambda t: (t.sort_order, t.created_at or utc_now()))
    try:
        idx = next(i for i, t in enumerate(ordered) if t.id == task.id)
    except StopIteration:
        return jsonify({'error': 'task nicht gefunden'}), 404

    if direction == 'up' and idx > 0:
        other = ordered[idx - 1]
    elif direction == 'down' and idx < len(ordered) - 1:
        other = ordered[idx + 1]
    else:
        return _response({'ok': True, 'moved': False})

    task.sort_order, other.sort_order = other.sort_order, task.sort_order
    db.session.commit()
    return _response({'ok': True, 'moved': True}, flash_message='Reihenfolge aktualisiert.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/copy', methods=['POST'])
@login_required
def api_work_plan_copy_task(plan_id, task_id):
    source_plan = _ensure_plan_access_or_404(plan_id)
    source_task = _ensure_task_in_plan_or_404(source_plan, task_id)

    target_plan_id = (request.form.get('targetWorkPlanId') or '').strip()
    target_student_ids_raw = request.form.getlist('targetStudentIds')
    if not target_student_ids_raw:
        single_target = (request.form.get('targetStudentId') or '').strip()
        if single_target:
            target_student_ids_raw = [single_target]

    target_plan = None
    if target_plan_id:
        target_plan = _ensure_plan_access_or_404(target_plan_id)
    elif target_student_ids_raw:
        parsed_target_ids = []
        for raw in target_student_ids_raw:
            try:
                sid = int((raw or '').strip())
            except ValueError:
                continue
            if sid not in parsed_target_ids:
                parsed_target_ids.append(sid)
        if not parsed_target_ids:
            return jsonify({'error': 'ungueltige targetStudentIds'}), 400

        copied_tasks = []
        created_plan_ids = []
        for sid in parsed_target_ids:
            target_student = _ensure_student_access_or_403(sid)
            target_plan = (
                _workplan_query_for_user(current_user)
                .filter(
                    WorkPlan.student_id == target_student.id,
                    WorkPlan.status.in_([WORKPLAN_STATUS_IN_PLANUNG, WORKPLAN_STATUS_AKTIV, 'draft', 'active']),
                )
                .order_by(WorkPlan.created_at.desc())
                .first()
            )
            if not target_plan:
                start = utc_now().date()
                target_plan = WorkPlan(
                    student_id=target_student.id,
                    created_by_user_id=current_user.id,
                    period_start=start,
                    period_end=start + timedelta(days=7),
                    status=WORKPLAN_STATUS_IN_PLANUNG,
                )
                db.session.add(target_plan)
                db.session.flush()

            new_task = WorkPlanTask(
                work_plan_id=target_plan.id,
                title=source_task.title,
                learning_area=source_task.learning_area,
                icon_name=source_task.icon_name,
                instructions=source_task.instructions,
                materials=source_task.materials,
                child_goal=source_task.child_goal,
                source_type='copied',
                copied_from_task_id=source_task.id,
                sort_order=len(target_plan.tasks) + 1,
            )
            db.session.add(new_task)
            db.session.flush()
            for link in source_task.competency_links:
                db.session.add(WorkPlanTaskCompetency(task_id=new_task.id, item_id=link.item_id))
            copied_tasks.append(_serialize_task(new_task))
            created_plan_ids.append(target_plan.id)

        db.session.commit()
        response_payload = {'targetWorkPlanIds': created_plan_ids, 'tasks': copied_tasks}
        if len(created_plan_ids) == 1:
            response_payload['targetWorkPlanId'] = created_plan_ids[0]
        return _response(
            response_payload,
            redirect_to=url_for('workplan.workplan_edit_page', plan_id=source_plan.id),
            flash_message=f'Aufgabe für {len(created_plan_ids)} Kind(er) übernommen.',
        )
    else:
        return jsonify({'error': 'targetStudentId(s) oder targetWorkPlanId erforderlich'}), 400

    new_task = WorkPlanTask(
        work_plan_id=target_plan.id,
        title=source_task.title,
        learning_area=source_task.learning_area,
        icon_name=source_task.icon_name,
        instructions=source_task.instructions,
        materials=source_task.materials,
        child_goal=source_task.child_goal,
        source_type='copied',
        copied_from_task_id=source_task.id,
        sort_order=len(target_plan.tasks) + 1,
    )
    db.session.add(new_task)
    db.session.flush()

    for link in source_task.competency_links:
        db.session.add(WorkPlanTaskCompetency(task_id=new_task.id, item_id=link.item_id))

    db.session.commit()
    return _response(
        {'targetWorkPlanId': target_plan.id, 'task': _serialize_task(new_task)},
        redirect_to=url_for('workplan.workplan_edit_page', plan_id=source_plan.id),
        flash_message='Aufgabe kopiert.',
    )


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/attachments', methods=['POST'])
@login_required
def api_work_plan_add_attachment(plan_id, task_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)

    file_storage = request.files.get('photo') or request.files.get('file')
    rel_path = speichere_upload_workplan_bild(file_storage)
    if not rel_path:
        return jsonify({'error': 'Upload fehlgeschlagen (nur jpg/png/webp, max 5MB).'}), 400

    attachment = WorkPlanTaskAttachment(
        task_id=task.id,
        file_path=rel_path,
        caption=(request.form.get('caption') or '').strip() or None,
    )
    db.session.add(attachment)
    db.session.commit()
    return _response({'attachment': {
        'id': attachment.id,
        'filePath': attachment.file_path,
        'fileUrl': url_for('system.media_workplan_attachment', attachment_id=attachment.id),
        'caption': attachment.caption,
    }}, flash_message='Foto hinzugefügt.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/attachments/<string:attachment_id>', methods=['DELETE', 'POST'])
@login_required
def api_work_plan_delete_attachment(plan_id, task_id, attachment_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)

    attachment = db.session.get(WorkPlanTaskAttachment, attachment_id)
    if not attachment or attachment.task_id != task.id:
        abort(404)

    db.session.delete(attachment)
    db.session.commit()
    return _response({'ok': True}, flash_message='Foto entfernt.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/tasks/<string:task_id>/evaluate', methods=['POST'])
@login_required
def api_work_plan_evaluate_task(plan_id, task_id):
    plan = _ensure_plan_access_or_404(plan_id)
    task = _ensure_task_in_plan_or_404(plan, task_id)

    rating = (request.form.get('rating') or '').strip().lower()
    if rating not in {'good', 'partial', 'bad'}:
        return jsonify({'error': 'rating muss good/partial/bad sein'}), 400

    evaluation = task.evaluation or WorkPlanTaskEvaluation(task_id=task.id)
    evaluation.rating = rating
    evaluation.comment = (request.form.get('comment') or '').strip() or None
    evaluation.evaluated_at = utc_now()
    evaluation.re_proposal = rating != 'good'

    db.session.add(evaluation)

    foto = request.files.get('foto')
    filename = speichere_upload_bild(foto)
    if filename:
        db.session.add(WorkPlanTaskAttachment(
            task_id=task.id,
            file_path=filename,
            caption=(request.form.get('foto_caption') or '').strip() or 'Evaluation',
        ))

    # Sobald alle Aufgaben bewertet sind, gilt der Plan als geschlossen.
    all_evaluated = all(t.evaluation is not None for t in plan.tasks) if plan.tasks else False
    if all_evaluated:
        plan.status = WORKPLAN_STATUS_GESCHLOSSEN
    db.session.commit()

    return _response({'evaluation': {
        'id': evaluation.id,
        'rating': evaluation.rating,
        'comment': evaluation.comment,
        'reProposal': evaluation.re_proposal,
    }}, flash_message='Evaluation gespeichert.')


@workplan_bp.route('/api/work-plans/<string:plan_id>/evaluate', methods=['POST'])
@login_required
def api_work_plan_evaluate_bulk(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    updated = 0
    observation_created = 0

    for task in plan.tasks:
        rating = (request.form.get(f'rating_{task.id}') or '').strip().lower()
        evaluation = task.evaluation
        if rating in {'good', 'partial', 'bad'}:
            evaluation = task.evaluation or WorkPlanTaskEvaluation(task_id=task.id)
            evaluation.rating = rating
            evaluation.comment = (request.form.get(f'comment_{task.id}') or '').strip() or None
            evaluation.evaluated_at = utc_now()
            evaluation.re_proposal = rating != 'good'
            db.session.add(evaluation)
            updated += 1

        foto = request.files.get(f'foto_{task.id}')
        filename = speichere_upload_bild(foto)
        if filename:
            db.session.add(WorkPlanTaskAttachment(
                task_id=task.id,
                file_path=filename,
                caption='Evaluation',
            ))

        competency_raw = (request.form.get(f'competency_{task.id}') or '').strip()
        if competency_raw:
            try:
                competency_id = int(competency_raw)
            except ValueError:
                competency_id = None

            if competency_id and any(link.item_id == competency_id for link in task.competency_links):
                obs_rating_raw = (request.form.get(f'obs_rating_{task.id}') or '').strip()
                obs_wert = _eval_to_beobachtung_wert(obs_rating_raw)
                if obs_wert is None and evaluation:
                    obs_wert = _eval_to_beobachtung_wert(evaluation.rating)

                if obs_wert is not None:
                    obs_date = _parse_date(request.form.get(f'obs_date_{task.id}'), fallback=utc_now().date())
                    obs_note = (request.form.get(f'obs_note_{task.id}') or '').strip() or (evaluation.comment if evaluation else None)
                    db.session.add(Beobachtung(
                        schueler_id=plan.student_id,
                        item_id=competency_id,
                        wert=obs_wert,
                        kommentar=obs_note,
                        foto_pfad=filename,
                        anlass='Arbeitsplan-Evaluation',
                        datum=datetime.combine(obs_date, datetime.min.time()),
                    ))
                    observation_created += 1

    if plan.tasks and all(t.evaluation is not None for t in plan.tasks):
        plan.status = WORKPLAN_STATUS_GESCHLOSSEN

    db.session.commit()
    return _response(
        {'updated': updated, 'status': plan.status, 'observationCreated': observation_created},
        flash_message=f'Evaluation gespeichert ({updated} Bewertung(en), {observation_created} Dokumentationseintrag/-einträge).',
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/mark-exported', methods=['POST'])
@login_required
def workplan_mark_exported(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    if plan.status != WORKPLAN_STATUS_GESCHLOSSEN:
        plan.status = WORKPLAN_STATUS_AKTIV
        db.session.commit()
    flash('Arbeitsplan auf "aktiv" gesetzt (Export markiert).')
    return redirect(url_for('workplan.workplan_edit_page', plan_id=plan.id))


@workplan_bp.route('/api/observations/from-evaluation', methods=['POST'])
@login_required
def api_observation_from_evaluation():
    task_id = (request.form.get('taskId') or '').strip()
    competency_id = (request.form.get('competencyId') or '').strip()
    rating_raw = (request.form.get('rating') or '').strip()

    task = db.session.get(WorkPlanTask, task_id)
    if not task:
        return jsonify({'error': 'task nicht gefunden'}), 404

    plan = _ensure_plan_access_or_404(task.work_plan_id)
    if task.work_plan_id != plan.id:
        return jsonify({'error': 'ungueltige task/workplan Kombination'}), 400

    try:
        item_id = int(competency_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'ungueltige competencyId'}), 400

    if not any(link.item_id == item_id for link in task.competency_links):
        return jsonify({'error': 'Kompetenz ist nicht mit der Aufgabe verknuepft'}), 400

    wert = _eval_to_beobachtung_wert(rating_raw)
    if wert is None:
        evaluation = task.evaluation.rating if task.evaluation else ''
        wert = _eval_to_beobachtung_wert(evaluation)
    if wert is None:
        return jsonify({'error': 'rating fehlt/ungueltig'}), 400

    date_value = _parse_date(request.form.get('date'), fallback=utc_now().date())
    note = (request.form.get('note') or '').strip() or (task.evaluation.comment if task.evaluation else None)
    foto = request.files.get('foto')
    filename = speichere_upload_bild(foto)

    observation = Beobachtung(
        schueler_id=plan.student_id,
        item_id=item_id,
        wert=wert,
        kommentar=note,
        foto_pfad=filename,
        anlass='Arbeitsplan-Evaluation',
        datum=datetime.combine(date_value, datetime.min.time()),
    )
    db.session.add(observation)
    db.session.commit()

    return _response({'observationId': observation.id}, flash_message='Beobachtungseintrag erstellt.')


@workplan_bp.route('/api/work-plan-library/templates', methods=['POST'])
@login_required
def api_work_plan_library_create_template():
    student_id = (request.form.get('studentId') or '').strip()
    if not student_id:
        return jsonify({'error': 'studentId erforderlich'}), 400

    try:
        student = _ensure_student_access_or_403(int(student_id))
    except ValueError:
        return jsonify({'error': 'ungueltige studentId'}), 400

    class_name = student.klasse
    if not class_name:
        return jsonify({'error': 'Kind hat keine Klasse'}), 400

    title = (request.form.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title erforderlich'}), 400

    library = (
        ClassTaskLibrary.query
        .filter(
            ClassTaskLibrary.class_name == class_name,
            ClassTaskLibrary.created_by_user_id == current_user.id,
        )
        .first()
    )
    if not library:
        library = ClassTaskLibrary(
            class_name=class_name,
            created_by_user_id=current_user.id,
            name=f'Klassenbibliothek {class_name}',
        )
        db.session.add(library)
        db.session.flush()

    template = ClassTaskTemplate(
        library_id=library.id,
        title=title,
        learning_area=(request.form.get('learningArea') or '').strip() or None,
        icon_name=(request.form.get('iconName') or '').strip() or None,
        instructions=(request.form.get('instructions') or '').strip() or None,
        materials=(request.form.get('materials') or '').strip() or None,
        diff_hints=(request.form.get('diffHints') or '').strip() or None,
    )
    db.session.add(template)
    db.session.flush()

    selected_competencies = []
    if (request.form.get('competencyId') or '').strip():
        selected_competencies.append(request.form.get('competencyId'))
    selected_competencies.extend(request.form.getlist('competencyIds'))
    for comp_id in selected_competencies[:1]:
        try:
            item_id = int(comp_id)
        except (TypeError, ValueError):
            continue
        if db.session.get(Item, item_id):
            db.session.add(ClassTaskTemplateCompetency(template_id=template.id, item_id=item_id))

    db.session.commit()
    return _response({'templateId': template.id}, flash_message='Vorlage gespeichert.')


@workplan_bp.route('/api/work-plan-library/templates/<string:template_id>/to-task', methods=['POST'])
@login_required
def api_work_plan_library_to_task(template_id):
    template = db.session.get(ClassTaskTemplate, template_id)
    if not template:
        return jsonify({'error': 'template nicht gefunden'}), 404

    plan_id = (request.form.get('workPlanId') or '').strip()
    plan = _ensure_plan_access_or_404(plan_id)

    task = WorkPlanTask(
        work_plan_id=plan.id,
        title=template.title,
        learning_area=template.learning_area,
        icon_name=template.icon_name,
        instructions=template.instructions,
        materials=template.materials,
        child_goal=template.diff_hints,
        source_type='library',
        sort_order=len(plan.tasks) + 1,
    )
    db.session.add(task)
    db.session.flush()

    for link in template.competency_links:
        db.session.add(WorkPlanTaskCompetency(task_id=task.id, item_id=link.item_id))

    db.session.commit()
    return _response({'task': _serialize_task(task)}, status=201, flash_message='Vorlage als Aufgabe übernommen.')


@workplan_bp.route('/api/work-plan-library/templates/to-task', methods=['POST'])
@login_required
def api_work_plan_library_to_task_by_form():
    template_id = (request.form.get('templateId') or '').strip()
    if not template_id:
        return jsonify({'error': 'templateId erforderlich'}), 400
    return api_work_plan_library_to_task(template_id)


@workplan_bp.route('/arbeitsplan/bibliothek')
@login_required
def workplan_library_page():
    accessible_classes = _accessible_classes_for_user(current_user)
    requested_class = (request.args.get('klasse') or '').strip()
    selected_class = requested_class if requested_class in accessible_classes else (accessible_classes[0] if accessible_classes else '')

    libraries_query = ClassTaskLibrary.query
    if selected_class:
        libraries_query = libraries_query.filter(ClassTaskLibrary.class_name == selected_class)
    elif not _is_admin(current_user):
        libraries_query = libraries_query.filter(ClassTaskLibrary.class_name.in_(accessible_classes))

    libraries = libraries_query.order_by(ClassTaskLibrary.name.asc()).all()
    templates = []
    for library in libraries:
        templates.extend(library.templates)

    if not _is_admin(current_user):
        templates = [t for t in templates if t.library and t.library.class_name in set(accessible_classes)]

    templates.sort(
        key=lambda t: (
            (t.learning_area or 'zzz').lower(),
            -(t.updated_at.timestamp() if t.updated_at else 0),
        )
    )

    grouped_templates = {}
    manageable_template_ids = set()
    for template in templates:
        key = template.learning_area or 'Ohne Lernbereich'
        grouped_templates.setdefault(key, []).append(template)
        if _can_manage_library(template.library):
            manageable_template_ids.add(template.id)

    items = Item.query.order_by(Item.bogen_id.asc(), Item.bereich.asc(), Item.text.asc()).all()

    return render_template(
        'workplan_library.html',
        accessible_classes=accessible_classes,
        selected_class=selected_class,
        grouped_templates=grouped_templates,
        manageable_template_ids=manageable_template_ids,
        all_items=items,
    )


@workplan_bp.route('/arbeitsplan/bibliothek/template/neu', methods=['POST'])
@login_required
def workplan_library_create_template_page():
    class_name = (request.form.get('class_name') or '').strip()
    accessible_classes = _accessible_classes_for_user(current_user)
    if class_name not in accessible_classes and not _is_admin(current_user):
        abort(403)

    title = (request.form.get('title') or '').strip()
    if not title:
        flash('Titel ist erforderlich.')
        return redirect(url_for('workplan.workplan_library_page', klasse=class_name))

    library = (
        ClassTaskLibrary.query
        .filter(
            ClassTaskLibrary.class_name == class_name,
            ClassTaskLibrary.created_by_user_id == current_user.id,
        )
        .first()
    )
    if not library:
        library = ClassTaskLibrary(
            class_name=class_name,
            created_by_user_id=current_user.id,
            name=f'Klassenbibliothek {class_name}',
        )
        db.session.add(library)
        db.session.flush()

    template = ClassTaskTemplate(
        library_id=library.id,
        title=title,
        learning_area=(request.form.get('learning_area') or '').strip() or None,
        icon_name=(request.form.get('icon_name') or '').strip() or None,
        instructions=(request.form.get('instructions') or '').strip() or None,
        materials=(request.form.get('materials') or '').strip() or None,
        diff_hints=(request.form.get('child_goal') or '').strip() or None,
    )
    db.session.add(template)
    db.session.flush()

    comp_id = (request.form.get('competency_id') or '').strip()
    if comp_id:
        try:
            item_id = int(comp_id)
        except ValueError:
            item_id = None
        if item_id and db.session.get(Item, item_id):
            db.session.add(ClassTaskTemplateCompetency(template_id=template.id, item_id=item_id))

    db.session.commit()
    flash('Bibliotheksaufgabe angelegt.')
    return redirect(url_for('workplan.workplan_library_page', klasse=class_name))


@workplan_bp.route('/arbeitsplan/bibliothek/template/<string:template_id>/update', methods=['POST'])
@login_required
def workplan_library_update_template_page(template_id):
    template = _ensure_template_view_access_or_404(template_id)
    if not _can_manage_library(template.library):
        abort(403)

    template.title = (request.form.get('title') or '').strip() or template.title
    template.learning_area = (request.form.get('learning_area') or '').strip() or None
    template.icon_name = (request.form.get('icon_name') or '').strip() or None
    template.instructions = (request.form.get('instructions') or '').strip() or None
    template.materials = (request.form.get('materials') or '').strip() or None
    template.diff_hints = (request.form.get('child_goal') or '').strip() or None

    ClassTaskTemplateCompetency.query.filter_by(template_id=template.id).delete()
    comp_id = (request.form.get('competency_id') or '').strip()
    if comp_id:
        try:
            item_id = int(comp_id)
        except ValueError:
            item_id = None
        if item_id and db.session.get(Item, item_id):
            db.session.add(ClassTaskTemplateCompetency(template_id=template.id, item_id=item_id))

    db.session.commit()
    flash('Bibliotheksaufgabe gespeichert.')
    return redirect(url_for('workplan.workplan_library_page', klasse=template.library.class_name))


@workplan_bp.route('/arbeitsplan/bibliothek/template/<string:template_id>/delete', methods=['POST'])
@login_required
def workplan_library_delete_template_page(template_id):
    template = _ensure_template_view_access_or_404(template_id)
    if not _can_manage_library(template.library):
        abort(403)

    class_name = template.library.class_name
    db.session.delete(template)
    db.session.commit()
    flash('Bibliotheksaufgabe gelöscht.')
    return redirect(url_for('workplan.workplan_library_page', klasse=class_name))


@workplan_bp.route('/arbeitsplaene')
@login_required
def workplan_list_page():
    students = [s for s in get_prioritized_students_for_user(current_user, include_archived=True) if _teacher_can_access_student(current_user, s)]
    student_groups = [
        {
            'key': group['key'],
            'label': group['label'],
            'students': [s for s in group['students'] if _teacher_can_access_student(current_user, s)],
        }
        for group in get_grouped_student_choices_for_user(current_user, include_archived=True)
    ]
    student_groups = [group for group in student_groups if group['students']]
    if not student_groups:
        student_groups = [{'key': 'all', 'label': 'Alle Kinder', 'students': []}]

    class_to_students = {}
    for student in students:
        if not student.is_active:
            continue
        key = (student.klasse or '').strip()
        class_to_students.setdefault(key, []).append(student)

    kontext = get_user_klassenkontext(current_user)
    own_class = kontext.get('klassenleitung')
    fach_classes = sorted(kontext.get('fachklassen') or set(), key=lambda x: x.lower())

    tab_definitions = []
    if own_class:
        tab_definitions.append({
            'id': 'own',
            'label': f'Meine Klasse ({own_class})',
            'students': class_to_students.get(own_class, []),
            'kind': 'class',
            'class_name': own_class,
        })
    for fach_class in fach_classes:
        tab_definitions.append({
            'id': f'fach-{fach_class}',
            'label': f'Fachunterricht ({fach_class})',
            'students': class_to_students.get(fach_class, []),
            'kind': 'class',
            'class_name': fach_class,
        })
    archived_students = [student for student in students if not student.is_active]
    if archived_students:
        tab_definitions.append({
            'id': 'archive',
            'label': f'Schülerarchiv ({len(archived_students)})',
            'students': archived_students,
            'kind': 'archive',
            'class_name': None,
        })
    tab_definitions.append({
        'id': 'dropdown',
        'label': 'Auswahl (Dropdown)',
        'students': students,
        'kind': 'dropdown',
        'class_name': None,
    })

    assigned_class_tab_ids = {tab['id'] for tab in tab_definitions if tab['kind'] == 'class'}
    has_assigned_class_tabs = bool(assigned_class_tab_ids)
    if not has_assigned_class_tabs:
        tab_definitions = [tab for tab in tab_definitions if tab['id'] in {'archive', 'dropdown'}]

    selected_s_id = (request.args.get('schueler_id') or '').strip()
    requested_tab = (request.args.get('tab') or '').strip()
    requested_view = (request.args.get('view') or '').strip().lower()
    class_date_from_raw = (request.args.get('klasse_von') or '').strip()
    class_date_to_raw = (request.args.get('klasse_bis') or '').strip()
    class_date_from = _parse_date(class_date_from_raw)
    class_date_to = _parse_date(class_date_to_raw)
    if class_date_from and class_date_to and class_date_from > class_date_to:
        class_date_from, class_date_to = class_date_to, class_date_from

    valid_tab_ids = {tab['id'] for tab in tab_definitions}
    active_tab = requested_tab if requested_tab in valid_tab_ids else (tab_definitions[0]['id'] if tab_definitions else 'dropdown')

    selected_student = None
    if selected_s_id:
        try:
            selected_student = _ensure_student_access_or_403(int(selected_s_id))
        except ValueError:
            selected_s_id = ''

    if selected_student and not requested_tab:
        if not selected_student.is_active and 'archive' in valid_tab_ids:
            active_tab = 'archive'
        elif own_class and selected_student.klasse == own_class and 'own' in valid_tab_ids:
            active_tab = 'own'
        elif selected_student.klasse and f'fach-{selected_student.klasse}' in valid_tab_ids:
            active_tab = f'fach-{selected_student.klasse}'
        else:
            active_tab = 'dropdown'

    active_tab_def = next((tab for tab in tab_definitions if tab['id'] == active_tab), None)
    class_view_mode = 'class' if requested_view == 'class' else 'student'
    if not active_tab_def or active_tab_def['kind'] != 'class':
        class_view_mode = 'student'

    if active_tab_def and active_tab_def['kind'] in {'class', 'archive'}:
        allowed_ids = {s.id for s in active_tab_def['students']}
        if selected_student and selected_student.id not in allowed_ids:
            selected_student = None
            selected_s_id = ''

    if not selected_student and active_tab_def and active_tab_def['students']:
        selected_student = active_tab_def['students'][0]
        selected_s_id = str(selected_student.id)
    elif not selected_student and students:
        selected_student = students[0]
        selected_s_id = str(selected_student.id)

    requested_scope = (request.args.get('scope') or '').strip().lower()
    plan_scope = requested_scope if requested_scope in {'current', 'archive'} else (
        'archive' if selected_student and not selected_student.is_active else 'current'
    )
    school_year_start = active_school_year_start()

    plans = []
    if selected_student:
        plan_query = (
            _workplan_query_for_user(current_user)
            .filter(WorkPlan.student_id == selected_student.id)
        )
        if school_year_start:
            if plan_scope == 'archive':
                plan_query = plan_query.filter(WorkPlan.period_end < school_year_start)
            else:
                plan_query = plan_query.filter(WorkPlan.period_end >= school_year_start)
        plans = plan_query.order_by(WorkPlan.created_at.desc()).all()

    class_plans = []
    if active_tab_def and active_tab_def['kind'] in {'class', 'archive'} and active_tab_def.get('class_name'):
        class_students = active_tab_def['students']
        student_ids = [s.id for s in class_students]
        if student_ids:
            class_query = _workplan_query_for_user(current_user).filter(WorkPlan.student_id.in_(student_ids))
            if class_date_from:
                class_query = class_query.filter(WorkPlan.period_end >= class_date_from)
            if class_date_to:
                class_query = class_query.filter(WorkPlan.period_start <= class_date_to)
            class_plans = (
                class_query
                .join(Schueler, Schueler.id == WorkPlan.student_id)
                .order_by(WorkPlan.period_start.desc(), Schueler.nachname.asc(), Schueler.vorname.asc())
                .all()
            )

    return render_template(
        'workplan_list.html',
        schueler=students,
        schueler_groups=student_groups,
        tab_definitions=tab_definitions,
        active_tab=active_tab,
        has_assigned_class_tabs=has_assigned_class_tabs,
        selected_s_id=selected_s_id,
        selected_student=selected_student,
        plans=plans,
        plan_scope=plan_scope,
        school_year_start=school_year_start,
        class_plans=class_plans,
        class_date_from=class_date_from_raw if class_date_from else '',
        class_date_to=class_date_to_raw if class_date_to else '',
        active_class_name=(active_tab_def.get('class_name') if active_tab_def else None),
        class_view_mode=class_view_mode,
    )


@workplan_bp.route('/arbeitsplan/neu/<int:s_id>', methods=['GET', 'POST'])
@login_required
def workplan_new_page(s_id):
    student = _ensure_student_access_or_403(s_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        start = _parse_date(request.form.get('period_start'), fallback=utc_now().date())
        end = _parse_date(request.form.get('period_end'), fallback=start + timedelta(days=7))
        if start > end:
            flash('Startdatum darf nicht nach Enddatum liegen.')
            return redirect(url_for('workplan.workplan_new_page', s_id=s_id, next=next_url))

        plan = WorkPlan(
            student_id=student.id,
            created_by_user_id=current_user.id,
            period_start=start,
            period_end=end,
            status=WORKPLAN_STATUS_IN_PLANUNG,
            notes_for_child=(request.form.get('notes_for_child') or '').strip() or None,
            notes_for_teacher=(request.form.get('notes_for_teacher') or '').strip() or None,
        )
        db.session.add(plan)
        db.session.commit()
        flash('Arbeitsplan angelegt.')
        return redirect(url_for('workplan.workplan_edit_page', plan_id=plan.id, next=next_url))

    return render_template(
        'workplan_new.html',
        student=student,
        plan=None,
        is_edit_mode=False,
        today=utc_now().date(),
        default_end=(utc_now().date() + timedelta(days=7)),
        next_url=next_url,
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/grunddaten', methods=['GET', 'POST'])
@login_required
def workplan_basics_edit_page(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    next_url = (request.args.get('next') or request.form.get('next') or '').strip()

    if request.method == 'POST':
        start = _parse_date(request.form.get('period_start'), fallback=plan.period_start)
        end = _parse_date(request.form.get('period_end'), fallback=plan.period_end)
        if start > end:
            flash('Startdatum darf nicht nach Enddatum liegen.')
            return redirect(url_for('workplan.workplan_basics_edit_page', plan_id=plan.id, next=next_url))

        plan.period_start = start
        plan.period_end = end
        plan.notes_for_child = (request.form.get('notes_for_child') or '').strip() or None
        plan.notes_for_teacher = (request.form.get('notes_for_teacher') or '').strip() or None
        db.session.commit()
        flash('Grunddaten gespeichert.')
        return redirect(_safe_next_url(next_url, url_for('workplan.workplan_edit_page', plan_id=plan.id)))

    return render_template(
        'workplan_new.html',
        student=plan.student,
        plan=plan,
        is_edit_mode=True,
        today=utc_now().date(),
        default_end=(plan.period_end or (utc_now().date() + timedelta(days=7))),
        next_url=next_url or url_for('workplan.workplan_edit_page', plan_id=plan.id),
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/bearbeiten', methods=['GET'])
@login_required
def workplan_edit_page(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    if not _teacher_can_access_student(current_user, plan.student):
        abort(403)

    suggestions = _compute_suggestions(plan.student)
    all_items = Item.query.order_by(Item.bogen_id.asc(), Item.bereich.asc(), Item.text.asc()).all()
    students = [s for s in get_prioritized_students_for_user(current_user) if _teacher_can_access_student(current_user, s)]

    return render_template(
        'workplan_edit.html',
        plan=plan,
        suggestions=suggestions,
        all_items=all_items,
        students=students,
        next_url=(request.args.get('next') or '').strip(),
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/kind')
@login_required
def workplan_child_view_page(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    return render_template(
        'workplan_child_view.html',
        plan=plan,
        next_url=(request.args.get('next') or '').strip(),
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/export/odt')
@login_required
def workplan_export_odt(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    if plan.status != WORKPLAN_STATUS_GESCHLOSSEN:
        plan.status = WORKPLAN_STATUS_AKTIV
        db.session.commit()

    odt_buffer = _build_workplan_odt(plan)
    safe_nachname = (plan.student.nachname or 'kind').replace(' ', '_')
    filename = f"Wochenplan_{safe_nachname}_{(plan.period_start.strftime('%Y-%m-%d') if plan.period_start else 'plan')}.odt"
    return send_file(
        odt_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=WORKPLAN_ODT_MIMETYPE,
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/export/pdf')
@login_required
def workplan_export_pdf(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    if plan.status != WORKPLAN_STATUS_GESCHLOSSEN:
        plan.status = WORKPLAN_STATUS_AKTIV
        db.session.commit()

    try:
        odt_buffer = _build_workplan_odt(plan)
        pdf_bytes = convert_odt_bytes_to_pdf(odt_buffer)
    except RuntimeError as exc:
        flash(f'PDF-Export nicht möglich: {exc}')
        return redirect(request.referrer or url_for('workplan.workplan_edit_page', plan_id=plan.id))
    except Exception:
        flash('PDF-Export fehlgeschlagen.')
        return redirect(request.referrer or url_for('workplan.workplan_edit_page', plan_id=plan.id))

    safe_nachname = (plan.student.nachname or 'kind').replace(' ', '_')
    filename = f"Wochenplan_{safe_nachname}_{(plan.period_start.strftime('%Y-%m-%d') if plan.period_start else 'plan')}.pdf"
    pdf_buffer = pdf_bytes if isinstance(pdf_bytes, BytesIO) else BytesIO(pdf_bytes)
    pdf_buffer.seek(0)
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=WORKPLAN_PDF_MIMETYPE,
    )


@workplan_bp.route('/arbeitsplaene/klasse/export/pdf')
@login_required
def workplan_export_class_pdf():
    tab_id = (request.args.get('tab') or '').strip()
    class_name = (request.args.get('klasse') or '').strip()
    from_raw = (request.args.get('klasse_von') or '').strip()
    to_raw = (request.args.get('klasse_bis') or '').strip()

    accessible_classes = set(_accessible_classes_for_user(current_user))
    if not class_name or class_name not in accessible_classes:
        abort(403)

    date_from = _parse_date(from_raw)
    date_to = _parse_date(to_raw)
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from

    class_students = Schueler.query.filter(Schueler.klasse == class_name, Schueler.is_active.is_(True)).order_by(Schueler.nachname.asc(), Schueler.vorname.asc()).all()
    class_students = [s for s in class_students if _teacher_can_access_student(current_user, s)]
    student_ids = [s.id for s in class_students]
    if not student_ids:
        flash('Keine Kinder in der ausgewählten Klasse gefunden.')
        return redirect(url_for('workplan.workplan_list_page', tab=tab_id or 'dropdown'))

    plans_query = _workplan_query_for_user(current_user).filter(WorkPlan.student_id.in_(student_ids))
    if date_from:
        plans_query = plans_query.filter(WorkPlan.period_end >= date_from)
    if date_to:
        plans_query = plans_query.filter(WorkPlan.period_start <= date_to)

    plans = (
        plans_query
        .join(Schueler, Schueler.id == WorkPlan.student_id)
        .order_by(WorkPlan.period_start.desc(), Schueler.nachname.asc(), Schueler.vorname.asc())
        .all()
    )
    if not plans:
        flash('Keine Arbeitspläne im gewählten Datumsbereich gefunden.')
        return redirect(url_for('workplan.workplan_list_page', tab=tab_id or 'dropdown', klasse_von=from_raw or None, klasse_bis=to_raw or None))

    for plan in plans:
        if plan.status != WORKPLAN_STATUS_GESCHLOSSEN and plan.status != WORKPLAN_STATUS_AKTIV:
            plan.status = WORKPLAN_STATUS_AKTIV
    db.session.commit()

    try:
        pdf_parts = []
        for plan in plans:
            current_odt = _build_workplan_odt(plan)
            pdf_parts.append(convert_odt_bytes_to_pdf(current_odt))
        pdf_buffer = _merge_pdf_buffers(pdf_parts)
    except RuntimeError as exc:
        flash(f'PDF-Export nicht möglich: {exc}')
        return redirect(url_for('workplan.workplan_list_page', tab=tab_id or 'dropdown', klasse_von=from_raw or None, klasse_bis=to_raw or None))
    except Exception:
        flash('PDF-Export fehlgeschlagen.')
        return redirect(url_for('workplan.workplan_list_page', tab=tab_id or 'dropdown', klasse_von=from_raw or None, klasse_bis=to_raw or None))

    from_part = date_from.strftime('%Y-%m-%d') if date_from else 'offen'
    to_part = date_to.strftime('%Y-%m-%d') if date_to else 'offen'
    safe_class = re.sub(r'[^A-Za-z0-9_-]+', '_', class_name).strip('_') or 'klasse'
    filename = f"Wochenplaene_Klasse_{safe_class}_{from_part}_{to_part}.pdf"
    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype=WORKPLAN_PDF_MIMETYPE,
    )


@workplan_bp.route('/arbeitsplan/<string:plan_id>/evaluate', methods=['GET'])
@login_required
def workplan_evaluate_page(plan_id):
    plan = _ensure_plan_access_or_404(plan_id)
    return render_template(
        'workplan_evaluate.html',
        plan=plan,
        today=utc_now().date(),
        next_url=(request.args.get('next') or '').strip(),
    )


def register_workplan_routes(app):
    app.register_blueprint(workplan_bp)
