import os
import django
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from competition.models import JudgePanel

pdf_path = os.path.abspath('tysa_stage_managers_directory.pdf')

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.saveState()
        self.setFont('Helvetica', 8)
        self.setFillColor(colors.HexColor('#64748B'))
        # Header
        self.drawString(36, 810, 'Telangana Yogasana Association (TYSA) — Stage Operations & Floor Management')
        self.setStrokeColor(colors.HexColor('#CBD5E1'))
        self.setLineWidth(0.5)
        self.line(36, 804, 559, 804)
        # Footer
        self.line(36, 45, 559, 45)
        self.drawString(36, 32, 'CONFIDENTIAL — For Stage Managers, Competition Directors & Floor Marshals Only')
        page_text = f'Page {self._pageNumber} of {page_count}'
        self.drawRightString(559, 32, page_text)
        self.restoreState()

doc = SimpleDocTemplate(
    pdf_path,
    pagesize=A4,
    leftMargin=36,
    rightMargin=36,
    topMargin=46,
    bottomMargin=52
)

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'DocTitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=18,
    leading=22,
    textColor=colors.HexColor('#0F172A'),
    alignment=1
)

subtitle_style = ParagraphStyle(
    'DocSubtitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=11,
    leading=15,
    textColor=colors.HexColor('#0284C7'),
    alignment=1
)

meta_style = ParagraphStyle(
    'DocMeta',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=8.5,
    leading=12,
    textColor=colors.HexColor('#475569'),
    alignment=1
)

h1_style = ParagraphStyle(
    'Heading1_Custom',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=12,
    leading=16,
    textColor=colors.HexColor('#0F172A'),
    spaceBefore=12,
    spaceAfter=4
)

desc_style = ParagraphStyle(
    'DescStyle',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=8.5,
    leading=12.5,
    textColor=colors.HexColor('#334155'),
    spaceAfter=6
)

th_style = ParagraphStyle(
    'TH',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=8,
    leading=10,
    textColor=colors.white
)

td_style = ParagraphStyle(
    'TD',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=7.5,
    leading=10,
    textColor=colors.HexColor('#1E293B')
)

td_code_style = ParagraphStyle(
    'TDCode',
    parent=styles['Normal'],
    fontName='Courier-Bold',
    fontSize=8,
    leading=10,
    textColor=colors.HexColor('#0369A1')
)

td_bold_style = ParagraphStyle(
    'TDBold',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=8,
    leading=10.5,
    textColor=colors.HexColor('#0F172A')
)

story = []

# Title Section
story.append(Paragraph('TELANGANA YOGASANA ASSOCIATION', subtitle_style))
story.append(Spacer(1, 3))
story.append(Paragraph('Stage Managers Directory & Credentials Dossier', title_style))
story.append(Spacer(1, 3))
story.append(Paragraph('Championship: <b>Telangana State Yogasana Championship 2026</b> (<code>TYSA-STATE-2026</code>)', meta_style))
story.append(Paragraph('Sub Junior Division &bull; Dedicated Stage Managers for Every Event &amp; Arena &bull; Strictly Scoped Controls', meta_style))
story.append(Spacer(1, 12))

# Section 1: Overview and Operating Rules
story.append(Paragraph('1. Stage Manager Operational Role & Access Scope', h1_style))
story.append(Paragraph(
    'Each Stage Manager is strictly provisioned with dedicated access restricted to their assigned Arena & Panel. '
    'Stage Managers control athlete calling, roll calls (Present, Absent, Hold, Resume), batch push to active judging desks, '
    'item-wise judge progress tracking, and audience second-screen sync. Accessing other arenas is strictly locked out.',
    desc_style
))
story.append(Spacer(1, 4))

# Section 2: Dedicated Stage Managers Table
story.append(Paragraph('2. Dedicated Stage Managers & Arena Allocations', h1_style))

panels_data = [
    [Paragraph('Arena / Stage', th_style), Paragraph('Event & Gender', th_style), Paragraph('Stage Manager Name', th_style), Paragraph('Login Email', th_style), Paragraph('Password', th_style), Paragraph('Panel ID', th_style)],
    [
        Paragraph('<b>Arena 1</b><br/>Stage 1 (Main Arena)', td_style),
        Paragraph('Traditional Yogasana<br/><b>Sub Junior Boys</b>', td_style),
        Paragraph('Smt. Radhika Nair', td_bold_style),
        Paragraph('stage.trad.m@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#15', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 2</b><br/>Stage 2 (North Hall)', td_style),
        Paragraph('Traditional Yogasana<br/><b>Sub Junior Girls</b>', td_style),
        Paragraph('Sri P. Mohan', td_bold_style),
        Paragraph('stage.trad.f@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#21', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 3</b><br/>Stage 3 (Podium Arena)', td_style),
        Paragraph('Artistic Yogasana Single<br/><b>Sub Junior Boys</b>', td_style),
        Paragraph('Sri G. Rajesh', td_bold_style),
        Paragraph('stage.art.single.m@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#18', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 4</b><br/>Stage 4 (South Hall)', td_style),
        Paragraph('Artistic Yogasana Single<br/><b>Sub Junior Girls</b>', td_style),
        Paragraph('Smt. Anitha Rao', td_bold_style),
        Paragraph('stage.art.single.f@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#22', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 5</b><br/>Stage 5 (West Arena)', td_style),
        Paragraph('Artistic Yogasana Pair<br/><b>Sub Junior Boys</b>', td_style),
        Paragraph('Sri K. Venkat', td_bold_style),
        Paragraph('stage.art.pair.m@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#23', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 6</b><br/>Stage 6 (East Arena)', td_style),
        Paragraph('Artistic Yogasana Pair<br/><b>Sub Junior Girls</b>', td_style),
        Paragraph('Smt. Madhavi Latha', td_bold_style),
        Paragraph('stage.art.pair.f@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#24', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 7</b><br/>Stage 7 (Central Hall)', td_style),
        Paragraph('Rhythmic Yogasana Pair<br/><b>Sub Junior Boys</b>', td_style),
        Paragraph('Sri T. Ramesh', td_bold_style),
        Paragraph('stage.rhyth.pair.m@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#25', td_bold_style)
    ],
    [
        Paragraph('<b>Arena 8</b><br/>Stage 8 (Pavilion Arena)', td_style),
        Paragraph('Rhythmic Yogasana Pair<br/><b>Sub Junior Girls</b>', td_style),
        Paragraph('Smt. Shailaja Rani', td_bold_style),
        Paragraph('stage.rhyth.pair.f@tysa.org', td_code_style),
        Paragraph('stage123', td_code_style),
        Paragraph('#26', td_bold_style)
    ],
]

t_panels = Table(panels_data, colWidths=[90, 105, 115, 128, 55, 30])
t_panels.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F766E')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 5),
    ('TOPPADDING', (0,0), (-1,-1), 5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))
story.append(t_panels)
story.append(Spacer(1, 14))

# Section 3: Arena Roster & Stage Management Matrix
story.append(Paragraph('3. Arena Coordination Matrix (Chief Judge & Stage Manager Pairing)', h1_style))
story.append(Paragraph('For seamless arena coordination, each Stage Manager works directly with a dedicated event Chief Judge as paired below:', desc_style))

matrix_data = [
    [Paragraph('Arena / Panel', th_style), Paragraph('Stage Manager (Queue & Stage Push)', th_style), Paragraph('Chief Judge (Score Review & Release)', th_style), Paragraph('Traditional D-Count / Judges', th_style)],
    [
        Paragraph('<b>Arena 1 (Panel #15)</b><br/>Traditional Boys', td_style),
        Paragraph('<b>Smt. Radhika Nair</b><br/><code>stage.trad.m@tysa.org</code>', td_style),
        Paragraph('<b>Dr. V. Nageshwar Rao</b><br/><code>chief.trad.m@tysa.org</code>', td_style),
        Paragraph('<b>5 D Judges</b> (D1-D5)<br/>+ Time, Evaluator, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 2 (Panel #21)</b><br/>Traditional Girls', td_style),
        Paragraph('<b>Sri P. Mohan</b><br/><code>stage.trad.f@tysa.org</code>', td_style),
        Paragraph('<b>Dr. Sunita Reddy</b><br/><code>chief.trad.f@tysa.org</code>', td_style),
        Paragraph('<b>5 D Judges</b> (D1-D5)<br/>+ Time, Evaluator, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 3 (Panel #18)</b><br/>Artistic Single Boys', td_style),
        Paragraph('<b>Sri G. Rajesh</b><br/><code>stage.art.single.m@tysa.org</code>', td_style),
        Paragraph('<b>Sri R. Venkatraman</b><br/><code>chief.art.single.m@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, Evaluator, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 4 (Panel #22)</b><br/>Artistic Single Girls', td_style),
        Paragraph('<b>Smt. Anitha Rao</b><br/><code>stage.art.single.f@tysa.org</code>', td_style),
        Paragraph('<b>Smt. Lakshmi Devi</b><br/><code>chief.art.single.f@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, Evaluator, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 5 (Panel #23)</b><br/>Artistic Pair Boys', td_style),
        Paragraph('<b>Sri K. Venkat</b><br/><code>stage.art.pair.m@tysa.org</code>', td_style),
        Paragraph('<b>Judge B. Chandrasekhar</b><br/><code>chief.art.pair.m@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, 2 Eval, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 6 (Panel #24)</b><br/>Artistic Pair Girls', td_style),
        Paragraph('<b>Smt. Madhavi Latha</b><br/><code>stage.art.pair.f@tysa.org</code>', td_style),
        Paragraph('<b>Judge Geetha Rani</b><br/><code>chief.art.pair.f@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, 2 Eval, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 7 (Panel #25)</b><br/>Rhythmic Pair Boys', td_style),
        Paragraph('<b>Sri T. Ramesh</b><br/><code>stage.rhyth.pair.m@tysa.org</code>', td_style),
        Paragraph('<b>Sri M. Ramesh</b><br/><code>chief.rhyth.pair.m@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, 1 Eval, Jury', td_style)
    ],
    [
        Paragraph('<b>Arena 8 (Panel #26)</b><br/>Rhythmic Pair Girls', td_style),
        Paragraph('<b>Smt. Shailaja Rani</b><br/><code>stage.rhyth.pair.f@tysa.org</code>', td_style),
        Paragraph('<b>Judge Sujatha Varma</b><br/><code>chief.rhyth.pair.f@tysa.org</code>', td_style),
        Paragraph('4 D Judges + 2 Artistry<br/>+ 2 Time, 1 Eval, Jury', td_style)
    ],
]

t_coord = Table(matrix_data, colWidths=[105, 140, 140, 138])
t_coord.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
    ('TOPPADDING', (0,0), (-1,-1), 4.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))
story.append(t_coord)
story.append(Spacer(1, 14))

# Section 4: General Supervisory & Operational Desks
story.append(Paragraph('4. Central Floor Direction & Supervision', h1_style))
super_data = [
    [Paragraph('Role / Designation', th_style), Paragraph('Login Email', th_style), Paragraph('Password', th_style), Paragraph('Operational Authority', th_style)],
    [Paragraph('Competition Director (CD)', td_bold_style), Paragraph('cd@tysa.org', td_code_style), Paragraph('cd123', td_code_style), Paragraph('Universal stage override, schedule changes & panel management', td_style)],
    [Paragraph('Central Floor Supervisor', td_bold_style), Paragraph('stage@tysa.org', td_code_style), Paragraph('stage123', td_code_style), Paragraph('General arena announcer, floor runner liaison & audio-visual sync', td_style)],
    [Paragraph('System Administrator', td_bold_style), Paragraph('admin@tysa.org', td_code_style), Paragraph('admin123', td_code_style), Paragraph('Server infrastructure, hardware connectivity & system health', td_style)],
]
t_super = Table(super_data, colWidths=[120, 130, 75, 198])
t_super.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0284C7')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ('TOPPADDING', (0,0), (-1,-1), 4),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
]))
story.append(t_super)

doc.build(story, canvasmaker=NumberedCanvas)
print('Stage Managers PDF generated successfully at:', pdf_path)
print('File size:', os.path.getsize(pdf_path), 'bytes')
