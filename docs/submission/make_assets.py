"""Editable source for Proof of Done submission slides and cover; no network.
Run from the repository root: python docs/submission/make_assets.py
Uses ReportLab and Pillow; screenshots are unchanged captures under docs/img/.
"""
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
IMG = HERE.parent / 'img'
W, H = 960, 540
NAVY, PANEL, BLUE, WHITE, MUTED, AMBER = map(HexColor,
    ('#091323', '#111f33', '#60a5fa', '#f1f5f9', '#aabbd2', '#facc83'))

# Explicit text lines avoid hidden auto-fit/crop; content can be edited here.
slides = [
    ('01  /  THE IDEA', 'AI says done. Prove it.',
     ['A passing test is not necessarily', 'a test that guards the requirement.',
      'Proof of Done challenges claims with', 'sandboxed execution and receipts.'],
     'live-home-2026-11-08.jpg', 'Live deployment · captured 8 Nov 2026'),
    ('02  /  THE WORKFLOW', 'From claim to evidence',
     ['Extract requirements and review rules.', 'Verify quotes and cited code/tests.',
      'Run targeted and blind mutation edits.', 'Review candidate guard tests and receipts.'],
     None, 'All agent model calls: Vultr Serverless Inference'),
    ('03  /  INFRASTRUCTURE', 'Real execution. Bounded blast radius.',
     ['Vultr Cloud Compute runs the web app,', 'orchestrator, journal and Docker.',
      'gVisor runsc sandboxes: network none,', 'read-only root, resource caps, teardown.'],
     'live-historical-containment-2026-11-08.jpg', 'Historical baseline receipt · fake canary'),
    ('04  /  RECORDED DEMO', 'What the run actually recorded',
     ['10 model-claimed covered  to  4 PROVEN', '6 WEAK  ·  2 UNTESTED  ·  0 proven rules',
      '4 gap attempts skipped; 1 failure entry.', 'Finished stages do not mean total success.'],
     'live-run-completeness-2026-11-08.jpg', 'Seeded refund-service run · not a benchmark'),
    ('05  /  REVIEW MATTERS', 'Two guards approved. Four rejected.',
     ['REQ-02: late-refund approval boundary.', 'REQ-03: actor, amount, reason, order ID.',
      'RULE-01: generated test assumption failed;', 'rejected, not a confirmed product defect.'],
     'live-rule01-rejected-2026-11-08.jpg', 'Decisions: Craft Agent navigator, delegated review'),
    ('06  /  CONTAINMENT', 'What we observed, not a guarantee',
     ['Historical run: 65 sandbox records destroyed.', 'Fake canary read + DNS lookup were recorded.',
      'Current image separately: 2 bounded tests passed.', 'PID cap: max event 0 to 1; runsc exit 2.'],
     'live-historical-containment-2026-11-08.jpg', 'Historical baseline still; current-image results separate'),
    ('07  /  LIMITATIONS', 'Evidence has a boundary',
     ['Equivalent or off-target edits can survive.', 'Model descriptions may be wrong.',
      'Not a benchmark or compliance certification.', 'Does not prove an absence of defects.'],
     'live-run-verdicts-2026-11-08.jpg', 'Original exports remain unchanged; see supplement'),
    ('08  /  EXPLORE', 'Evidence over assurance.',
     ['Live: 108-61-195-77.sslip.io', 'GitHub: KostiantynBk/proof-of-done',
      'Fresh work inspired by RuleProof and SoloTrace.', 'Review the preserved run and its limitations.'],
     'live-home-recorded-2026-11-08.jpg', 'Vultr Agent Rush · Blast Radius Zero'),
]


def text(c, x, y, message, size, color=WHITE, font='Helvetica', max_width=None):
    c.setFillColor(color)
    c.setFont(font, size)
    if max_width is not None:
        assert c.stringWidth(message, font, size) <= max_width, (message, size)
    c.drawString(x, y, message)


def make_pdf():
    c = canvas.Canvas(str(HERE / 'proof-of-done-slides.pdf'), pagesize=(W, H), pageCompression=1)
    c.setTitle('Proof of Done — Evidence Over Assurance')
    c.setAuthor('Proof of Done')
    for label, title, lines, image, caption in slides:
        c.setFillColor(NAVY); c.rect(0, 0, W, H, fill=1, stroke=0)
        c.setFillColor(BLUE); c.roundRect(46, 480, 10, 10, 3, fill=1, stroke=0)
        text(c, 67, 480, label, 11, BLUE, 'Helvetica-Bold')
        text(c, 46, 425, title, 28, WHITE, 'Helvetica-Bold', 870)
        c.setFillColor(PANEL); c.roundRect(46, 104, 420, 278, 14, fill=1, stroke=0)
        for i, line in enumerate(lines):
            text(c, 67, 340 - i * 44, line, 16, WHITE, max_width=380)
        if image:
            # Actual screenshot only, no UI reconstruction; full image retained.
            c.drawImage(ImageReader(str(IMG / image)), 494, 153, width=420, height=236.25,
                        preserveAspectRatio=True, anchor='c', mask='auto')
        else:
            c.setFillColor(PANEL); c.roundRect(494, 153, 420, 236, 14, fill=1, stroke=0)
            for y, title2, subtitle in [(342, '01  EXTRACT', 'Source-backed items'),
                                        (276, '02  CHALLENGE', 'Sandboxed mutation checks'),
                                        (210, '03  REVIEW', 'Decisions and receipts')]:
                text(c, 520, y, title2, 15, BLUE, 'Helvetica-Bold')
                text(c, 520, y-22, subtitle, 13, MUTED)
        text(c, 494, 124, caption, 10, MUTED, max_width=420)
        text(c, 46, 49, 'PROOF OF DONE  /  EVIDENCE OVER ASSURANCE', 10, MUTED, 'Helvetica-Bold')
        text(c, 887, 49, f'{slides.index((label,title,lines,image,caption))+1:02}', 10, BLUE, 'Helvetica-Bold')
        c.showPage()
    c.save()


def make_cover():
    im = Image.new('RGB', (1920, 1080), '#091323')
    d = ImageDraw.Draw(im)
    regular = 'C:/Windows/Fonts/arial.ttf'; bold = 'C:/Windows/Fonts/arialbd.ttf'
    def font(path, size): return ImageFont.truetype(path, size)
    d.rounded_rectangle((1100, 125, 1810, 955), radius=80, fill='#111f33', outline='#345579', width=4)
    d.rounded_rectangle((1260, 285, 1650, 710), radius=58, fill='#172d4b', outline='#60a5fa', width=6)
    d.line([(1350, 490), (1430, 570), (1565, 415)], fill='#60a5fa', width=28, joint='curve')
    d.text((130, 135), 'VULTR AGENT RUSH  /  BLAST RADIUS ZERO', font=font(bold, 27), fill='#60a5fa')
    d.text((130, 310), 'AI says done.', font=font(bold, 110), fill='#f1f5f9')
    d.text((130, 450), 'Prove it.', font=font(bold, 125), fill='#60a5fa')
    d.text((134, 650), 'Sandboxed execution. Reviewed evidence.', font=font(regular, 34), fill='#b9c8dc')
    d.text((134, 910), 'PROOF OF DONE  /  EVIDENCE OVER ASSURANCE', font=font(bold, 25), fill='#aabbd2')
    im.save(HERE / 'proof-of-done-cover.png', optimize=True)


if __name__ == '__main__':
    make_pdf()
    make_cover()
    print('Created proof-of-done-slides.pdf and proof-of-done-cover.png')
