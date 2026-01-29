const fs = require('fs');
const path = require('path');

const pricesDir = path.join(__dirname, '..', 'prices');
const outputFile = process.argv[2] || 'normalized-automated.json';

// Read all JSON files from prices folder
const files = fs.readdirSync(pricesDir).filter(f => f.endsWith('.json') && f !== 'schema.json');

const providers = {};
for (const file of files.sort()) {
  const data = JSON.parse(fs.readFileSync(path.join(pricesDir, file), 'utf8'));
  const providerName = data.provider || path.basename(file, '.json');
  providers[providerName] = data;
}

// Sort providers alphabetically for consistent output
const sortedProviders = {};
Object.keys(providers).sort().forEach(key => {
  sortedProviders[key] = providers[key];
});

const output = {
  last_updated: new Date().toISOString(),
  providers: sortedProviders
};

fs.writeFileSync(outputFile, JSON.stringify(output, null, 2) + '\n');
console.log(`Written to ${outputFile} with ${Object.keys(providers).length} providers`);
