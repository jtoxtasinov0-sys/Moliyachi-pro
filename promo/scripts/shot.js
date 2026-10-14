// node scripts/shot.js <page> <out.jpg> <t> [scale]
const {chromium}=require('/opt/node22/lib/node_modules/playwright');
(async()=>{const [,,pg,out,t,sc]=process.argv;const b=await chromium.launch();const p=await b.newPage({viewport:{width:1080,height:1920},deviceScaleFactor:+(sc||.3)});
p.on('pageerror',e=>console.error('ERR',e.message));
await p.goto('file://'+require('path').resolve(pg));await p.waitForFunction(()=>window.READY===true,null,{timeout:20000});await p.evaluate(t=>renderAt(t),+t);await p.screenshot({path:out,type:'jpeg'});await b.close()})();
