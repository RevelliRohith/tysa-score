const fs = require('fs');
const path = require('path');
// Data audit without external dependencies

async function runAudit() {
  const jsonPath = 'C:/Users/My Files/Downloads/asanas.json';
  const csvPath = 'C:/Users/My Files/Downloads/asanas.csv';
  
  const rawJson = fs.readFileSync(jsonPath, 'utf8');
  const jsonData = JSON.parse(rawJson);
  
  const rawCsv = fs.readFileSync(csvPath, 'utf8');
  const csvLines = rawCsv.split(/\r?\n/).filter(line => line.trim().length > 0);
  const csvHeaders = csvLines[0].split(',');
  const csvRows = csvLines.slice(1);

  console.log(`JSON records: ${jsonData.length}`);
  console.log(`CSV records: ${csvRows.length}`);

  const poseRecords = jsonData.filter(r => r.type !== 'base asana code');
  const baseRecords = jsonData.filter(r => r.type === 'base asana code');

  console.log(`Pose records: ${poseRecords.length}`);
  console.log(`Base reference records: ${baseRecords.length}`);

  // Check unique codes
  const codeSet = new Set();
  const duplicates = [];
  jsonData.forEach(r => {
    if (codeSet.has(r.code)) duplicates.push(r.code);
    codeSet.add(r.code);
  });
  console.log(`Unique codes: ${codeSet.size}, Duplicates: ${duplicates.length}`);

  // Inspect categories / types
  const categories = {};
  poseRecords.forEach(r => {
    categories[r.type] = (categories[r.type] || 0) + 1;
  });
  console.log('Categories count:', Object.keys(categories).length);
  console.log(categories);

  return {
    totalRecords: jsonData.length,
    poseRecords: poseRecords.length,
    baseRecords: baseRecords.length,
    duplicates,
    categories
  };
}

runAudit().catch(console.error);
