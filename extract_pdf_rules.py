import pypdf
import os

def dump_matches(path, keywords, outfile):
    print(f'=== Searching {path} ===')
    if not os.path.exists(path):
        print(f'File not found: {path}')
        return
    reader = pypdf.PdfReader(path)
    print(f'Total pages: {len(reader.pages)}')
    with open(outfile, 'w', encoding='utf-8') as out:
        out.write(f'=== SEARCH RESULTS FOR {path} ===\n\n')
        for idx, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            matched = [kw for kw in keywords if kw.lower() in text.lower()]
            if matched:
                out.write(f'--- PAGE {idx+1} (Matched: {", ".join(matched)}) ---\n')
                out.write(text + '\n\n')

dump_matches(r'C:\Users\My Files\Desktop\calculation Requirements.pdf', 
             ['artistic pair', 'rhythmic pair', 'pair', 'formation', 'touch', 'difficulty', 'evaluator', 'hold', 'execution'], 
             r'calc_req_extracted.txt')

dump_matches(r'C:\Users\My Files\Downloads\YB+COP+2025-26 (1).pdf', 
             ['artistic pair', 'rhythmic pair', 'kalatmak', 'talatmak', 'body touch', 'pyramid', 'formation', 'pair', 'touch'], 
             r'cop_extracted.txt')

print('Extraction complete!')
