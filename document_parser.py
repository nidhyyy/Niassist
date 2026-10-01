"""Bounded document extraction, run by the worker in a disposable subprocess."""
import json
from pathlib import Path
import sys

MAX_CHARS = 300_000
MAX_PAGES = 100


def extract(path, kind):
    if kind == 'txt':
        text = Path(path).read_bytes().decode('utf-8-sig')
        if '\x00' in text:
            raise ValueError('Upload a UTF-8 text file, not a binary file.')
        pages = [(None, text)]
    else:
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError('Password-protected PDFs are not supported.')
        if len(reader.pages) > MAX_PAGES:
            raise ValueError('Use a PDF with no more than 100 pages.')
        pages, length = [], 0
        for number, page in enumerate(reader.pages, 1):
            contents = page.get_contents()
            if contents and len(contents.get_data()) > 8_000_000:
                raise ValueError('A PDF page is too complex to process.')
            text = page.extract_text() or ''
            length += len(text)
            if length > MAX_CHARS:
                raise ValueError('Document exceeds 300,000 extracted characters.')
            pages.append((number, text))
    if sum(len(text) for _, text in pages) > MAX_CHARS:
        raise ValueError('Document exceeds 300,000 extracted characters.')
    chunks = []
    for page, text in pages:
        text = ' '.join(text.split())
        for start in range(0, len(text), 1080):
            segment = text[start:start + 1200]
            if segment.strip():
                chunks.append({'page': page, 'text': segment})
    if not chunks or sum(len(c['text']) for c in chunks) < 20:
        raise ValueError('No readable text found. Upload a text-based PDF or UTF-8 TXT; scanned PDFs need OCR first.')
    return chunks


if __name__ == '__main__':
    # Linux memory ceiling. Windows still gets the parent's wall-clock timeout.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024,) * 2)
    except (ImportError, ValueError, OSError):
        pass
    try:
        result = {'chunks': extract(sys.argv[1], sys.argv[3])}
    except (ValueError, UnicodeDecodeError) as exc:
        result = {'error': str(exc)[:240]}
    except Exception:
        result = {'error': 'Could not read this document. Try a simpler text-based PDF or UTF-8 TXT.'}
    Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
