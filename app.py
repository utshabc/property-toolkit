import streamlit as st
import fitz  # PyMuPDF
import anthropic
import base64
import json
import datetime
import os

def get_api_key():
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.environ.get("ANTHROPIC_API_KEY")

client = anthropic.Anthropic(api_key=get_api_key())

with open("checklist.md", "r") as f:
    checklist_text = f.read()

USAGE_FILE = "usage.json"
DAILY_LIMIT = 5

def check_and_increment_usage():
    today = str(datetime.date.today())
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "r") as f:
            usage = json.load(f)
    else:
        usage = {}

    if usage.get("date") != today:
        usage = {"date": today, "count": 0}

    if usage["count"] >= DAILY_LIMIT:
        return False, usage["count"]

    usage["count"] += 1
    with open(USAGE_FILE, "w") as f:
        json.dump(usage, f)

    return True, usage["count"]

def get_usage_today():
    today = str(datetime.date.today())
    if os.path.exists(USAGE_FILE):
        with open(USAGE_FILE, "r") as f:
            usage = json.load(f)
        if usage.get("date") == today:
            return usage["count"]
    return 0

st.title("🏠 Building & Pest Report Reviewer")
st.caption("Upload a building & pest inspection report to get a plain-language summary.")
st.caption(f"Daily usage: {get_usage_today()}/{DAILY_LIMIT} reviews used today")

uploaded_file = st.file_uploader("Upload your report (PDF)", type=["pdf"])

consent = st.checkbox(
    "I understand this tool provides a plain-language summary for my own information only. "
    "It is not legal, financial, or professional building/pest advice."
)

def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text

def classify_document(full_text):
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=(
            "You are a fast document-type classifier. Given text extracted from an "
            "uploaded PDF, decide whether it is a building and/or timber pest inspection "
            "report — a report from a licensed inspector assessing the physical condition "
            "of a residential property (defects, timber pests, safety hazards, etc). "
            "Legal/strata/contract/disclosure documents are NOT building & pest reports, "
            "even if they mention the property. Respond with ONLY valid JSON, no markdown: "
            '{"is_building_pest_report": true or false, "reason": "one plain-language '
            'sentence saying what the document actually is, only needed if false"}'
        ),
        messages=[{"role": "user", "content": f"DOCUMENT TEXT (excerpt):\n{full_text[:6000]}"}]
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return json.loads(extract_json(text))

SEVERITY_STYLE = {
    "safety-critical": ("#E4002B", "⛔ Safety-critical"),
    "major": ("#F97316", "🔶 Major"),
    "moderate": ("#F5B301", "🔸 Moderate"),
    "minor": ("#2E7D32", "🟢 Minor"),
    "gap": ("#6B7280", "❔ Needs follow-up"),
}

def severity_badge(severity):
    color, label = SEVERITY_STYLE.get(severity, ("#6B7280", severity.title()))
    return (
        f'<span style="background-color:{color};color:white;padding:4px 12px;'
        f'border-radius:999px;font-size:0.85em;font-weight:600;">{label}</span>'
    )

def build_markdown_report(data):
    lines = ["# Building & Pest Report Review", ""]
    if data.get("report_type_note"):
        lines += [f"> {data['report_type_note']}", ""]

    lines += ["## Top 3 Priority Actions", ""]
    for i, action in enumerate(data.get("top_3_actions", []), start=1):
        lines.append(f"{i}. {action}")
    lines.append("")

    counts = {}
    for f in data["findings"]:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    lines += ["## Severity Breakdown", ""]
    for sev, (_, label) in SEVERITY_STYLE.items():
        lines.append(f"- {label}: {counts.get(sev, 0)}")
    lines.append("")

    lines += ["## Full Findings List", ""]
    for f in data["findings"]:
        label = SEVERITY_STYLE.get(f["severity"], (None, f["severity"]))[1]
        lines.append(f"### {label}")
        lines.append(f"{f['description']}")
        lines.append(f"- **If left 6–12 months:** {f['trajectory_6_12_months']}")
        lines.append(f"- **If left 3–5 years:** {f['trajectory_3_5_years']}")
        lines.append(f"- **Indicative cost:** {f['cost_estimate']}")
        if f.get("source_page"):
            lines.append(f"- **Report page:** {f['source_page']}")
        lines.append("")

    lines += ["## Checklist Compliance Review", "", "| Item | Status | Notes |", "|---|---|---|"]
    for c in data["checklist_review"]:
        lines.append(f"| {c['item']} | {c['status']} | {c['notes']} |")
    lines.append("")
    lines.append("---")
    lines.append(
        "*This is a plain-language summary tool only — not legal, financial, or professional "
        "building/pest advice. All costs are general estimates, not quotes.*"
    )
    return "\n".join(lines)

if st.button("Submit", type="primary"):
    if not uploaded_file:
        st.error("Please upload a PDF first.")
    elif not consent:
        st.error("Please tick the consent box to continue.")
    else:
        allowed, count_so_far = check_and_increment_usage()
        if not allowed:
            st.error(
                f"Daily limit of {DAILY_LIMIT} reviews reached for today. "
                "Please try again tomorrow."
            )
            st.stop()

        pdf_bytes = uploaded_file.read()
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        full_text = ""
        for i, page in enumerate(doc):
            full_text += f"\n\n--- Page {i + 1} ---\n" + page.get_text()

        with st.spinner("Checking document type..."):
            classification = classify_document(full_text)

        if not classification.get("is_building_pest_report", True):
            st.error(
                "This doesn't look like a building & pest inspection report — "
                f"{classification.get('reason', 'the document does not match the expected type.')}"
            )
            st.info("Please upload a building and/or pest inspection report to get a plain-language review.")
            st.stop()

        content = []
        page_images = {}

        for i, page in enumerate(doc):
            page_num = i + 1
            if page.get_images(full=True):
                pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5))
                png_bytes = pix.tobytes("png")
                page_images[page_num] = png_bytes
                b64 = base64.b64encode(png_bytes).decode("utf-8")
                content.append({"type": "text", "text": f"Image below is page {page_num} of the report:"})
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64}
                })

        content.insert(0, {
            "type": "text",
            "text": f"CHECKLIST:\n{checklist_text}\n\nREPORT TEXT (page markers included):\n{full_text}"
        })

        with st.spinner("Reading your report, including photos..."):
            message = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=12000,
                system=(
                    "You are reviewing a building & pest inspection report for a home buyer. "
                    "You are not a licensed builder, pest inspector, or lawyer, and must not give "
                    "legal, financial, or professional advice. You are given the report's text "
                    "(with page number markers) and images of pages that contain photos — look at "
                    "the photos when relevant to a finding.\n\n"
                    "Respond with ONLY a valid JSON object — no markdown, no code fences, no text "
                    "before or after — matching exactly this structure:\n"
                    "{\n"
                    '  "report_type_note": "one sentence noting what kind of report this is and any scope gap, or null",\n'
                    '  "top_3_actions": ["...", "...", "..."],\n'
                    '  "findings": [\n'
                    "    {\n"
                    '      "description": "plain language description",\n'
                    '      "severity": "safety-critical | major | moderate | minor | gap",\n'
                    '      "trajectory_6_12_months": "qualitative description",\n'
                    '      "trajectory_3_5_years": "qualitative description",\n'
                    '      "cost_estimate": "indicative AUD range, general estimate only",\n'
                    '      "source_page": integer page number this finding relates to, or null\n'
                    "    }\n"
                    "  ],\n"
                    '  "checklist_review": [\n'
                    '    {"item": "checklist item name", "status": "pass | partial | gap", "notes": "..."}\n'
                    "  ]\n"
                    "}\n\n"
                    "Only include ACTUAL findings, issues, or gaps in the findings array — never "
                    "include a 'no issues found' statement as a finding. Never include an overall "
                    "score or buy/don't-buy recommendation anywhere. When referencing technical "
                    "standards, use plain language, not raw code numbers. Do not worry about "
                    "ordering the findings array — that is handled separately."
                ),
                messages=[{"role": "user", "content": content}]
            )

        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        image_count = sum(1 for c in content if c.get("type") == "image")
        st.caption(f"Debug — stop reason: {message.stop_reason} | tokens used: {message.usage.output_tokens} | images sent: {image_count}")

        try:
            data = json.loads(extract_json(response_text))
        except json.JSONDecodeError as e:
            st.error(f"Could not parse JSON: {e}")
            st.subheader("Raw response (for debugging)")
            st.write(response_text)
            st.stop()

        severity_rank = {"safety-critical": 0, "major": 1, "moderate": 2, "minor": 3, "gap": 4}
        data["findings"].sort(key=lambda f: severity_rank.get(f.get("severity", "gap"), 5))

        if data.get("report_type_note"):
            st.info(data["report_type_note"])

        st.download_button(
            label="⬇️ Download this review",
            data=build_markdown_report(data),
            file_name=f"{uploaded_file.name.rsplit('.', 1)[0]}_review.md",
            mime="text/markdown",
            type="primary"
        )

        st.markdown("## 🎯 Top 3 Priority Actions")
        with st.container(border=True):
            for i, action in enumerate(data.get("top_3_actions", []), start=1):
                st.markdown(f"**{i}.** {action}")

        counts = {}
        for f in data["findings"]:
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1

        st.markdown("## 📊 Severity Breakdown")
        cols = st.columns(len(SEVERITY_STYLE))
        for col, (sev, (color, label)) in zip(cols, SEVERITY_STYLE.items()):
            with col:
                st.markdown(
                    f'<div style="text-align:center;padding:12px;border-radius:12px;'
                    f'background-color:{color}20;border:2px solid {color};">'
                    f'<div style="font-size:1.8em;font-weight:700;color:{color};">{counts.get(sev,0)}</div>'
                    f'<div style="font-size:0.8em;color:{color};">{label}</div></div>',
                    unsafe_allow_html=True
                )

        st.markdown("## 📋 Full Findings List")
        for f in data["findings"]:
            with st.container(border=True):
                img_col, text_col = st.columns([1, 2])
                with img_col:
                    src_page = f.get("source_page")
                    if src_page and src_page in page_images:
                        st.image(page_images[src_page], caption=f"Page {src_page}", use_container_width=True)
                    else:
                        st.markdown(
                            '<div style="background-color:#F5F6F7;border-radius:8px;padding:40px 10px;'
                            'text-align:center;color:#9CA3AF;">No photo for this item</div>',
                            unsafe_allow_html=True
                        )
                with text_col:
                    st.markdown(severity_badge(f["severity"]), unsafe_allow_html=True)
                    st.markdown(f"**{f['description']}**")
                    st.markdown(f"**If left 6–12 months:** {f['trajectory_6_12_months']}")
                    st.markdown(f"**If left 3–5 years:** {f['trajectory_3_5_years']}")
                    st.markdown(f"**Indicative cost:** {f['cost_estimate']}")

        st.markdown("## ✅ Checklist Compliance Review")
        st.table(data["checklist_review"])
