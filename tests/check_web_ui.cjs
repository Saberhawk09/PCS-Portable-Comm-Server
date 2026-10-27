// Development tooling only: start tests/preview_web_ui.py first. Requires Playwright and Chrome.
const {chromium}=require(process.env.PCS_PLAYWRIGHT_MODULE || 'playwright');
const path=require('node:path');
const fs=require('node:fs');
(async()=>{
 const out=path.resolve('docs/ui-modernization/screenshots');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({headless:true,executablePath:process.env.PCS_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const page=await browser.newPage();const issues=[];
 page.on('pageerror',e=>issues.push(String(e)));
 for(const width of [2560,1440,768,360]){
  await page.setViewportSize({width,height:1000});await page.goto('http://127.0.0.1:8766/');
  await page.waitForFunction(()=>document.querySelector('[data-field="clients"]').textContent==='0');
  if(width>=1440){
   if(await page.locator('.workspace-content').evaluate(e=>e.getBoundingClientRect().width>1480))issues.push('unbounded public width');
   if(await page.locator('.overview-kpis .kpi').first().evaluate(e=>e.getBoundingClientRect().width>360))issues.push('stretched KPI');
   if(await page.locator('#services .tile').first().evaluate(e=>e.getBoundingClientRect().height>120))issues.push('oversized service tile');
  }
  for(const section of ['overview','network','radio','power','storage','diagnostics']){
   await page.locator('.workspace-nav a[href="#'+section+'"]').click();
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))issues.push('public overflow '+width+' '+section);
  }
  await page.goto('http://127.0.0.1:8766/');await page.waitForFunction(()=>document.querySelector('[data-field=clients]').textContent==='0');await page.screenshot({path:path.join(out,'public-'+width+'.png'),fullPage:true});
  await page.goto('http://127.0.0.1:8766/admin/login');if(await page.locator('#password').count()){await page.locator('#password').fill('ui-preview-password');await page.getByRole('button',{name:'Sign in to PCS Administration'}).click();}
  const panels=await page.locator('#overview [data-panel]').count();if(panels!==4)issues.push('admin overview panels missing');
  for(const section of ['overview','network','radio','power','storage','diagnostics','actions']){
   await page.locator('.workspace-nav a[href="#'+section+'"]').click();
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))issues.push('admin overflow '+width+' '+section);
  }
  await page.goto('http://127.0.0.1:8766/admin/');await page.screenshot({path:path.join(out,'admin-'+width+'.png'),fullPage:true});
  await page.locator('.workspace-nav a[href="#network"]').click();
  await page.getByRole('button',{name:'View Cellular',exact:true}).click();
  if(!await page.locator('#network').isVisible())issues.push('post lost network context '+width);
  if(!await page.locator('#action-result').textContent().then(t=>t.includes('LOCAL FIXTURE ONLY')))issues.push('missing output');
  if(await page.locator('#action-result').evaluate(e=>e!==document.activeElement))issues.push('output not focused');
 }
 // Secondary routes share the palette and retain usable narrow layouts.
 for(const route of ['/status/','/power/','/starlink/','/admin/password','/admin/bandwidth']){
   await page.goto('http://127.0.0.1:8766'+route);
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))issues.push('secondary overflow '+route);
 }
 await page.goto('http://127.0.0.1:8766/admin/');await page.keyboard.press('Tab');
 if(await page.evaluate(()=>document.activeElement.textContent)!=='Skip to content')issues.push('skip link not first');
 await page.keyboard.press('Enter');
 // Confirm cancelling a dangerous operation never submits its form.
 await page.locator('.workspace-nav a[href="#diagnostics"]').click();let posts=0;
 const onRequest=r=>{if(r.method()==='POST')posts++;};page.on('request',onRequest);
 page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'Reboot PCS',exact:true}).click();
 if(posts)issues.push('cancelled confirmation submitted');page.off('request',onRequest);
 // Every form remains present and reachable without JavaScript.
 const nojs=await browser.newContext({javaScriptEnabled:false,viewport:{width:360,height:900}});const p=await nojs.newPage();
 await p.goto('http://127.0.0.1:8766/admin/login');await p.locator('#password').fill('ui-preview-password');await p.getByRole('button',{name:'Sign in to PCS Administration'}).click();
 const count=await p.locator('input[name="action"]').count();if(count!==31)issues.push('unexpected action count '+count);
 if(!await p.locator('input[name="interval_minutes"]').isVisible())issues.push('no-JS backup form hidden');
 await p.getByRole('button',{name:'View Wi-Fi',exact:true}).click();if(!await p.locator('#action-result').isVisible())issues.push('no-JS output missing');
 await p.goto('http://127.0.0.1:8766/');if(!await p.locator('a[href="/status/"]').first().isVisible())issues.push('no-JS status unavailable');
 fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({widths:[2560,1440,768,360],actions:count,issues},null,2));
 console.log(JSON.stringify({out,actions:count,issues}));await browser.close();if(issues.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
