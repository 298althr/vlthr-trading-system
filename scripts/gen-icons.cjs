// Generates PWA PNG icons from the SVG favicon at build time
// Called from Dockerfile.frontend during the build stage
const sharp = require('sharp');
const fs = require('fs');
const path = require('path');

const svgBuf = fs.readFileSync(path.join(__dirname, '../public/favicon.svg'));
const outDir = path.join(__dirname, '../dist');

Promise.all([
  sharp(svgBuf).resize(192, 192).png().toFile(path.join(outDir, 'icon-192.png')),
  sharp(svgBuf).resize(512, 512).png().toFile(path.join(outDir, 'icon-512.png')),
]).then(() => {
  console.log('[PWA] icon-192.png and icon-512.png generated in dist/');
}).catch(err => {
  console.error('[PWA] Icon generation failed:', err.message);
  process.exit(1);
});
