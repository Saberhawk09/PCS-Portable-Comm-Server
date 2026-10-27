// Development fixture only. Start preview_web_ui.py --port 8767 first.
const {chromium}=require(process.env.PCS_PLAYWRIGHT_MODULE || 'playwright');
const fs=require('node:fs');const path=require('node:path');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PCS_CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'});
 const page=await browser.newPage();const issues=[];page.on('pageerror',e=>issues.push(String(e)));
 const base=process.env.PCS_LOCATION_PREVIEW_URL || 'http://127.0.0.1:8767';
 const out=path.resolve('docs/manual-position');fs.mkdirSync(out,{recursive:true});
 await page.goto(base+'/admin/login');await page.locator('#password').fill('ui-preview-password');
 await page.getByRole('button',{name:'Sign in to PCS Administration'}).click();
 for(const width of [1440,768,360]){
  await page.setViewportSize({width,height:1000});await page.goto(base+'/admin/location');
  if(!await page.getByText('Manual',{exact:true}).isVisible())issues.push('missing Manual label');
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))issues.push('overflow '+width);
  await page.getByLabel('Latitude (decimal degrees)').fill('41.1');
  await page.getByLabel('Longitude (decimal degrees)').fill('-81.2');
  await page.getByLabel('Persist manual position even when GPS recovers').check();
  await page.screenshot({path:path.join(out,'settings-'+width+'.png'),fullPage:true});
  // Browser must block an unconfirmed save without a request.
  let posts=0;const count=r=>{if(r.method()==='POST')posts++;};page.on('request',count);
  await page.getByRole('button',{name:'Save location settings'}).click();
  if(posts)issues.push('unconfirmed form submitted');page.off('request',count);
  await page.getByLabel('I understand existing APRS/Meshtastic sharing may publish this position.').check();
  await page.getByRole('button',{name:'Save location settings'}).click();
  await page.waitForURL('**/admin/location?saved=1');
  if(!await page.getByText('Location settings saved.',{exact:true}).isVisible())issues.push('missing save notice');
 }
 await page.goto(base+'/admin/#diagnostics');await page.getByRole('link',{name:'Location settings',exact:true}).click();
 if(!page.url().endsWith('/admin/location'))issues.push('missing dashboard link');
 await browser.close();fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify({fixture:true,widths:[1440,768,360],issues},null,2));
 console.log(JSON.stringify({issues}));if(issues.length)process.exitCode=1;
})().catch(e=>{console.error(e);process.exit(1)});
