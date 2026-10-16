// Inlines coming-soon/art/*.svg into index.html between <!-- art:NAME --> … <!-- /art:NAME --> markers,
// and styles.css between <!-- css:styles --> markers (saves a render-blocking request on slow networks).
// Also shows the "Hear Katha" button only when audio/katha-sample.mp3 exists (brief §9.1).
// The deployed site stays plain static HTML; run this after editing art, styles or audio, and commit the result.
//   node coming-soon/build.mjs          rewrite index.html
//   node coming-soon/build.mjs --check  exit 1 if index.html is out of date
import { existsSync, readFileSync, writeFileSync } from 'node:fs';

const dir = new URL('.', import.meta.url);
const page = new URL('index.html', dir);
const before = readFileSync(page, 'utf8');

const after = before.replace(/<!-- art:([\w-]+) -->[\s\S]*?<!-- \/art:\1 -->/g, (_, name) => {
  const svg = readFileSync(new URL(`art/${name}.svg`, dir), 'utf8')
    .replace(/<\?xml[^>]*>\s*/, '')
    .replace(/^<!--[\s\S]*?-->\s*/, '')
    .trim();
  return `<!-- art:${name} -->\n${svg}\n<!-- /art:${name} -->`;
})
  .replace(/<!-- css:styles -->[\s\S]*?<!-- \/css:styles -->/, () => {
    const css = readFileSync(new URL('styles.css', dir), 'utf8').trim();
    return `<!-- css:styles -->\n<style>\n${css}\n</style>\n<!-- /css:styles -->`;
  })
  .replace(/(<button[^>]*class="hear"[^>]*?)( hidden)?>/, (_, open) =>
    existsSync(new URL('audio/katha-sample.mp3', dir)) ? `${open}>` : `${open} hidden>`);

if (process.argv.includes('--check')) {
  if (after !== before) {
    console.error('index.html is out of date with art/, styles.css or audio/. Run: node coming-soon/build.mjs');
    process.exit(1);
  }
  console.log('index.html is up to date.');
} else {
  writeFileSync(page, after);
  console.log('Inlined art into index.html.');
}
