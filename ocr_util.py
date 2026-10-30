import asyncio
from playwright.async_api import async_playwright
import base64
import sys

async def ocr(img_path):
    with open(img_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        html = f"""
        <!DOCTYPE html>
        <html>
        <head><script src="https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js"></script></head>
        <body>
            <img id="img" src="data:image/png;base64,{data}" />
            <script>
                Tesseract.recognize(document.getElementById('img').src, 'eng').then(({{ data: {{ text }} }}) => {{
                    window.ocrResult = text;
                }}).catch(err => {{ window.ocrResult = 'ERROR: ' + err; }});
            </script>
        </body>
        </html>
        """
        await page.set_content(html)
        await page.wait_for_function('window.ocrResult !== undefined', timeout=60000)
        res = await page.evaluate('window.ocrResult')
        print(f"=== OCR: {img_path} ===")
        print(res)
        await browser.close()

if __name__ == '__main__':
    img = sys.argv[1]
    asyncio.run(ocr(img))
