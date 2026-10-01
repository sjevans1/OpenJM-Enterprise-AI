"""Create deterministic RAG test fixtures for OpenJM Knowledge/RAG baseline.

Fixtures embed unique, non-ambiguous facts:
  A. Narrative retrieval ........ QALO-7031 revenue figure
  B. Heading-dependent context . HEADING-4421 marker
  C. Chunk-boundary fact ........ BR-7749-Q3 review ID
  D. DOCX table-only value ..... 1425 (financial figure, table cell only)
  E. PDF table-only value ...... 18.5 (ROI percentage, table cell only)
  F. Page-specific context ..... Project Phoenix codename PHOENIX-8822
  G. Adjacent-context statement . "assumes no new regulatory constraints"
  H. Slide-specific context .... Slide 3 only: "CAC ratio is 3.2 to 1"
"""

from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)


def create_long_report_md():
    """A long Markdown report (~2400 chars) for ordinary narrative retrieval (A)
    and heading-dependent context (B)."""
    lines = [
        "# QALO Strategic Insights - Quarterly Report Q3 2024",
        "",
        "## Executive Summary",
        "QALO Strategic Insights was founded in 2018 as a boutique analytics firm",
        "serving emerging markets. In Q3 2024, total revenue reached QALO-7031",
        "million Jamaican dollars, representing a 12% increase over the prior quarter.",
        "The growth was driven primarily by expansion into the Caribbean fintech sector.",
        "",
        "## Market Dynamics",
        "HEADING-4421 marks the start of the market dynamics analysis.",
        "Consumer confidence indices showed a 12% decline in Q2, which directly",
        "informed our revised forecast for Q4. The decline was most pronounced",
        "among first-time kefir buyers in the Kingston metropolitan area.",
        "",
        "## Financial Performance",
        "Revenue by segment for Q3 2024:",
        "- Beverage Operations: JMD 4,500,000",
        "- Analytics Services: JMD 2,531,000",
        "- Distribution Network: JMD 3,200,000",
        "",
        "The forecast assumes no new regulatory constraints will be introduced",
        "before the end of fiscal year 2025. Should new regulations emerge, our",
        "projected margin compression of 3.2% would need to be revisited.",
        "",
        "## Risk Assessment",
        "Primary risk factors include supply chain volatility, competitor",
        "pricing pressure, and potential currency devaluation. The board has",
        "approved a hedging strategy covering 60% of Q4 exposure.",
        "",
        "## Conclusion",
        "QALO remains well-positioned for continued growth through its focus on",
        "data-driven decision making and artisan craftsmanship. The Q4 outlook",
        "is positive, with projected revenue of QALO-7200 million Jamaican dollars.",
    ]
    (FIXTURE_DIR / "long_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_heading_context_md():
    """Markdown with headings critical for disambiguation (B)."""
    lines = [
        "# Project Atlas",
        "",
        "## Team Composition",
        "The Atlas team has 12 engineers and 4 data scientists.",
        "",
        "## Technical Stack",
        "The project uses a microservices architecture deployed on Kubernetes.",
        "",
        "# Project Phoenix",
        "",
        "## Team Composition",
        "The Phoenix team has 8 engineers and 6 data scientists.",
        "",
        "## Technical Stack",
        "The Phoenix project uses a monolithic architecture deployed on VMs.",
        "",
        "# Project Orion",
        "",
        "## Team Composition",
        "The Orion team has 5 engineers and 3 data scientists.",
    ]
    (FIXTURE_DIR / "heading_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_boundary_fact_txt():
    """TXT with a fact placed near the 512-char chunk boundary (C).

    The string BR-7749-Q3 should appear near character offset 505.
    """
    # Build filler text up to ~505 chars, then insert the boundary fact.
    filler = (
        "This document contains general operational metadata for the "
        "quarterly review process. Each review is assigned a unique "
        "identifier that follows the pattern BR-NNNN-QX. "
    )  # ~165 chars
    # Pad to reach ~500 chars
    while len(filler) < 495:
        filler += "X"
    # Insert the boundary fact at ~500
    text = filler[:495] + "The quarterly review ID is BR-7749-Q3." + filler[495:]
    (FIXTURE_DIR / "boundary_fact.txt").write_text(text, encoding="utf-8")


def create_adjacent_context_md():
    """Markdown for adjacency-context test (G).

    The statement about regulatory constraints appears right after a
    ~512-char boundary so that a chunk ending just before it loses the
    context that 'no new regulatory constraints' refers to the forecast.
    """
    lines = [
        "# Forecast Document",
        "",
        "## Q4 Projections",
        "Our Q4 2024 forecast model incorporates three key assumptions.",
    ]
    # Pad to push the key statement past offset 500
    padding = " " * 400
    lines.append("")
    lines.append(padding)
    lines.append("The forecast assumes no new regulatory constraints will be introduced")
    lines.append("before the end of fiscal year 2025.")
    lines.append("")
    lines.append("## Sensitivity Analysis")
    lines.append("If regulations change, the model will need to be recalibrated.")
    (FIXTURE_DIR / "adjacent_context.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_financial_table_docx():
    """DOCX with paragraph text AND a table where 1425 appears only in a cell (D)."""
    from docx import Document

    doc = Document()
    doc.add_heading("Q3 2024 Financial Summary", level=1)
    doc.add_paragraph(
        "The following financial highlights cover the period January through "
        "September 2024. Revenue exceeded expectations across all segments."
    )
    doc.add_heading("Quarterly Revenue", level=2)
    doc.add_paragraph("Revenue by division for Q3 2024 is provided in the table below.")

    table = doc.add_table(rows=4, cols=3)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "Division"
    hdr[1].text = "Revenue (millions JMD)"
    hdr[2].text = "YoY Growth"

    row1 = table.rows[1].cells
    row1[0].text = "Beverage Operations"
    row1[1].text = "4,500"  # also appears in long_report.md paragraph text
    row1[2].text = "12%"

    row2 = table.rows[2].cells
    row2[0].text = "Analytics Services"
    row2[1].text = "1,425"  # ONLY in table cell; unique value 1425
    row2[2].text = "18%"

    row3 = table.rows[3].cells
    row3[0].text = "Distribution"
    row3[1].text = "3,200"
    row3[2].text = "5%"

    doc.add_paragraph("Note: all figures are preliminary and subject to audit.")
    doc.save(str(FIXTURE_DIR / "financial_table.docx"))


def create_financial_table_pdf():
    """Single-page PDF with a table where 18.5 appears only in a cell (E).

    Uses fpdf2 so the resulting PDF is valid and extractable by pdfplumber,
    which is the engine DB-GPT uses for PDF ingestion.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)

    table_x = 100
    table_y_top = 520
    table_y = 400
    col_w = 150
    row_h = 30
    cols = 3

    headers = ["Product Line", "Projected ROI", "Confidence"]
    data_rows = [
        ["Kefir Gold", "18.5%", "High"],
        ["Kefir Strawberry", "15.2%", "Medium"],
        ["Kefir Mango", "12.8%", "Medium"],
    ]

    # Draw table border rectangles
    pdf.set_draw_color(0, 0, 0)
    for r in range(4):
        for c in range(cols):
            rx = table_x + c * col_w
            ry = table_y - r * row_h
            pdf.rect(rx, ry, col_w, row_h)

    # Write cell text using pdf.text() (produces extractable Tj operators)
    for c, h in enumerate(headers):
        pdf.text(table_x + 12, table_y_top - 12, h)

    for r, row in enumerate(data_rows):
        cy = table_y - (r + 1) * row_h
        for c, cell in enumerate(row):
            pdf.text(table_x + c * col_w + 12, cy - 12, cell)

    # Narrative text - intentionally does NOT mention 18.5
    pdf.text(100, 720, "Q3 2024 Projected ROI by Product Line")
    pdf.text(100, 690, "This report covers the Q3 2024 ROI projections.")
    pdf.text(100, 660, "All figures are preliminary and subject to change.")

    pdf.output(str(FIXTURE_DIR / "financial_table.pdf"))


def create_multi_page_pdf():
    """Three-page PDF where PHOENIX-8822 appears only on page 2 (F).

    Uses fpdf2 for valid, pdfplumber-readable PDFs.
    """
    from fpdf import FPDF

    pdf = FPDF()

    page1_lines = [
        "QALO Strategic Insights - Multi-Quarter Report",
        "Page 1: Overview",
        "This document summarises key initiatives across Q1-Q4 2024.",
        "The organisation has 15 major projects under way.",
        "Each project has a codename and a dedicated team.",
        "Revenue for Q1 was JMD 2,300,000.",
        "Revenue for Q2 was JMD 2,800,000.",
        "Revenue for Q3 was JMD 3,200,000.",
    ]

    page2_lines = [
        "Page 2: Project Details",
        "Project codenames and statuses:",
        "- Project Atlas: Phase 2 complete",
        "- Project PHOENIX-8822: In active development",
        "- Project Orion: Planning phase",
        "The PHOENIX-8822 initiative will launch in Q1 2025.",
        "It targets the Kingston metropolitan market.",
    ]

    page3_lines = [
        "Page 3: Appendices",
        "Appendix A: Glossary",
        "Appendix B: Contact List",
        "Appendix C: Change Log",
        "No project codenames are listed in this appendix.",
        "For project details see pages 1 and 2.",
    ]

    for page_lines in [page1_lines, page2_lines, page3_lines]:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        y = 720
        for line in page_lines:
            pdf.text(100, y, line)
            y -= 30

    pdf.output(str(FIXTURE_DIR / "multi_page_report.pdf"))


def create_slide_context_pptx():
    """PPTX with 4 slides; slide 3 has CAC ratio value 3.2 (H)."""
    from pptx import Presentation
    from pptx.util import Inches

    pr = Presentation()

    # Slide 1 - title
    s1 = pr.slides.add_slide(pr.slide_layouts[6])
    txb1 = s1.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(9), Inches(0.7))
    txb1.text = "Q4 2024 Forecast Review"
    body1 = s1.shapes.add_textbox(Inches(1), Inches(1.8), Inches(8), Inches(5))
    body1.text = "This presentation covers our Q4 projections and assumptions."

    # Slide 2 - overview
    s2 = pr.slides.add_slide(pr.slide_layouts[6])
    txb2 = s2.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(9), Inches(0.7))
    txb2.text = "Revenue Overview"
    body2 = s2.shapes.add_textbox(Inches(1), Inches(1.8), Inches(8), Inches(5))
    body2.text = "Total Q4 revenue is projected at JMD 3,500,000."

    # Slide 3 - CAC ratio (unique fact)
    s3 = pr.slides.add_slide(pr.slide_layouts[6])
    txb3 = s3.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(9), Inches(0.7))
    txb3.text = "Customer Acquisition Cost"
    body3 = s3.shapes.add_textbox(Inches(1), Inches(1.8), Inches(8), Inches(5))
    body3.text = "The CAC ratio is 3.2 to 1, meaning we spend JMD 3.20 for every JMD 1.00 in new revenue."

    # Slide 4 - conclusion
    s4 = pr.slides.add_slide(pr.slide_layouts[6])
    txb4 = s4.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(9), Inches(0.7))
    txb4.text = "Conclusion"
    body4 = s4.shapes.add_textbox(Inches(1), Inches(1.8), Inches(8), Inches(5))
    body4.text = "We recommend proceeding with the Q4 plan as outlined."

    pr.save(str(FIXTURE_DIR / "slide_context.pptx"))


def create_phoenix_protocol_md():
    """Phoenix launch protocol document for Gate C acceptance.

    Contains a unique test fact: PRIMARY launch sequence code 7-3-9-2-5.
    """
    lines = [
        "# PHOENIX Launch Protocol - Internal Distribution Only",
        "",
        "## Document Control",
        "Document ID: PHOENIX-2024",
        "Classification: Internal",
        "Version: 1.0",
        "",
        "## Launch Sequence Overview",
        "The PRIMARY launch sequence code for Project Phoenix is 7-3-9-2-5.",
        "",
        "This code must be validated at each stage gate before proceeding",
        "with product release activities. The sequence is irreversible once",
        "executed and cannot be modified in the field.",
        "",
        "## Safety Protocol",
        "All team members must verify the launch sequence code against the",
        "master checklist stored in the secure vault. Under no circumstances",
        "should the code be shared outside the authorised launch team.",
        "",
        "## Activation Criteria",
        "1. All seven quality gates must pass.",
        "2. The regional distribution network must be confirmed ready.",
        "3. Customer support must be staffed and trained.",
        "4. The marketing embargo is lifted at T+0 hours.",
        "",
        "## Contact",
        "Launch Director: ops@qalo.io",
        "Emergency: 911",
    ]
    (FIXTURE_DIR / "phoenix_launch_protocol.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def create_all():
    create_long_report_md()
    create_heading_context_md()
    create_boundary_fact_txt()
    create_adjacent_context_md()
    create_financial_table_docx()
    create_financial_table_pdf()
    create_multi_page_pdf()
    create_slide_context_pptx()
    create_phoenix_protocol_md()
    print("All fixtures created in:", FIXTURE_DIR)
    for f in sorted(FIXTURE_DIR.iterdir()):
        if f.is_file():
            print(f"  {f.name}  ({f.stat().st_size} bytes)")


if __name__ == "__main__":
    create_all()
