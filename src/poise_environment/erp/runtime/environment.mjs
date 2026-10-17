import { readFileSync } from 'node:fs';
import { parseEnv } from 'node:util';
import { relative } from 'node:path';
import { pathToFileURL } from 'node:url';

const [operation, ...args] = process.argv.slice(2);
if (operation === 'read') {
  process.stdout.write(JSON.stringify(parseEnv(readFileSync(args[0], 'utf8'))));
} else if (operation === 'cjm') {
  const { loadCjmConfiguration } = await import(pathToFileURL(args[0]).href);
  const result = loadCjmConfiguration(args[1], relative(args[1], args[2]));
  process.stdout.write(JSON.stringify({
    wirePrice: result.businessFixture.wirePrice,
    setupPrice: result.businessFixture.setupPrice,
  }));
} else {
  throw new Error(`Unknown environment operation: ${operation}`);
}
