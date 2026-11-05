from pathlib import Path

from pypdf import PdfReader

reader = PdfReader('/work/input.pdf')
Path('/work/output.txt').write_text('\n\n'.join(page.extract_text() or '' for page in reader.pages), encoding='utf-8')
