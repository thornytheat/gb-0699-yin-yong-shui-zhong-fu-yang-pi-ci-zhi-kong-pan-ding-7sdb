// M8 核查保护放行：补入核查后局部刷新，保留既有放行结论（服务端只追加）。
const app = document.getElementById('check-app');
const notice = document.getElementById('check-notice');

async function post(url, payload) {
  const resp = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.message || '操作失败');
  return data;
}

function statusLabel(view, code) {
  return view.status_text[code] || code;
}

function render(view) {
  document.getElementById('k-total').textContent = view.summary.total;
  document.getElementById('k-protected').textContent = view.summary.protected;
  document.getElementById('k-waiting').textContent = view.summary.waiting;
  document.getElementById('k-retest').textContent = view.summary.retest_open;
  document.getElementById('k-checks').textContent = view.checks.length;

  document.getElementById('segments').innerHTML = view.segments.map(seg => `
    <article class="seg ${seg.protected ? 'seg-ok' : 'seg-bad'}">
      <header><b>第${seg.segment + 1}段</b>
        <span class="seg-guard">${seg.guard_check ? `核查 ${seg.guard_check} · ${seg.guard_result}` : '无合格核查'}</span>
        <span class="seg-range">序列 ${seg.start_seq}–${seg.end_seq}</span></header>
      <p class="seg-state">${seg.status_text}${seg.waiting ? ` · <em>${seg.waiting} 份等待证据</em>` : ''}</p>
      <div class="seg-samples">${seg.sample_ids.map(sid => `<span class="chip">${sid}</span>`).join('')}</div>
    </article>`).join('');

  document.querySelector('#sample-table tbody').innerHTML = view.samples.map(s => `
    <tr class="row-${s.status}">
      <td>${s.sample_id}</td><td>${s.batch}</td><td>${s.analyte}</td>
      <td class="num">${s.seq}</td><td class="num">${s.value ?? '—'}</td>
      <td>${s.protecting_check ? `<code>${s.protecting_check}</code>` : '<span class="muted">—</span>'}</td>
      <td><span class="badge badge-${s.status}">${statusLabel(view, s.status)}</span></td>
      <td>${s.released ? `放行 rev${s.release_revision}` : '<em class="warn">等待证据</em>'}</td>
    </tr>`).join('');

  document.getElementById('check-ledger').innerHTML = view.checks.map(c => `
    <li class="${c.result === 'PASS' ? 'pass' : 'fail'}"><code>${c.check_id}</code><b>${c.result}</b>
      <span>序列${c.seq} · ${c.source}</span></li>`).join('') || '<li class="muted">暂无核查。</li>';

  document.getElementById('task-count').textContent = `${view.tasks.length} 项`;
  document.getElementById('task-list').innerHTML = view.tasks.map(t => `
    <li class="task-${t.status}"><b>${t.sample_id}</b><span>${t.analyte} · 序列${t.from_seq}</span>
      <em class="tag-${t.status}">${t.status === 'OPEN' ? '在办' : '已覆盖·' + t.closed_check}</em></li>`).join('')
    || '<li class="muted">暂无复测任务。</li>';
}

document.getElementById('add-check-form').onsubmit = async (event) => {
  event.preventDefault();
  const fd = new FormData(event.target);
  const payload = {
    check_id: String(fd.get('check_id') || '').trim(),
    result: fd.get('result'),
    value: fd.get('value') || null,
  };
  const seqRaw = String(fd.get('seq') || '').trim();
  if (seqRaw) payload.seq = Number(seqRaw);
  notice.textContent = '正在重判…';
  try {
    const view = await post(`/api/check/batches/${app.dataset.id}/checks`, payload);
    render(view);
    notice.textContent = `已补入 ${payload.check_id}，关闭复测 ${view.closed_tasks || 0} 项；原放行结论保留`;
    event.target.reset();
  } catch (err) {
    notice.textContent = err.message;
  }
};
