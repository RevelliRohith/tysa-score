with open('cop_extracted.txt', 'r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

print(f'Total lines in cop_extracted: {len(lines)}')
# Search for sections mentioning Artistic Pair, Rhythmic Pair, body touch, different asana, etc.
keywords = ['artistic pair', 'rhythmic pair', 'body touch', 'touch', 'different asana', 'same asana', 'pyramid', 'namaste']
matching_pages = []
current_page = None
current_text = []

for line in lines:
    if line.startswith('--- PAGE '):
        if current_page and any(any(kw in l.lower() for kw in keywords) for l in current_text):
            matching_pages.append((current_page, ''.join(current_text)))
        current_page = line.strip()
        current_text = []
    else:
        current_text.append(line)

if current_page and any(any(kw in l.lower() for kw in keywords) for l in current_text):
    matching_pages.append((current_page, ''.join(current_text)))

print(f'Found {len(matching_pages)} matching pages in COP.')
with open('cop_pairs_summary.txt', 'w', encoding='utf-8') as out:
    for page_header, text in matching_pages:
        out.write(f'\n=========================================\n{page_header}\n=========================================\n')
        out.write(text)
print('Wrote cop_pairs_summary.txt')
