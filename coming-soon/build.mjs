// Inlines coming-soon/art/*.svg into index.html between <!-- art:NAME --> … <!-- /art:NAME --> markers.
// The deployed site stays plain static HTML; run this after editing any art file and commit the result.
//   node coming-soon/build.mjs          rewrite index.html
//   node coming-soon/build.mjs --check  exit 1 if index.html is out of date
import { readFileSync, writeFileSync } from 'node:fs';

const dir = new URL('.', import.meta.url);
const page = new URL('index.html', dir);
const before = readFileSync(page, 'utf8');

const after = before.replace(/<!-- art:([\w-]+) -->[\s\S]*?<!-- \/art:\1 -->/g, (_, name) => {
  const svg = readFileSync(new URL(`art/${name}.svg`, dir), 'utf8')
    .replace(/<\?xml[^>]*>\s*/, '')
    .replace(/^<!--[\s\S]*?-->\s*/, '')
    .trim();
  return `<!-- art:${name} -->\n${svg}\n<!-- /art:${name} -->`;
});

if (process.argv.includes('--check')) {
  if (after !== before) {
    console.error('index.html is out of date with art/. Run: node coming-soon/build.mjs');
    process.exit(1);
  }
  console.log('index.html art is up to date.');
} else {
  writeFileSync(page, after);
  console.log('Inlined art into index.html.');
}
