import os
import django
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from judges.models import UniversalJudge

pdf_path = os.path.abspath('tysa_credentials_and_panels.pdf')

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
        self.drawString(36, 810, 'Telangana Yogasana Association (TYSA) — Official Championship System')
        self.setStrokeColor(colors.HexColor('#CBD5E1'))
        self.setLineWidth(0.5)
        self.line(36, 804, 559, 804)
        # Footer
        self.line(36, 45, 559, 45)
        self.drawString(36, 32, 'CONFIDENTIAL — Strictly For Technical Officials, Chief Judges & Stage Managers Only')
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
    fontSize=17,
    leading=21,
    textColor=colors.HexColor('#0F172A'),
    alignment=1
)

subtitle_style = ParagraphStyle(
    'DocSubtitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=10.5,
    leading=14,
    textColor=colors.HexColor('#0284C7'),
    alignment=1
)

meta_style = ParagraphStyle(
    'DocMeta',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=8,
    leading=11,
    textColor=colors.HexColor('#475569'),
    alignment=1
)

h1_style = ParagraphStyle(
    'Heading1_Custom',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=11,
    leading=14,
    textColor=colors.HexColor('#0F172A'),
    spaceBefore=8,
    spaceAfter=3
)

desc_style = ParagraphStyle(
    'DescStyle',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=7.5,
    leading=10.5,
    textColor=colors.HexColor('#334155'),
    spaceAfter=4
)

th_style = ParagraphStyle(
    'TH',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.white
)

td_style = ParagraphStyle(
    'TD',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=7,
    leading=9,
    textColor=colors.HexColor('#1E293B')
)

td_code_style = ParagraphStyle(
    'TDCode',
    parent=styles['Normal'],
    fontName='Courier-Bold',
    fontSize=7,
    leading=9,
    textColor=colors.HexColor('#0369A1')
)

td_bold_style = ParagraphStyle(
    'TDBold',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=7,
    leading=9,
    textColor=colors.HexColor('#0F172A')
)

story = []

# Title Section
story.append(Paragraph('TELANGANA YOGASANA ASSOCIATION', subtitle_style))
story.append(Spacer(1, 2))
story.append(Paragraph('Official Credentials & Dedicated Panels Directory', title_style))
story.append(Spacer(1, 2))
story.append(Paragraph('Championship: <b>Telangana State Yogasana Championship 2026</b> (Code: <code>TYSA-STATE-2026</code>)', meta_style))
story.append(Paragraph('All Events &bull; Sub Junior Division (Boys &amp; Girls) &bull; Separate Chief Judges &amp; Stage Managers &bull; 5-D Judges', meta_style))
story.append(Spacer(1, 6))

# Section 1: Executive & Administrative Logins
story.append(Paragraph('1. Core Administration & Officials Logins', h1_style))
admin_data = [
    [Paragraph('Role / Official', th_style), Paragraph('Login Email', th_style), Paragraph('Password', th_style), Paragraph('Scope / Function', th_style)],
    [Paragraph('System Admin', td_bold_style), Paragraph('admin@tysa.org', td_code_style), Paragraph('admin123', td_code_style), Paragraph('Full association access, user registry & panel approvals', td_style)],
    [Paragraph('Competition Director', td_bold_style), Paragraph('cd@tysa.org', td_code_style), Paragraph('cd123', td_code_style), Paragraph('Stage push, corrections, master rolls, schedule & draws', td_style)],
    [Paragraph('Jury Official', td_bold_style), Paragraph('jury@tysa.org', td_code_style), Paragraph('judge123', td_code_style), Paragraph('Read-only score audit search across all panels', td_style)],
    [Paragraph('General Stage Desk', td_bold_style), Paragraph('stage@tysa.org', td_code_style), Paragraph('stage123', td_code_style), Paragraph('General stage supervisor / floor liaison', td_style)],
    [Paragraph('Venue Desk', td_bold_style), Paragraph('venue@tysa.org', td_code_style), Paragraph('venue123', td_code_style), Paragraph('Athlete attendance check-in & ID verification', td_style)],
]
t_admin = Table(admin_data, colWidths=[110, 130, 75, 208])
t_admin.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0284C7')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ('TOPPADDING', (0,0), (-1,-1), 2.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
]))
story.append(t_admin)
story.append(Spacer(1, 6))

# Section 2: Dedicated Chief Judges & Stage Managers (Strictly Scoped Access)
story.append(Paragraph('2. Dedicated Chief Judges & Stage Managers (Strictly Scoped Access per Panel)', h1_style))
story.append(Paragraph('Each arena operates independently. Chief Judges review/release only their panel\'s scores; Stage Managers control only their stage\'s athlete queue and live calls.', desc_style))

scoped_data = [
    [Paragraph('Event & Division', th_style), Paragraph('Stage / Arena', th_style), Paragraph('Chief Judge (Login / Pwd)', th_style), Paragraph('Stage Manager (Login / Pwd)', th_style)],
    [
        Paragraph('Traditional - Boys', td_bold_style),
        Paragraph('Arena 1 (Panel #15)', td_style),
        Paragraph('<b>Dr. V. Nageshwar Rao</b><br/><code>chief.trad.m@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Smt. Radhika Nair</b><br/><code>stage.trad.m@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Traditional - Girls', td_bold_style),
        Paragraph('Arena 2 (Panel #21)', td_style),
        Paragraph('<b>Dr. Sunita Reddy</b><br/><code>chief.trad.f@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Sri P. Mohan</b><br/><code>stage.trad.f@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Artistic Single - Boys', td_bold_style),
        Paragraph('Arena 3 (Panel #18)', td_style),
        Paragraph('<b>Sri R. Venkatraman</b><br/><code>chief.art.single.m@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Sri G. Rajesh</b><br/><code>stage.art.single.m@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Artistic Single - Girls', td_bold_style),
        Paragraph('Arena 4 (Panel #22)', td_style),
        Paragraph('<b>Smt. Lakshmi Devi</b><br/><code>chief.art.single.f@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Smt. Anitha Rao</b><br/><code>stage.art.single.f@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Artistic Pair - Boys', td_bold_style),
        Paragraph('Arena 5 (Panel #23)', td_style),
        Paragraph('<b>Judge B. Chandrasekhar</b><br/><code>chief.art.pair.m@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Sri K. Venkat</b><br/><code>stage.art.pair.m@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Artistic Pair - Girls', td_bold_style),
        Paragraph('Arena 6 (Panel #24)', td_style),
        Paragraph('<b>Judge Geetha Rani</b><br/><code>chief.art.pair.f@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Smt. Madhavi Latha</b><br/><code>stage.art.pair.f@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Rhythmic Pair - Boys', td_bold_style),
        Paragraph('Arena 7 (Panel #25)', td_style),
        Paragraph('<b>Sri M. Ramesh</b><br/><code>chief.rhyth.pair.m@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Sri T. Ramesh</b><br/><code>stage.rhyth.pair.m@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
    [
        Paragraph('Rhythmic Pair - Girls', td_bold_style),
        Paragraph('Arena 8 (Panel #26)', td_style),
        Paragraph('<b>Judge Sujatha Varma</b><br/><code>chief.rhyth.pair.f@tysa.org</code> (<code>chief123</code>)', td_style),
        Paragraph('<b>Smt. Shailaja Rani</b><br/><code>stage.rhyth.pair.f@tysa.org</code> (<code>stage123</code>)', td_style)
    ],
]
t_scoped = Table(scoped_data, colWidths=[95, 98, 165, 165])
t_scoped.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0F766E')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ('TOPPADDING', (0,0), (-1,-1), 2.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))
story.append(t_scoped)
story.append(Spacer(1, 6))

# Section 3: Sub Junior Panels Assignment Matrix
story.append(Paragraph('3. Sub Junior Full Judge Panels (Simultaneous Arena Matrix)', h1_style))
story.append(Paragraph('Traditional events feature 5 Difficulty Judges (D1-D5). All 8 panels can execute simultaneously.', desc_style))

panel_matrix = [
    [Paragraph('Event & Arena', th_style), Paragraph('D Judges (Difficulty)', th_style), Paragraph('Timing Judges', th_style), Paragraph('Evaluators', th_style), Paragraph('Artistry Judges', th_style)],
    [
        Paragraph('<b>Traditional Boys</b><br/>Arena 1 (Panel #15)<br/><i>Chief: chief.trad.m</i><br/><i>Stage: stage.trad.m</i>', td_style),
        Paragraph('D1: d1@tysa.org<br/>D2: d2@tysa.org<br/>D3: d3@tysa.org<br/>D4: d4@tysa.org<br/><b>D5: judge10@tysa.org</b>', td_style),
        Paragraph('T1: time@tysa.org', td_style),
        Paragraph('E1: evaluator@tysa.org', td_style),
        Paragraph('<i>Not applicable</i>', td_style)
    ],
    [
        Paragraph('<b>Traditional Girls</b><br/>Arena 2 (Panel #21)<br/><i>Chief: chief.trad.f</i><br/><i>Stage: stage.trad.f</i>', td_style),
        Paragraph('D1: judge09@tysa.org<br/>D2: judge11@tysa.org<br/>D3: judge12@tysa.org<br/>D4: judge13@tysa.org<br/><b>D5: judge14@tysa.org</b>', td_style),
        Paragraph('T1: judge15@tysa.org', td_style),
        Paragraph('E1: judge16@tysa.org', td_style),
        Paragraph('<i>Not applicable</i>', td_style)
    ],
    [
        Paragraph('<b>Artistic Single Boys</b><br/>Arena 3 (Panel #18)<br/><i>Chief: chief.art.single.m</i><br/><i>Stage: stage.art.single.m</i>', td_style),
        Paragraph('D1: judge17@tysa.org<br/>D2: judge18@tysa.org<br/>D3: judge19@tysa.org<br/>D4: judge20@tysa.org', td_style),
        Paragraph('T1: judge21@tysa.org<br/>T2: judge22@tysa.org', td_style),
        Paragraph('E1: judge23@tysa.org', td_style),
        Paragraph('A1: artistry@tysa.org<br/>A2: judge24@tysa.org', td_style)
    ],
    [
        Paragraph('<b>Artistic Single Girls</b><br/>Arena 4 (Panel #22)<br/><i>Chief: chief.art.single.f</i><br/><i>Stage: stage.art.single.f</i>', td_style),
        Paragraph('D1: judge25@tysa.org<br/>D2: judge26@tysa.org<br/>D3: judge27@tysa.org<br/>D4: judge28@tysa.org', td_style),
        Paragraph('T1: judge29@tysa.org<br/>T2: judge30@tysa.org', td_style),
        Paragraph('E1: judge31@tysa.org', td_style),
        Paragraph('A1: judge32@tysa.org<br/>A2: test_judge_temp@tysa.org', td_style)
    ],
    [
        Paragraph('<b>Artistic Pair Boys</b><br/>Arena 5 (Panel #23)<br/><i>Chief: chief.art.pair.m</i><br/><i>Stage: stage.art.pair.m</i>', td_style),
        Paragraph('D1: d1@tysa.org<br/>D2: d2@tysa.org<br/>D3: d3@tysa.org<br/>D4: d4@tysa.org', td_style),
        Paragraph('T1: time@tysa.org<br/>T2: judge10@tysa.org', td_style),
        Paragraph('E1: evaluator@tysa.org<br/>E2: judge12@tysa.org', td_style),
        Paragraph('A1: artistry@tysa.org<br/>A2: judge14@tysa.org', td_style)
    ],
    [
        Paragraph('<b>Artistic Pair Girls</b><br/>Arena 6 (Panel #24)<br/><i>Chief: chief.art.pair.f</i><br/><i>Stage: stage.art.pair.f</i>', td_style),
        Paragraph('D1: judge09@tysa.org<br/>D2: judge11@tysa.org<br/>D3: judge13@tysa.org<br/>D4: judge15@tysa.org', td_style),
        Paragraph('T1: judge16@tysa.org<br/>T2: judge17@tysa.org', td_style),
        Paragraph('E1: judge18@tysa.org<br/>E2: judge19@tysa.org', td_style),
        Paragraph('A1: judge20@tysa.org<br/>A2: judge21@tysa.org', td_style)
    ],
    [
        Paragraph('<b>Rhythmic Pair Boys</b><br/>Arena 7 (Panel #25)<br/><i>Chief: chief.rhyth.pair.m</i><br/><i>Stage: stage.rhyth.pair.m</i>', td_style),
        Paragraph('D1: judge22@tysa.org<br/>D2: judge23@tysa.org<br/>D3: judge24@tysa.org<br/>D4: judge25@tysa.org', td_style),
        Paragraph('T1: judge26@tysa.org<br/>T2: judge27@tysa.org', td_style),
        Paragraph('E1: judge28@tysa.org', td_style),
        Paragraph('A1: judge29@tysa.org<br/>A2: judge30@tysa.org', td_style)
    ],
    [
        Paragraph('<b>Rhythmic Pair Girls</b><br/>Arena 8 (Panel #26)<br/><i>Chief: chief.rhyth.pair.f</i><br/><i>Stage: stage.rhyth.pair.f</i>', td_style),
        Paragraph('D1: judge31@tysa.org<br/>D2: judge32@tysa.org<br/>D3: adminsss@tysa.org<br/>D4: arun.kumar@gmail.com', td_style),
        Paragraph('T1: judge13@tysa.org<br/>T2: judge14@tysa.org', td_style),
        Paragraph('E1: judge15@tysa.org', td_style),
        Paragraph('A1: judge16@tysa.org<br/>A2: judge17@tysa.org', td_style)
    ],
]
t_matrix = Table(panel_matrix, colWidths=[120, 135, 88, 88, 92])
t_matrix.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
    ('VALIGN', (0,0), (-1,-1), 'TOP'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ('TOPPADDING', (0,0), (-1,-1), 2.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))
story.append(t_matrix)

story.append(PageBreak())

# Section 4: Master Judges Credential Directory
story.append(Paragraph('4. Master Official & Judge Credentials Directory', h1_style))
story.append(Paragraph('All universal certified judges and stage managers can log in at <code>http://127.0.0.1:8000/accounts/login/</code>.', desc_style))

judges_qs = UniversalJudge.objects.filter(approval_status='APPROVED').select_related('user').order_by('user__email')

judge_rows = [
    [Paragraph('#', th_style), Paragraph('Official Name', th_style), Paragraph('Login Email', th_style), Paragraph('Password', th_style), Paragraph('Role / Category', th_style), Paragraph('Status', th_style)]
]

idx = 1
for j in judges_qs:
    if not j.user:
        continue
    pwd = 'judge123'
    role_cat = 'Judge'
    if j.user.email == 'admin@tysa.org':
        pwd = 'admin123'
        role_cat = 'Admin'
    elif j.user.email == 'cd@tysa.org':
        pwd = 'cd123'
        role_cat = 'Director'
    elif j.user.email.startswith('chief.'):
        pwd = 'chief123'
        role_cat = 'Chief Judge'
    elif j.user.email.startswith('stage.'):
        pwd = 'stage123'
        role_cat = 'Stage Manager'
    elif j.user.email == 'stage@tysa.org':
        pwd = 'stage123'
        role_cat = 'Stage Manager'
    elif j.user.email == 'venue@tysa.org':
        pwd = 'venue123'
        role_cat = 'Venue Desk'
        
    judge_rows.append([
        Paragraph(str(idx), td_style),
        Paragraph(j.user.full_name or 'Official Judge', td_bold_style),
        Paragraph(j.user.email, td_code_style),
        Paragraph(pwd, td_code_style),
        Paragraph(role_cat, td_style),
        Paragraph('<font color="#15803D"><b>APPROVED</b></font>', td_style)
    ])
    idx += 1

t_judges = Table(judge_rows, colWidths=[18, 155, 160, 70, 70, 50])
t_judges.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#475569')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ('TOPPADDING', (0,0), (-1,-1), 2),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
]))
story.append(t_judges)

doc.build(story, canvasmaker=NumberedCanvas)
print('PDF generated successfully at:', pdf_path)
print('File size:', os.path.getsize(pdf_path), 'bytes')
