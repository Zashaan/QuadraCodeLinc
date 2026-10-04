export const demoPage = `<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Abe · Judging demo</title>
<style>
body{margin:0;background:#f5f6f2;color:#193b34;font:17px/1.6 system-ui,sans-serif}main{max-width:850px;margin:65px auto;padding:24px}h1{font-size:52px;line-height:1.1}p{max-width:720px}.tag{font-size:13px;text-transform:uppercase;letter-spacing:2px}button,a{display:inline-block;padding:12px 20px;border-radius:12px;border:1px solid #193b34;margin:6px 8px 6px 0;font:inherit}button{background:#193b34;color:white;cursor:pointer}a{color:#193b34;text-decoration:none}button:disabled{opacity:.6}section{background:white;border-radius:18px;padding:24px;margin-top:24px}pre{overflow:auto;font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}summary{cursor:pointer}#status{font-weight:600}
</style><main><span class="tag">Abe · AI dental-benefits assistant</span>
<h1>Understand the benefits.<br>See the math.</h1>
<p>This credential-free demo runs the Python backend, Node gateway and React companion on your computer. All member, provider and plan information is synthetic.</p>
<button id="run">Run a crown benefits scenario</button><a href="http://127.0.0.1:5173" target="_blank" rel="noreferrer">Explore companion app ↗</a>
<section><h2>What you can try</h2><p>The scenario normalizes “D E M O double zero one,” looks up the member, retrieves local plan evidence, finds providers, calculates a crown estimate, and compares benefit options. Results below come from the real backend tools.</p>
<p>The companion app includes sample conversations, transcripts and summaries. Free-form AI chat and telephone calls need AWS/Twilio credentials and are unavailable in this offline demo. No paid services are called.</p></section>
<p id="status" role="status" aria-live="polite"></p><div id="results"></div>
<script>
const run = document.getElementById('run');
run.onclick = async () => {
 run.disabled = true;
 document.getElementById('status').textContent = 'Running the gateway → backend tool flow…';
 document.getElementById('results').replaceChildren();
 try {
  const response = await fetch('/demo/run', {method:'POST', headers:{'X-Abe-Demo':'1'}});
  if (!response.ok) throw new Error('Scenario could not finish. Check the terminal and try again.');
  const data = await response.json();
  for (const item of data.results) {
   const section = document.createElement('section');
   const title = document.createElement('h2'); title.textContent = item.tool.replaceAll('_', ' ');
   section.append(title);
   if (item.tool === 'calculate_benefit') {
    const summary = document.createElement('p');
    summary.textContent = 'Estimated member payment: $' + item.result.estimated_member_payment + ' · Estimated plan payment: $' + item.result.plan_payment + '. Estimate only; no benefits spent or reserved.';
    section.append(summary);
   }
   const detail = document.createElement('details');
   const label = document.createElement('summary'); label.textContent = 'View verified tool output';
   const pre = document.createElement('pre'); pre.textContent = JSON.stringify(item.result, null, 2);
   detail.append(label, pre); section.append(detail); document.getElementById('results').append(section);
  }
  document.getElementById('status').textContent = 'Completed all seven tool calls. Local fixtures only — no phone minutes or cloud charges.';
 } catch (error) { document.getElementById('status').textContent = error.message; }
 finally { run.disabled = false; }
};
</script></main></html>`;
