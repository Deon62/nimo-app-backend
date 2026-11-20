// Regenerates app/data/agents.json from the app's src/data/agents.ts.
// Run from the repo root:  node --experimental-strip-types backend/scripts/export_agents.mjs
import { writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const { agents } = await import(pathToFileURL(path.join(here, '../../src/data/agents.ts')).href);

const out = agents.map((a) => ({
  id: a.id,
  name: a.name,
  role: a.role,
  category: a.category,
  tagline: a.tagline,
  personality: a.personality,
  helps_with: a.helpsWith,
  starters: a.starters,
  instructions: a.instructions,
  voice_id: a.voice.id,
  voice_sample: a.voice.sample,
}));
writeFileSync(path.join(here, '../app/data/agents.json'), JSON.stringify(out, null, 2) + '\n');
console.log(`Exported ${out.length} agents`);
