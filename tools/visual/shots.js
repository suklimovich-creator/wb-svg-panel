// SVG -> PNG для сравнения вида: две папки (было / стало) в одну папку
// картинок name.a.png / name.b.png. Сравнивает tools/visual/pixdiff.py.
//
//   node tools/visual/shots.js <было> <стало> <куда> [ширина]
//
// Нужен Playwright с Chromium (npm i playwright). Путь к браузеру можно
// задать переменной CHROMIUM, иначе - тот, что знает Playwright.
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [a, b, out, width] = process.argv.slice(2);
  const W = +(width || 800);
  const opts = {args: ['--no-sandbox']};
  if (process.env.CHROMIUM) opts.executablePath = process.env.CHROMIUM;
  const br = await chromium.launch(opts);
  const p = await br.newPage({viewport: {width: W, height: 600}});
  fs.mkdirSync(out, {recursive: true});
  const files = fs.readdirSync(a).filter(f => f.endsWith('.svg') && fs.existsSync(b + '/' + f));
  for (const f of files) {
    for (const [dir, tag] of [[a, 'a'], [b, 'b']]) {
      const svg = fs.readFileSync(dir + '/' + f, 'utf8');
      await p.setContent('<body style="margin:0;background:#fff">' +
        svg.replace('<svg', '<svg style="width:' + W + 'px;height:auto;display:block"') + '</body>');
      // SMIL (вентилятор, круги на воде) - на нулевой кадр: иначе
      // одинаковые картинки различаются моментом анимации
      await p.evaluate(() => { const s = document.querySelector('svg');
        if (s && s.pauseAnimations) { s.pauseAnimations(); s.setCurrentTime(0); } });
      await p.waitForTimeout(40);
      await p.screenshot({path: out + '/' + f + '.' + tag + '.png', fullPage: true, animations: 'disabled'});
    }
  }
  await br.close();
  console.log('картинок:', files.length);
})();
