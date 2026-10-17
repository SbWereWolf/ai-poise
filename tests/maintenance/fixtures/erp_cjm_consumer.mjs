// Invoke only the actual selected configuration loader. No browser/application.
import { pathToFileURL } from 'node:url';

const { loadCjmConfiguration } = await import(pathToFileURL(process.argv[2]).href);
const configuration = loadCjmConfiguration(process.argv[3], process.argv[4]);
console.log(JSON.stringify({
  wirePrice: configuration.businessFixture.wirePrice,
  setupPrice: configuration.businessFixture.setupPrice,
  draftSlabCountLimit: configuration.draftSlabCountLimit,
}));
