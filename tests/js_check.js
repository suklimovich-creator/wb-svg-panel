// Проверка скриптов страницы (с 1.45):
//  * синтаксис не новее ES5 - страница должна работать на старых
//    планшетах (Android 5, старый Firefox): никаких =>, let, `шаблонов`;
//  * порядок файлов static/js: код, который выполняется сразу при
//    загрузке, зовёт только функции из своего и предыдущих файлов;
//  * одна функция не объявлена в двух файлах.
//
//   npm i --no-save acorn@8 && node tests/js_check.js
const fs = require('fs'), path = require('path');
const acorn = require('acorn');
const ROOT = path.join(__dirname, '..');
let bad = 0;

function inlineScripts(file) {
  const html = fs.readFileSync(path.join(ROOT, file), 'utf8');
  const out = [];
  const re = /<script>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html))) out.push(m[1]);
  return out.join('\n;\n').replace(/__\w+__/g, '0').replace(/\{\{[\s\S]*?\}\}/g, '0')
    .replace(/\{%[\s\S]*?%\}/g, '');
}
function es5(name, src) {
  try { return acorn.parse(src, {ecmaVersion: 5, locations: true}); }
  catch (e) {
    bad++;
    const line = src.split('\n')[(e.loc ? e.loc.line : 1) - 1] || '';
    console.log('НЕ ES5: ' + name + ':' + (e.loc ? e.loc.line : '?') + ': ' + e.message + '\n    ' + line.trim().slice(0, 120));
    return null;
  }
}

// порядок - как в templates/wrapper.html
const wrapper = fs.readFileSync(path.join(ROOT, 'templates/wrapper.html'), 'utf8');
const order = [];
wrapper.replace(/<script src="static\/js\/([\w-]+)\.js/g, function (_, n) { order.push(n); });
const onDisk = fs.readdirSync(path.join(ROOT, 'static/js')).filter(f => f.endsWith('.js')).map(f => f.slice(0, -3));
onDisk.forEach(n => { if (order.indexOf(n) < 0) { bad++; console.log('static/js/' + n + '.js не подключён в wrapper.html'); } });

es5('templates/wrapper.html (встроенный)', inlineScripts('templates/wrapper.html'));
es5('templates/themes.html', inlineScripts('templates/themes.html'));

const asts = {}, defs = {};
order.forEach(n => {
  const src = fs.readFileSync(path.join(ROOT, 'static/js', n + '.js'), 'utf8');
  asts[n] = es5('static/js/' + n + '.js', src);
  defs[n] = new Set(asts[n] ? asts[n].body.filter(s => s.type === 'FunctionDeclaration').map(s => s.id.name) : []);
});

// Идентификаторы, которые вычисляются сразу при загрузке файла: всё вне
// тел функций, кроме тел немедленно вызванных функций (IIFE).
function refs(node, out) {
  if (!node || typeof node.type !== 'string') return;
  if (node.type === 'FunctionExpression' || node.type === 'FunctionDeclaration') return;
  if (node.type === 'CallExpression' && node.callee.type === 'FunctionExpression') {
    node.callee.body.body.forEach(s => refs(s, out));
    node.arguments.forEach(a => refs(a, out));
    return;
  }
  if (node.type === 'MemberExpression') { refs(node.object, out); if (node.computed) refs(node.property, out); return; }
  if (node.type === 'Property') { if (node.computed) refs(node.key, out); refs(node.value, out); return; }
  if (node.type === 'Identifier') { out.push([node.name, node.loc.start.line]); return; }
  for (const k in node) {
    if (k === 'loc') continue;
    const v = node[k];
    if (Array.isArray(v)) v.forEach(x => refs(x, out));
    else if (v && typeof v === 'object' && v.type) refs(v, out);
  }
}
const seen = {};
order.forEach((n, i) => {
  defs[n].forEach(f => {
    if (seen[f]) { bad++; console.log('функция ' + f + ' объявлена и в ' + seen[f] + '.js, и в ' + n + '.js'); }
    seen[f] = n;
  });
  if (!asts[n]) return;
  const later = {};
  order.slice(i + 1).forEach(m => defs[m].forEach(f => { later[f] = m; }));
  const out = [];
  asts[n].body.filter(s => s.type !== 'FunctionDeclaration').forEach(s => refs(s, out));
  out.forEach(([nm, line]) => {
    if (later[nm]) { bad++; console.log('static/js/' + n + '.js:' + line + ': при загрузке зовёт ' + nm + '() из ' + later[nm] + '.js - он подключён позже'); }
  });
});
console.log(bad ? 'ошибок: ' + bad : 'скрипты страницы: ES5, порядок файлов в порядке (' + order.length + ' файлов)');
process.exit(bad ? 1 : 0);
