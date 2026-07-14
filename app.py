import streamlit as st
import fitz  # PyMuPDF
import anthropic
import base64
import json
import datetime
import os
import io
from PIL import Image

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

with st.expander("📋 Your property context (optional)"):
    st.caption(
        "Property age, construction type, and roof details are read directly from your "
        "report — no need to enter them. Just add the price if you want a verdict and "
        "dollar-tier prioritization."
    )
    price = st.text_input("Asking/contract price", placeholder="e.g. $650,000")
    plan = st.selectbox(
        "Your plan for this property",
        ["", "Long-term rental", "Renovate", "Land value / knock-down", "Owner-occupy"]
    )

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

VERDICT_STYLE = {
    "proceed as is": ("#2E7D32", "✅ Proceed as is"),
    "proceed with negotiation": ("#F5B301", "🤝 Proceed with negotiation"),
    "specialist follow-up before deciding": ("#3B82F6", "🔍 Specialist follow-up before deciding"),
    "walk away": ("#E4002B", "🚫 Walk away"),
}

def severity_badge(severity):
    color, label = SEVERITY_STYLE.get(severity, ("#6B7280", severity.title()))
    return (
        f'<span style="background-color:{color};color:white;padding:4px 12px;'
        f'border-radius:999px;font-size:0.85em;font-weight:600;">{label}</span>'
    )

def build_markdown_report(data, image_registry):
    lines = ["# Building & Pest Report Review", ""]
    if data.get("report_type_note"):
        lines += [f"> {data['report_type_note']}", ""]

    extracted = data.get("extracted_property_details")
    if extracted and any(extracted.values()):
        lines += ["## Property Details (extracted from report)", ""]
        if extracted.get("age_year_built"):
            lines.append(f"- **Built:** {extracted['age_year_built']}")
        if extracted.get("construction_type"):
            lines.append(f"- **Construction:** {extracted['construction_type']}")
        if extracted.get("roof_type"):
            lines.append(f"- **Roof:** {extracted['roof_type']}")
        if extracted.get("foundation_type"):
            lines.append(f"- **Foundation:** {extracted['foundation_type']}")
        lines.append("")

    verdict = data.get("verdict")
    if verdict:
        lines += [
            "## Verdict",
            f"**{verdict.get('recommendation', '').title()}**",
            verdict.get("reasoning", ""),
        ]
        if verdict.get("negotiation_amount"):
            lines.append(f"Suggested negotiation figure: {verdict['negotiation_amount']}")
        lines.append("")

    lines += ["## Top 3 Priority Actions", ""]
    for i, item in enumerate(data.get("top_3_actions", []), start=1):
        action = item.get("action") if isinstance(item, dict) else item
        script = item.get("script") if isinstance(item, dict) else None
        lines.append(f"{i}. {action}")
        if script:
            lines.append(f"   - 💬 Say this: \"{script}\"")
    lines.append("")

    if data.get("inspector_questions"):
        lines += ["## Questions to Ask Your Inspector", ""]
        for q in data["inspector_questions"]:
            lines.append(f"- {q}")
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
        tier_tag = f" (Tier {f['tier']})" if f.get("tier") else ""
        lines.append(f"### {label}{tier_tag}")
        lines.append(f"{f['description']}")
        if f.get("source_quote"):
            lines.append(f"> \"{f['source_quote']}\"")
        if f.get("pest_category"):
            lines.append(f"- 🐛 **Pest category:** {f['pest_category']}")
        if f.get("hedge_language_note"):
            lines.append(f"- ⚠️ **Hedge language check:** {f['hedge_language_note']}")
        if f.get("regional_risk_note"):
            lines.append(f"- 📍 **Regional risk:** {f['regional_risk_note']}")
        if f.get("landlord_compliance_note"):
            lines.append(f"- 🏠 **Landlord compliance:** {f['landlord_compliance_note']}")
        lines.append(f"- **If left 6–12 months:** {f['trajectory_6_12_months']}")
        lines.append(f"- **If left 3–5 years:** {f['trajectory_3_5_years']}")
        lines.append(f"- **Indicative cost:** {f['cost_estimate']}")
        photo_id = f.get("source_photo_id")
        if photo_id and photo_id in image_registry:
            lines.append(f"- **Report page:** {image_registry[photo_id]['page']}")
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
        image_registry = {}
        photo_counter = 0

        for i, page in enumerate(doc):
            page_num = i + 1
            for img in page.get_images(full=True):
                xref = img[0]
                try:
                    base_image = doc.extract_image(xref)
                    width = base_image.get("width", 0)
                    height = base_image.get("height", 0)
                    if width < 150 or height < 150:
                        continue  # skip tiny logos/icons, not real inspection photos
                    pil_img = Image.open(io.BytesIO(base_image["image"])).convert("RGB")
                    buf = io.BytesIO()
                    pil_img.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                except Exception:
                    continue

                photo_counter += 1
                photo_id = f"photo_{photo_counter}"
                image_registry[photo_id] = {"bytes": png_bytes, "page": page_num}

                b64 = base64.b64encode(png_bytes).decode("utf-8")
                content.append({"type": "text", "text": f"{photo_id} (from report page {page_num}):"})
                content.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64}
                })

        context_lines = []
        if price:
            context_lines.append(f"Asking/contract price: {price}")
        if plan:
            context_lines.append(f"Buyer's plan: {plan}")
        context_text = "\n".join(context_lines) if context_lines else "None provided."

        content.insert(0, {
            "type": "text",
            "text": f"BUYER CONTEXT:\n{context_text}\n\nCHECKLIST:\n{checklist_text}\n\n"
                    f"REPORT TEXT (page markers included):\n{full_text}"
        })

        with st.spinner("Reading your report, including photos..."):
            message = client.messages.create(
                model="claude-sonnet-5",
                max_tokens=12000,
                system=(
                    "You are reviewing a building & pest inspection report for a home buyer. "
                    "You are not a licensed builder, pest inspector, or lawyer, and must not give "
                    "legal, financial, or professional advice, EXCEPT for the conditional verdict "
                    "behavior described below, which only activates when a price is provided.\n\n"
                    "You are given: buyer context (price and buyer's plan, if supplied), the "
                    "checklist, the report's text (with page number markers), and individual "
                    "labelled photos extracted from the report (each labelled photo_1, photo_2, "
                    "etc, with the page it came from) — look at the photos when relevant.\n\n"
                    "EXTRACTED PROPERTY DETAILS: Most compliant reports state the property's age/"
                    "year built, construction type (e.g. brick veneer, weatherboard, double brick), "
                    "roof type, and foundation type (slab, stumps, etc) as part of the inspection "
                    "scope. Extract these directly from the report text into "
                    "'extracted_property_details' so the reader doesn't have to type them in. If a "
                    "detail genuinely isn't stated anywhere in the report, say so rather than "
                    "guessing.\n\n"
                    "CONDITIONAL VERDICT MODE: If a price was provided in the buyer context, "
                    "populate the 'verdict' object with a recommendation (one of: 'proceed as is', "
                    "'proceed with negotiation', 'specialist follow-up before deciding', 'walk away'), "
                    "2-3 sentences of reasoning, and if negotiating, a specific dollar figure and how "
                    "to justify it given the price and findings. Also populate each finding's 'tier' "
                    "field: tier 1 = safety hazards, structural defects, active termites, or anything "
                    "likely over ~$10k; tier 2 = roughly $2k-$10k or negotiation-relevant; tier 3 = "
                    "minor/cosmetic. If no price was given, set 'verdict' to null and leave 'tier' "
                    "null on every finding — do not give a verdict or dollar-tier grouping in that "
                    "case, use severity only. Every reader gets the same findings, photos, costs, "
                    "and trajectories regardless of price; price only unlocks the verdict and tier "
                    "layer on top.\n\n"
                    "HEDGE LANGUAGE: When a finding involves phrases like 'further investigation "
                    "recommended' or 'outside the scope of this inspection', note in "
                    "'hedge_language_note' whether this reads as generic liability boilerplate or a "
                    "genuine signal worth following up, and briefly say what makes you read it that way. "
                    "Null if not applicable.\n\n"
                    "PEST CATEGORIZATION: For pest/timber-pest findings, classify 'pest_category' as "
                    "one of: 'active termite activity', 'historical damage', 'conducive conditions', "
                    "'evidence of prior treatment'. Each carries different risk and action. For borer "
                    "findings, distinguish in the description whether it's lyctus (sapwood only, "
                    "usually cosmetic) or anobium (can be structural in older pine floors). Leave "
                    "pest_category null if the finding isn't pest-related.\n\n"
                    "REGIONAL RISK: Infer the property's state from the report (address, inspector "
                    "licensing body, etc). Where relevant, note in 'regional_risk_note' any applicable "
                    "regional risk context: cyclone wind regions in northern QLD/WA, higher termite "
                    "pressure in QLD and northern WA, restumping/reblocking risk in older VIC homes, "
                    "salt damp in SA stone buildings, asbestos likelihood in anything built pre-1990. "
                    "Null if not relevant to this finding.\n\n"
                    "LANDLORD COMPLIANCE: For smoke alarms, balustrades, pool fencing, or similar items "
                    "covered by state minimum rental standards, note in 'landlord_compliance_note' that "
                    "this may carry a statutory compliance deadline or fine separate from ordinary "
                    "repair cost, and recommend confirming current requirements for the relevant state. "
                    "Null if not applicable.\n\n"
                    "INSPECTOR QUESTIONS: Separately from top_3_actions, compile a fuller list in "
                    "'inspector_questions' of every question genuinely worth a phone call to the "
                    "inspector — contradictions in the report, ambiguous findings, anything the report "
                    "hedges on.\n\n"
                    "Respond with ONLY a valid JSON object — no markdown, no code fences, no text "
                    "before or after — matching exactly this structure:\n"
                    "{\n"
                    '  "report_type_note": "one sentence noting what kind of report this is and any scope gap, or null",\n'
                    '  "extracted_property_details": {"age_year_built": "... or null if not stated", '
                    '"construction_type": "... or null", "roof_type": "... or null", '
                    '"foundation_type": "... or null"},\n'
                    '  "verdict": {"recommendation": "...", "reasoning": "...", "negotiation_amount": "... or null"} or null,\n'
                    '  "top_3_actions": [\n'
                    "    {\n"
                    '      "action": "plain-language description of what to do or ask about",\n'
                    '      "script": "an exact, copy-pasteable sentence the reader could literally say '
                    'to the inspector, agent, or vendor to follow up on this"\n'
                    "    }\n"
                    "  ],\n"
                    '  "inspector_questions": ["...", "..."],\n'
                    '  "findings": [\n'
                    "    {\n"
                    '      "description": "plain language description",\n'
                    '      "source_quote": "the exact sentence or phrase from the report this finding is '
                    'based on, quoted verbatim, or null if it is a general observation not tied to one '
                    'specific line",\n'
                    '      "severity": "safety-critical | major | moderate | minor | gap",\n'
                    '      "tier": "1 | 2 | 3, or null (see CONDITIONAL VERDICT MODE above)",\n'
                    '      "pest_category": "active termite activity | historical damage | conducive '
                    'conditions | evidence of prior treatment, or null",\n'
                    '      "hedge_language_note": "... or null",\n'
                    '      "regional_risk_note": "... or null",\n'
                    '      "landlord_compliance_note": "... or null",\n'
                    '      "trajectory_6_12_months": "qualitative description",\n'
                    '      "trajectory_3_5_years": "qualitative description",\n'
                    '      "cost_estimate": "indicative AUD range, general estimate only",\n'
                    '      "source_photo_id": "the exact photo_N label that best illustrates this finding, '
                    'or null if no specific photo applies"\n'
                    "    }\n"
                    "  ],\n"
                    '  "checklist_review": [\n'
                    '    {"item": "checklist item name", "status": "pass | partial | gap", "notes": "..."}\n'
                    "  ]\n"
                    "}\n\n"
                    "Only include ACTUAL findings, issues, or gaps in the findings array — never "
                    "include a 'no issues found' statement as a finding. If the report is genuinely "
                    "thorough and clean, say so plainly in report_type_note or the findings — do not "
                    "manufacture problems, gaps, or concerns that are not genuinely present just to "
                    "have something to report. Outside of the conditional verdict mode described above, "
                    "never include an overall score or buy/don't-buy recommendation anywhere. When "
                    "referencing technical standards, use plain language, not raw code numbers. Do not "
                    "worry about ordering the findings array — that is handled separately."
                ),
                messages=[{"role": "user", "content": content}]
            )

        response_text = ""
        for block in message.content:
            if block.type == "text":
                response_text += block.text

        st.caption(f"Debug — stop reason: {message.stop_reason} | tokens used: {message.usage.output_tokens} | photos sent: {photo_counter}")

        try:
            data = json.loads(extract_json(response_text))
        except json.JSONDecodeError as e:
            st.error(f"Could not parse JSON: {e}")
            st.subheader("Raw response (for debugging)")
            st.write(response_text)
            st.stop()

        if price:
            tier_rank = {"1": 0, "2": 1, "3": 2}
            data["findings"].sort(key=lambda f: tier_rank.get(str(f.get("tier")), 3))
        else:
            severity_rank = {"safety-critical": 0, "major": 1, "moderate": 2, "minor": 3, "gap": 4}
            data["findings"].sort(key=lambda f: severity_rank.get(f.get("severity", "gap"), 5))

        if data.get("report_type_note"):
            st.info(data["report_type_note"])

        extracted = data.get("extracted_property_details")
        if extracted and any(extracted.values()):
            detail_bits = []
            if extracted.get("age_year_built"):
                detail_bits.append(f"**Built:** {extracted['age_year_built']}")
            if extracted.get("construction_type"):
                detail_bits.append(f"**Construction:** {extracted['construction_type']}")
            if extracted.get("roof_type"):
                detail_bits.append(f"**Roof:** {extracted['roof_type']}")
            if extracted.get("foundation_type"):
                detail_bits.append(f"**Foundation:** {extracted['foundation_type']}")
            if detail_bits:
                st.markdown(
                    '<div style="background-color:#F5F6F7;border-radius:12px;padding:12px 16px;'
                    'margin-bottom:16px;">'
                    '<span style="color:#6B7280;font-size:0.85em;">📐 Extracted from report — ' +
                    " &nbsp;|&nbsp; ".join(detail_bits) +
                    '</span></div>',
                    unsafe_allow_html=True
                )

        verdict = data.get("verdict")
        if verdict:
            rec = verdict.get("recommendation", "")
            color, label = VERDICT_STYLE.get(rec, ("#6B7280", rec.title()))
            negotiation_html = (
                f'<div style="margin-top:8px;"><b>Suggested negotiation figure:</b> '
                f'{verdict.get("negotiation_amount")}</div>'
                if verdict.get("negotiation_amount") else ""
            )
            st.markdown(
                f'<div style="background-color:{color}15;border:2px solid {color};'
                f'border-radius:12px;padding:16px;margin-bottom:16px;">'
                f'<div style="font-size:1.3em;font-weight:700;color:{color};">{label}</div>'
                f'<div style="margin-top:8px;">{verdict.get("reasoning","")}</div>'
                f'{negotiation_html}</div>',
                unsafe_allow_html=True
            )

        st.download_button(
            label="⬇️ Download this review",
            data=build_markdown_report(data, image_registry),
            file_name=f"{uploaded_file.name.rsplit('.', 1)[0]}_review.md",
            mime="text/markdown",
            type="primary"
        )

        st.markdown("## 🎯 Top 3 Priority Actions")
        with st.container(border=True):
            for i, item in enumerate(data.get("top_3_actions", []), start=1):
                action = item.get("action") if isinstance(item, dict) else item
                script = item.get("script") if isinstance(item, dict) else None
                st.markdown(f"**{i}.** {action}")
                if script:
                    st.markdown(f"> 💬 *Say this:* \"{script}\"")

        if data.get("inspector_questions"):
            st.markdown("## 📞 Questions to Ask Your Inspector")
            with st.container(border=True):
                for q in data["inspector_questions"]:
                    st.markdown(f"- {q}")

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
                    photo_id = f.get("source_photo_id")
                    if photo_id and photo_id in image_registry:
                        entry = image_registry[photo_id]
                        st.image(entry["bytes"], caption=f"Page {entry['page']}", use_container_width=True)
                    else:
                        st.markdown(
                            '<div style="background-color:#F5F6F7;border-radius:8px;padding:40px 10px;'
                            'text-align:center;color:#9CA3AF;">No photo for this item</div>',
                            unsafe_allow_html=True
                        )
                with text_col:
                    st.markdown(severity_badge(f["severity"]), unsafe_allow_html=True)
                    if f.get("tier"):
                        st.markdown(f"**Tier {f['tier']}**")
                    st.markdown(f"**{f['description']}**")
                    if f.get("source_quote"):
                        st.markdown(f"> *\"{f['source_quote']}\"*")
                    if f.get("pest_category"):
                        st.markdown(f"🐛 *Pest category: {f['pest_category']}*")
                    if f.get("hedge_language_note"):
                        st.markdown(f"⚠️ *Hedge language check: {f['hedge_language_note']}*")
                    if f.get("regional_risk_note"):
                        st.markdown(f"📍 *Regional risk: {f['regional_risk_note']}*")
                    if f.get("landlord_compliance_note"):
                        st.markdown(f"🏠 *Landlord compliance: {f['landlord_compliance_note']}*")
                    st.markdown(f"**If left 6–12 months:** {f['trajectory_6_12_months']}")
                    st.markdown(f"**If left 3–5 years:** {f['trajectory_3_5_years']}")
                    st.markdown(f"**Indicative cost:** {f['cost_estimate']}")

        st.markdown("## ✅ Checklist Compliance Review")
        st.table(data["checklist_review"])
