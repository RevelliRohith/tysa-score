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

pdf_path = os.path.abspath('tysa_complete_panels_and_credentials.pdf')

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
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont('Helvetica-Bold', 7.5)
        self.setFillColor(colors.HexColor('#0284C7'))
        self.drawString(36, 812, 'TELANGANA YOGASANA ASSOCIATION (TYSA)')
        self.setFont('Helvetica', 7.5)
        self.setFillColor(colors.HexColor('#64748B'))
        self.drawRightString(559, 812, 'Official Championship Technical Panels & Login Dossier')
        self.setStrokeColor(colors.HexColor('#CBD5E1'))
        self.setLineWidth(0.5)
        self.line(36, 806, 559, 806)
        
        # Footer
        self.line(36, 38, 559, 38)
        self.setFont('Helvetica', 7.5)
        self.setFillColor(colors.HexColor('#64748B'))
        self.drawString(36, 26, 'CONFIDENTIAL — Official Roster with Assigned Desks, Usernames & Passwords')
        page_text = f'Page {self._pageNumber} of {page_count}'
        self.drawRightString(559, 26, page_text)
        self.restoreState()

doc = SimpleDocTemplate(
    pdf_path,
    pagesize=A4,
    leftMargin=36,
    rightMargin=36,
    topMargin=46,
    bottomMargin=46
)

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'DocTitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=16,
    leading=20,
    textColor=colors.HexColor('#0F172A'),
    alignment=1
)

subtitle_style = ParagraphStyle(
    'DocSubtitle',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=10,
    leading=13,
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

panel_header_style = ParagraphStyle(
    'PanelHeader',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=10.5,
    leading=14,
    textColor=colors.white
)

panel_sub_style = ParagraphStyle(
    'PanelSub',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=7.5,
    leading=10,
    textColor=colors.HexColor('#E2E8F0')
)

th_style = ParagraphStyle(
    'TH',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.white
)

td_role_style = ParagraphStyle(
    'TDRole',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.HexColor('#0F172A')
)

td_name_style = ParagraphStyle(
    'TDName',
    parent=styles['Normal'],
    fontName='Helvetica',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.HexColor('#1E293B')
)

td_code_style = ParagraphStyle(
    'TDCode',
    parent=styles['Normal'],
    fontName='Courier-Bold',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.HexColor('#0369A1')
)

td_pwd_style = ParagraphStyle(
    'TDPwd',
    parent=styles['Normal'],
    fontName='Courier-Bold',
    fontSize=7.5,
    leading=9.5,
    textColor=colors.HexColor('#B45309')
)

td_badge_style = ParagraphStyle(
    'TDBadge',
    parent=styles['Normal'],
    fontName='Helvetica-Bold',
    fontSize=7,
    leading=9,
    textColor=colors.HexColor('#15803D')
)

story = []

# Title Banner
story.append(Paragraph('TELANGANA YOGASANA ASSOCIATION', subtitle_style))
story.append(Spacer(1, 2))
story.append(Paragraph('Sub Junior Panels & Judge Credentials Dossier', title_style))
story.append(Spacer(1, 2))
story.append(Paragraph('Championship: <b>Telangana State Yogasana Championship 2026</b> (<code>TYSA-STATE-2026</code>)', meta_style))
story.append(Paragraph('All 8 Arenas &bull; Dedicated Chief Judges &bull; Dedicated Stage Managers &bull; 5 Full Difficulty Judges (D1 to D5)', meta_style))
story.append(Spacer(1, 8))

# Central Administrative Logins
admin_box_header = [
    [Paragraph('<b>Core Association Leadership &amp; Central Officials</b>', ParagraphStyle('AH', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, textColor=colors.white)),
     Paragraph('Login URL: <code>http://127.0.0.1:8000/accounts/login/</code>', ParagraphStyle('AL', parent=styles['Normal'], fontName='Helvetica', fontSize=7.5, textColor=colors.HexColor('#E2E8F0'), alignment=2))]
]
t_ah = Table(admin_box_header, colWidths=[320, 203])
t_ah.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#0284C7')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ('TOPPADDING', (0,0), (-1,-1), 2.5),
]))
story.append(t_ah)

admin_data = [
    [Paragraph('Role / Designation', th_style), Paragraph('Official Name', th_style), Paragraph('Username / Email', th_style), Paragraph('Password', th_style), Paragraph('Operational Authority', th_style)],
    [Paragraph('System Admin', td_role_style), Paragraph('Dr. K. Srinivas Rao', td_name_style), Paragraph('admin@tysa.org', td_code_style), Paragraph('admin123', td_pwd_style), Paragraph('Full access, users, approvals & config', td_name_style)],
    [Paragraph('Competition Director', td_role_style), Paragraph('Prof. Ananya Sharma', td_name_style), Paragraph('cd@tysa.org', td_code_style), Paragraph('cd123', td_pwd_style), Paragraph('Stage push, draws, corrections & schedule', td_name_style)],
    [Paragraph('Jury Member', td_role_style), Paragraph('Sri B. Chandrasekhar', td_name_style), Paragraph('jury@tysa.org', td_code_style), Paragraph('judge123', td_pwd_style), Paragraph('Read-only score audit search across all panels', td_name_style)],
    [Paragraph('General Stage Desk', td_role_style), Paragraph('Smt. Radhika Nair', td_name_style), Paragraph('stage@tysa.org', td_code_style), Paragraph('stage123', td_pwd_style), Paragraph('Central floor coordination & announcements', td_name_style)],
    [Paragraph('Venue Clearance Desk', td_role_style), Paragraph('Smt. Kavitha Reddy', td_name_style), Paragraph('venue@tysa.org', td_code_style), Paragraph('venue123', td_pwd_style), Paragraph('Athlete attendance check-in & verification', td_name_style)],
]
t_admin = Table(admin_data, colWidths=[95, 105, 128, 65, 130])
t_admin.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0369A1')),
    ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ('TOPPADDING', (0,0), (-1,-1), 2.5),
    ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
    ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))
story.append(t_admin)
story.append(Spacer(1, 10))

# Clear, consistent seat descriptions: D5 is an equal, full Difficulty & Execution judge just like D1, D2, D3, D4
SEAT_TITLES = {
    'chief_judge': 'Chief Judge (Score Review & Provisional Release)',
    'stage_manager': 'Stage Manager (Queue, Call & Batch Push)',
    'd1_judge': 'D1 Judge (Difficulty & Execution)',
    'd2_judge': 'D2 Judge (Difficulty & Execution)',
    'd3_judge': 'D3 Judge (Difficulty & Execution)',
    'd4_judge': 'D4 Judge (Difficulty & Execution)',
    'd5_judge': 'D5 Judge (Difficulty & Execution)',
    'time_judge': 'Time Judge 1 (Hold & Routine Timers)',
    'time_judge_2': 'Time Judge 2 (Routine Music / Duration Sync)',
    'evaluator': 'Evaluator 1 (Declared vs Observed Asanas)',
    'evaluator_2': 'Evaluator 2 (Pair Synchronization & Declarations)',
    'artistry_judge': 'Artistry Judge 1 (Choreography, Music & Transition)',
    'artistry_judge_2': 'Artistry Judge 2 (Synchronization & Floor Pattern)',
    'jury_member': 'Jury Member (Panel Oversight & Appeal Verification)',
}

def get_password_for_email(email):
    if email == 'admin@tysa.org':
        return 'admin123'
    if email == 'cd@tysa.org':
        return 'cd123'
    if email.startswith('chief.'):
        return 'chief123'
    if email.startswith('stage.'):
        return 'stage123'
    if email == 'stage@tysa.org':
        return 'stage123'
    if email == 'venue@tysa.org':
        return 'venue123'
    return 'judge123'

panels = list(JudgePanel.objects.filter(age_category_id=1).order_by('id'))

for i, p in enumerate(panels):
    panel_title = f"{p.stage_name} &bull; Panel #{p.id}"
    panel_meta = f"Event: <b>{p.event_config.get_event_type_display()}</b> &bull; Category: <b>Sub Junior (10–14 yrs)</b> &bull; Gender: <b>{'Boys' if p.gender == 'M' else 'Girls'}</b> &bull; Round: <b>Preliminary</b> &bull; Status: <b>ACTIVE</b>"

    header_table = Table([
        [Paragraph(panel_title, panel_header_style)],
        [Paragraph(panel_meta, panel_sub_style)]
    ], colWidths=[523])
    
    header_color = colors.HexColor('#0F766E') if p.gender == 'M' else colors.HexColor('#7E22CE')
    
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), header_color),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
    ]))

    roster_rows = [
        [Paragraph('Assigned Seat / Role', th_style), Paragraph('Assigned Official', th_style), Paragraph('Username / Email', th_style), Paragraph('Password', th_style), Paragraph('Desk Status', th_style)]
    ]

    ordered_seats = ['chief_judge', 'stage_manager']
    for s in p.required_seats:
        if s not in ordered_seats:
            ordered_seats.append(s)
    if p.jury_member and 'jury_member' not in ordered_seats:
        ordered_seats.append('jury_member')

    for seat in ordered_seats:
        u = getattr(p, seat)
        if not u:
            continue
        role_title = SEAT_TITLES.get(seat, seat.replace('_', ' ').title())
        pwd = get_password_for_email(u.email)
        
        is_chief_or_stage = seat in ('chief_judge', 'stage_manager')
        name_p = Paragraph(f"<b>{u.full_name}</b>" if is_chief_or_stage else (u.full_name or 'Official Judge'), td_name_style)
        
        roster_rows.append([
            Paragraph(role_title, td_role_style),
            name_p,
            Paragraph(u.email, td_code_style),
            Paragraph(pwd, td_pwd_style),
            Paragraph('READY &bull; ACTIVE', td_badge_style)
        ])

    roster_table = Table(roster_rows, colWidths=[155, 125, 128, 55, 60])
    roster_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.2),
        ('TOPPADDING', (0,0), (-1,-1), 2.2),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.HexColor('#F8FAFC'), colors.white]),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
]))

    panel_flowable = KeepTogether([
        header_table,
        roster_table,
        Spacer(1, 8)
    ])

    story.append(panel_flowable)
    if i in (1, 3, 5):
        story.append(PageBreak())

doc.build(story, canvasmaker=NumberedCanvas)
print('PDF updated successfully at:', pdf_path)
print('File size:', os.path.getsize(pdf_path), 'bytes')
