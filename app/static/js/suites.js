let currentSuiteId = null;

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

document.addEventListener('DOMContentLoaded', () => {
  loadSuites();

  document.getElementById('btn-seed-suite').addEventListener('click', async () => {
    try {
      await API.post('/test-suites/seed');
      alert('Standard test suite seeded successfully!');
      loadSuites();
    } catch (err) {
      alert('Seed failed: ' + err.message);
    }
  });

  document.getElementById('btn-open-gen-modal').addEventListener('click', async () => {
    const suites = await API.get('/test-suites');
    const sel = document.getElementById('gen-suite-id');
    sel.innerHTML = suites.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
    document.getElementById('modal-gen').classList.add('active');
  });

  document.getElementById('form-generate-cases').addEventListener('submit', async (e) => {
    e.preventDefault();
    const suiteId = parseInt(document.getElementById('gen-suite-id').value);
    const category = document.getElementById('gen-category').value;
    const count = parseInt(document.getElementById('gen-count').value);

    try {
      await API.post(`/test-suites/${suiteId}/generate`, {
        category: category,
        num_cases: count
      });
      closeModal('modal-gen');
      alert(`Generated ${count} test cases! Reviewing cases in suite...`);
      selectSuite(suiteId);
    } catch (err) {
      alert('Case generation failed: ' + err.message);
    }
  });
});

async function loadSuites() {
  const body = document.getElementById('suites-table-body');
  try {
    const suites = await API.get('/test-suites');
    if (!suites || suites.length === 0) {
      body.innerHTML = '<tr><td colspan="6" style="text-align:center;">No test suites found. Click "Seed Standard Suite" above!</td></tr>';
      return;
    }

    body.innerHTML = suites.map(s => `
      <tr>
        <td>#${s.id}</td>
        <td><strong>${s.name}</strong></td>
        <td><span class="badge badge-info">${s.category || 'general'}</span></td>
        <td><strong>${s.case_count || s.cases?.length || 0} cases</strong></td>
        <td style="color:var(--text-dim); font-size:0.8rem;">${new Date(s.created_at || Date.now()).toLocaleDateString()}</td>
        <td>
          <button class="btn btn-secondary" style="font-size:0.75rem; padding:0.3rem 0.6rem;" onclick="selectSuite(${s.id}, '${s.name}')">
            Inspect Cases ➔
          </button>
        </td>
      </tr>
    `).join('');

    if (suites.length > 0 && !currentSuiteId) {
      selectSuite(suites[0].id, suites[0].name);
    }
  } catch (err) {
    body.innerHTML = `<tr><td colspan="6" style="color:var(--danger);">Error loading suites: ${err.message}</td></tr>`;
  }
}

async function selectSuite(id, name) {
  currentSuiteId = id;
  document.getElementById('cases-section').style.display = 'block';
  document.getElementById('selected-suite-title').textContent = `Suite Cases: ${name} (Suite #${id})`;
  loadCases(id);
}

async function loadCases(suiteId) {
  const body = document.getElementById('cases-table-body');
  try {
    const suite = await API.get(`/test-suites/${suiteId}`);
    const cases = suite.cases || [];

    if (cases.length === 0) {
      body.innerHTML = '<tr><td colspan="5" style="text-align:center;">No test cases in this suite yet.</td></tr>';
      return;
    }

    body.innerHTML = cases.map(c => `
      <tr>
        <td>#${c.id}</td>
        <td><span class="badge badge-info">${c.category || 'general'}</span></td>
        <td style="font-family:var(--font-mono); font-size:0.85rem; max-width:300px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
          ${escapeHtml(c.input || '')}
        </td>
        <td style="color:var(--text-muted); font-size:0.85rem; max-width:250px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
          ${escapeHtml(c.expected_answer || '—')}
        </td>
        <td><span class="badge ${c.is_approved ? 'badge-pass' : 'badge-review'}">${c.is_approved ? 'ACTIVE' : 'PENDING'}</span></td>
      </tr>
    `).join('');
  } catch (err) {
    body.innerHTML = `<tr><td colspan="5" style="color:var(--danger);">Error loading cases: ${err.message}</td></tr>`;
  }
}

function escapeHtml(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
