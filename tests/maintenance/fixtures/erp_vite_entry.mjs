// Independent usable-entry observation, not an npm metadata check.
import { pathToFileURL } from 'node:url';

const vite = await import(pathToFileURL(process.argv[2]).href);
process.stdout.write(typeof vite.createServer);
