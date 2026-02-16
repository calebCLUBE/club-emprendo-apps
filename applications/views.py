# applications/views.py
import re

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from .forms import build_application_form
from .models import Application, Answer, FormDefinition
from .emprendedora_a1_autograde import autograde_and_email_emprendedora_a1


GROUP_SLUG_RE = re.compile(r"^G(?P<num>\d+)_")


# -------------------------
# Utilities
# -------------------------
def _latest_group_form_slug(suffix: str) -> str | None:
    pattern = re.compile(rf"^G(?P<num>\d+)_{re.escape(suffix)}$")
    best = None
    best_num = -1

    for fd in FormDefinition.objects.filter(slug__endswith=suffix):
        m = pattern.match(fd.slug or "")
        if not m:
            continue
        n = int(m.group("num"))
        if n > best_num:
            best_num = n
            best = fd.slug

    return best


def _send_html_email(to_email: str, subject: str, html_body: str):
    msg = EmailMultiAlternatives(
        subject=subject,
        body="",
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)


def _mentor_a1_autograde_and_email(request, app: Application):
    answers = {
        a.question.slug: (a.value or "")
        for a in app.answers.select_related("question").all()
    }

    def yesish(v: str) -> bool:
        t = (v or "").strip().lower()
        return ("si" in t) or ("sí" in t) or ("yes" in t) or (t == "true") or (t == "1") or (t == "yes")

    requisitos = (
        answers.get("meets_requirements")
        or answers.get("m1_meet_requirements")
        or answers.get("m1_meets_requirements")
        or answers.get("m1_requirements_ok")
        or ""
    )
    disponibilidad = (
        answers.get("available_period")
        or answers.get("availability_ok")
        or answers.get("m1_availability_ok")
        or answers.get("m1_available_period")
        or answers.get("m1_available")
        or ""
    )

    passes_requisitos = yesish(requisitos)
    passes_disponibilidad = yesish(disponibilidad)

    if passes_requisitos and passes_disponibilidad:
        app.generate_invite_token()
        app.invited_to_second_stage = True
        app.save(update_fields=["invite_token", "invited_to_second_stage"])

        form2_url = request.build_absolute_uri(
            reverse("apply_mentora_second", kwargs={"token": app.invite_token})
        )

        subject = "Siguiente paso: Completa la segunda solicitud"
        html_body = (
            '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
            "<p><strong>Querida aplicante a Mentora,</strong></p>"
            "<p>Gracias por completar la primera aplicación para ser mentora en Club Emprendo. 🌱</p>"
            "<p>Con base en tus respuestas, confirmamos que cumples con los requisitos y la disponibilidad necesaria, por lo que estás habilitada para continuar con el proceso.</p>"
            "<p>A continuación, te compartimos la <strong>Aplicación #2</strong>, que es el segundo y último paso para postularte como mentora voluntaria.</p>"
            "<p><strong>📌 Instrucciones para acceder a la Aplicación #2:</strong></p>"
            "<ol>"
            f'<li>Haz clic aquí: 👉 <a href="{form2_url}">Aplicación 2</a> fecha límite - 11/01/2026 </li>'
            "<li>Lee con atención y responde cada pregunta.</li>"
            "</ol>"
            "<p>Gracias nuevamente por tu interés y compromiso con otras mujeres emprendedoras 💛</p>"
            "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
            "</div>"
        )
        _send_html_email(app.email, subject, html_body)
        return

    app.invited_to_second_stage = False
    app.save(update_fields=["invited_to_second_stage"])

    subject = "Sobre tu aplicación como mentora voluntaria 🌟"
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;line-height:1.6;max-width:700px;margin:0 auto;word-break:break-word;white-space:normal;">'
        "<p>Querida aplicante a mentora,</p>"
        "<p>Gracias por tu interés en ser parte del programa de mentoría de Club Emprendo. 💛</p>"
        "<p>En la aplicación indicaste que no cumples uno o más requisitos o disponibilidad para esta cohorte, por eso no podremos enviarte el paso 2.</p>"
        "<p>Con cariño,<br><strong>El equipo de Club Emprendo</strong></p>"
        "</div>"
    )
    _send_html_email(app.email, subject, html_body)


def _m2_sections(form_def: FormDefinition):
    def find_all_fragments(fragments):
        out = []
        for q in form_def.questions.filter(active=True).order_by("position", "id"):
            t = (q.text or "").lower()
            if all(f.lower() in t for f in fragments):
                out.append(f"q_{q.slug}")
        return out

    def find_any_keywords(keywords):
        out = []
        for q in form_def.questions.filter(active=True).order_by("position", "id"):
            t = (q.text or "").lower()
            if any(k.lower() in t for k in keywords):
                out.append(f"q_{q.slug}")
        return out

    owned_business_fields = find_all_fragments(["has dirigido tu propio negocio"])
    owned_business_field = owned_business_fields[0] if owned_business_fields else None

    sections = [
        {
            "key": "s1",
            "title": "Información personal",
            "intro": (
                "Solicitamos tu número de cédula únicamente para identificar de forma única tu postulación y evitar aplicaciones duplicadas.\n\n"
                "Tu información será utilizada exclusivamente para fines administrativos del programa de mentoría y tratada con estricta confidencialidad, conforme a la legislación de protección de datos personales vigente en tu país.\n\n"
                "🛡 Aviso de privacidad:\n"
                "Club Emprendo recopila datos personales limitados, como tu nombre y número de cédula, con fines administrativos relacionados con el proceso de postulación.\n"
                "Nos comprometemos a tratar esta información de forma confidencial, segura y conforme a las leyes de protección de datos aplicables en América Latina.\n"
                "Puedes ejercer tus derechos de acceso, corrección o eliminación de datos escribiéndonos a: contacto@clubemprendo.org"
            ),
            "field_names": find_any_keywords([
                "cédula", "cedula", "documento de identidad",
                "nombre completo",
                "nombre de preferencia",
                "certificado",
                "correo electrónico", "correo electronico",
                "whatsapp",
                "ciudad de residencia",
                "país de residencia", "pais de residencia",
                "país de nacimiento", "pais de nacimiento",
                "edad",
                "participado anteriormente",
                "aviso de privacidad",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s2",
            "title": "Requisitos del programa",
            "intro": "",
            "field_names": find_any_keywords([
                "requisitos básicos", "requisitos basicos",
                "requisitos de disponibilidad",
                "marca la casilla",
                "confirmar tu entendimiento",
                "si no cumples",
                "especifica cuál", "especifica cual",
                "revisaste el pdf",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s3",
            "title": "Experiencia previa",
            "intro": "",
            "field_names": owned_business_fields,
            "show_if_field": None,
        },
        {
            "key": "s4",
            "title": "Experiencia como emprendedora",
            "intro": (
                "En esta sección, te solicitamos que compartas tu experiencia previa como emprendedor(a). "
                "Responde a las preguntas sobre los negocios que has dirigido, centrándote en tu negocio favorito o "
                "más notable si has gestionado más de uno. Tu experiencia será valiosa para ayudar a nuestras "
                "microemprendedoras a crecer y superar desafíos"
            ),
            "field_names": find_any_keywords([
                "nombre de tu emprendimiento",
                "industria de tu emprendimiento",
                "descripción del negocio", "descripcion del negocio",
                "dónde operas tu negocio", "donde operas tu negocio",
                "cuánto tiempo has estado operando", "cuanto tiempo has estado operando",
                "tienes empleados",
            ]),
            "show_if_field": owned_business_field,
        },
        {
            "key": "s5",
            "title": "Motivación y experiencia con la mentoría",
            "intro": (
                "💡 Tip importante:\n"
                "En las preguntas abiertas, te recomendamos que seas lo más amplia posible al compartir tu experiencia, "
                "motivaciones y visión. 📝✨ Evita responder solo con una o dos frases — ¡queremos conocerte mejor para "
                "valorar todo lo que puedes aportar como mentora!"
            ),
            "field_names": find_any_keywords([
                "área de experiencia profesional", "area de experiencia profesional",
                "qué te motiva", "que te motiva",
                "buena mentora",
                "experiencia previa con mentoría", "experiencia previa con mentoria",
                "describe brevemente tu experiencia",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s6",
            "title": "Disponibilidad",
            "intro": "",
            "field_names": find_any_keywords([
                "cuánto tiempo puedes dedicar", "cuanto tiempo puedes dedicar",
                "en qué horario te resulta más conveniente", "en que horario te resulta mas conveniente",
            ]),
            "show_if_field": None,
        },
        {
            "key": "s8",
            "title": "Comentarios adicionales",
            "intro": "Este espacio es tuyo: comentarios, dudas, sugerencias o algo que no hayamos preguntado.",
            "field_names": find_any_keywords([
                "hay algo más que te gustaría compartir",
                "hay algo mas que te gustaria compartir",
            ]),
            "show_if_field": None,
        },
    ]

    for s in sections:
        seen = set()
        deduped = []
        for n in s["field_names"]:
            if n and n not in seen:
                seen.add(n)
                deduped.append(n)
        s["field_names"] = deduped

    sections = [s for s in sections if s["field_names"]]
    return sections, owned_business_field


def _sections_from_model(form_def: FormDefinition, form):
    """
    Build a list of section dictionaries using Section model assignments.
    """
    sections_qs = list(form_def.sections.order_by("position", "id"))
    if not sections_qs:
        return None

    q_by_id = {q.id: q for q in form_def.questions.all()}

    section_map = {
        s.id: {
            "id": s.id,
            "title": s.title,
            "intro": s.description,
            "fields": [],
        }
        for s in sections_qs
    }

    default_bucket = {
        "id": "unassigned",
        "title": form_def.default_section_title or "Preguntas generales",
        "intro": "",
        "fields": [],
    }

    for field in form:
        raw = field.field.widget.attrs.get("section_id") if hasattr(field.field, "widget") else ""
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            sid = None

        if sid and sid in section_map:
            section_map[sid]["fields"].append(field)
        else:
            default_bucket["fields"].append(field)

    ordered = []
    for bucket in ([default_bucket] + [section_map[s.id] for s in sections_qs]):
        if bucket["fields"]:
            ordered.append(bucket)

    if not ordered:
        return None

    return ordered


def _handle_application_form(request, form_slug: str, second_stage: bool = False):
    form_def = get_object_or_404(FormDefinition, slug=form_slug)
        # Block new submissions when closed (we use is_public as "open" flag)
    if not form_def.is_public:
        return render(
            request,
            "applications/closed.html",
            {"form_def": form_def},
            status=403,
        )


    if request.method == "POST" and not getattr(form_def, "accepting_responses", True):
        return render(
            request,
            "applications/form_closed.html",
            {
                "form_def": form_def,
                "second_stage": second_stage,
            },
            status=403,
        )

    ApplicationForm = build_application_form(form_slug)

    rendered_description = ""
    for attr in ("description", "intro", "intro_text", "public_description"):
        if hasattr(form_def, attr):
            v = getattr(form_def, attr) or ""
            if str(v).strip():
                rendered_description = str(v)
                break

    if rendered_description.strip() and (
        rendered_description.strip() == (form_def.description or "").strip()
    ):
        rendered_description = ""

    if request.method == "POST":
        form = ApplicationForm(request.POST)
    else:
        form = ApplicationForm()

    sections = _sections_from_model(form_def, form)
    m2_gate_field = None

    # Legacy: fallback to heuristic sections for Mentora A2 if no explicit sections exist
    if not sections and (form_def.slug or "").endswith("M_A2"):
        raw_sections, gate = _m2_sections(form_def)
        m2_gate_field = gate

        sections = []
        for s in raw_sections:
            bound = []
            for fname in s["field_names"]:
                if fname in form.fields:
                    bound.append(form[fname])
            if bound:
                sections.append({
                    "key": s["key"],
                    "title": s["title"],
                    "intro": s["intro"],
                    "show_if_field": s["show_if_field"],
                    "fields": bound,
                })

    if request.method == "POST" and form.is_valid():

        def _pick_first(*keys: str) -> str:
            for k in keys:
                v = (form.cleaned_data.get(k) or "").strip()
                if v:
                    return v
            return ""

        full_name = _pick_first(
            "q_full_name", "q_name", "q_nombre",
            "q_e1_full_name", "q_m1_full_name",
            "q_e2_full_name", "q_m2_full_name",
        )
        email = _pick_first(
            "q_email", "q_correo", "q_correo_electronico",
            "q_e1_email", "q_m1_email",
            "q_e2_email", "q_m2_email",
        )

        if not email:
            for k, v in form.cleaned_data.items():
                if not k.startswith("q_"):
                    continue
                s = (v or "").strip()
                if "@" in s and "." in s:
                    email = s
                    break

        if not full_name:
            for k, v in form.cleaned_data.items():
                if not k.startswith("q_"):
                    continue
                lk = k.lower()
                if ("name" in lk) or ("nombre" in lk):
                    s = (v or "").strip()
                    if s:
                        full_name = s
                        break

        app = Application.objects.create(
            form=form_def,
            name=full_name,
            email=email,
        )

        for q in form_def.questions.filter(active=True).order_by("position", "id"):
            field_name = f"q_{q.slug}"
            value = form.cleaned_data.get(field_name)
            if isinstance(value, list):
                value = ",".join(value)
            Answer.objects.create(
                application=app,
                question=q,
                value=str(value or ""),
            )

        # A1 autogrades
        if form_def.slug.endswith("M_A1"):
            _mentor_a1_autograde_and_email(request, app)

        if form_def.slug.endswith("E_A1"):
            autograde_and_email_emprendedora_a1(request, app)

        # group number (from slug like G5_M_A1)
        group_num = ""
        m = GROUP_SLUG_RE.match(form_def.slug or "")
        if m:
            group_num = m.group("num")

        # Track for rejection message
        track = ""
        if form_def.slug.endswith("M_A1"):
            track = "mentoras"
        elif form_def.slug.endswith("E_A1"):
            track = "emprendedoras"

        # ✅ Thank-you routing
        if form_def.slug.endswith("M_A2"):
            request.session["ce_thanks_payload"] = {
                "kind": "mentor_final",
                "group_num": group_num,
            }
        elif form_def.slug.endswith("E_A2"):
            request.session["ce_thanks_payload"] = {
                "kind": "emprendedora_final",
                "group_num": group_num,
            }
        else:
            request.session["ce_thanks_payload"] = {
                "kind": "a1",
                "approved": bool(app.invited_to_second_stage),
                "group_num": group_num,
                "track": track,
            }

        return redirect("application_thanks")

    return render(
        request,
        "applications/application_form.html",
        {
            "form": form,
            "form_def": form_def,
            "second_stage": second_stage,
            "rendered_description": rendered_description,
            "sections": sections,
            "m2_gate_field": m2_gate_field,
        },
    )


def apply_emprendedora_first(request):
    latest = _latest_group_form_slug("E_A1")
    return _handle_application_form(request, latest or "E_A1", second_stage=False)


def apply_mentora_first(request):
    latest = _latest_group_form_slug("M_A1")
    return _handle_application_form(request, latest or "M_A1", second_stage=False)


def apply_emprendedora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    form_slug = "E_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_E_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

    return _handle_application_form(request, form_slug, second_stage=True)


def apply_mentora_second(request, token):
    first_app = get_object_or_404(Application, invite_token=token)

    form_slug = "M_A2"
    m = GROUP_SLUG_RE.match(first_app.form.slug or "")
    if m:
        gnum = m.group("num")
        candidate = f"G{gnum}_M_A2"
        if FormDefinition.objects.filter(slug=candidate).exists():
            form_slug = candidate

    return _handle_application_form(request, form_slug, second_stage=True)


def apply_emprendedora_second_preview(request):
    latest = _latest_group_form_slug("E_A2")
    return _handle_application_form(request, latest or "E_A2", second_stage=True)


def apply_mentora_second_preview(request):
    latest = _latest_group_form_slug("M_A2")
    return _handle_application_form(request, latest or "M_A2", second_stage=True)


def apply_by_slug(request, form_slug):
    second_stage = str(form_slug).endswith("_A2")
    return _handle_application_form(request, form_slug, second_stage=second_stage)


def application_thanks(request):
    payload = request.session.pop("ce_thanks_payload", None) or {}
    return render(request, "applications/thanks.html", payload)


def survey_by_slug(request, form_slug):
    return _handle_application_form(request, form_slug, second_stage=False)
