const fs = require('fs');
const path = require('path');

const jsonPath = 'C:/Users/My Files/Downloads/asanas.json';
const jsonData = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));

const extractedDir = 'C:/Users/My Files/Desktop/tysa-score/public/asanas_extracted/Asanas';
const extractedFiles = fs.readdirSync(extractedDir);

console.log('Total extracted files:', extractedFiles.length);

const targetDir = 'C:/Users/My Files/Desktop/tysa-score/public/asanas';
if (!fs.existsSync(targetDir)) {
  fs.mkdirSync(targetDir, { recursive: true });
}

let directMatchCount = 0;
let codeMatchCount = 0;
let missingCount = 0;
const report = [];

// Create map of lower code to filename
const codeToFile = new Map();
extractedFiles.forEach(file => {
  const base = path.parse(file).name.toUpperCase();
  codeToFile.set(base, file);
});

jsonData.forEach(record => {
  if (record.type === 'base asana code') {
    report.push({
      id: record.id,
      code: record.code,
      type: 'base_reference',
      status: 'REFERENCE_RECORD_NO_IMAGE_EXPECTED',
      target: null
    });
    return;
  }

  // Check expected image_filename: e.g. "001_FBS-A1.png"
  const expectedFilename = record.image_filename;
  const code = record.code.toUpperCase();

  let matchedFile = null;
  if (extractedFiles.includes(expectedFilename)) {
    matchedFile = expectedFilename;
    directMatchCount++;
  } else if (codeToFile.has(code)) {
    matchedFile = codeToFile.get(code);
    codeMatchCount++;
  }

  if (matchedFile) {
    // Copy to public/asanas/ both as expected filename (e.g. 001_FBS-A1.png) and code (e.g. FBS-A1.jpg)
    const src = path.join(extractedDir, matchedFile);
    const dest1 = path.join(targetDir, expectedFilename);
    const dest2 = path.join(targetDir, `${record.code}.png`);
    fs.copyFileSync(src, dest1);
    fs.copyFileSync(src, dest2);

    report.push({
      id: record.id,
      code: record.code,
      expected: expectedFilename,
      matched: matchedFile,
      status: 'AVAILABLE'
    });
  } else {
    missingCount++;
    report.push({
      id: record.id,
      code: record.code,
      expected: expectedFilename,
      status: 'MISSING'
    });
  }
});

console.log(`Direct matches: ${directMatchCount}`);
console.log(`Code matches: ${codeMatchCount}`);
console.log(`Total matched poses: ${directMatchCount + codeMatchCount} / 250`);
console.log(`Missing poses: ${missingCount}`);

// Write asset report
fs.writeFileSync(
  'C:/Users/My Files/Desktop/tysa-score/docs/data-asset-audit.md',
  `# TYSA Score — Data and Asset Audit Report

- **Total source JSON records**: ${jsonData.length}
- **Selectable pose records**: 250
- **Base reference records**: 5 (BAC-A through BAC-E)
- **Extracted image binaries found**: ${extractedFiles.length}
- **Successfully matched and deployed pose images**: ${directMatchCount + codeMatchCount}
- **Missing pose images**: ${missingCount}
- **Neutral fallback status**: Neutral SVG placeholder implemented for any missing image.

## Base Difficulty Reference Records
${jsonData.filter(r => r.type === 'base asana code').map(r => `- **${r.code}**: BV ${r.base_value} — ${r.description}`).join('\n')}

## Category Distribution (25 poses each)
${Object.entries(
  jsonData.filter(r => r.type !== 'base asana code').reduce((acc, r) => {
    acc[r.type] = (acc[r.type] || 0) + 1;
    return acc;
  }, {})
).map(([cat, count]) => `- **${cat}**: ${count} poses`).join('\n')}
`
);
console.log('Report written to docs/data-asset-audit.md');
