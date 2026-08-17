document.addEventListener("DOMContentLoaded", () => {
  const statSent = document.getElementById("stat-sent");
  const statQueued = document.getElementById("stat-queued");
  const statDuplicates = document.getElementById("stat-duplicates");
  const statFailed = document.getElementById("stat-failed");
  
  const rulesTableBody = document.getElementById("rules-tbody");
  const rulesCount = document.getElementById("rules-count");
  const jobsTableBody = document.getElementById("jobs-tbody");
  const jobsCount = document.getElementById("jobs-count");
  
  const ruleForm = document.getElementById("rule-form");
  const ruleKeyword = document.getElementById("rule-keyword");
  const ruleMessage = document.getElementById("rule-message");
  const btnSaveRule = document.getElementById("btn-save-rule");
  const refreshBtn = document.getElementById("refresh-btn");
  
  const btnStartSim = document.getElementById("btn-start-sim");
  const simUrlInput = document.getElementById("sim-url");
  const simCountInput = document.getElementById("sim-count");
  const simDurationInput = document.getElementById("sim-duration");
  const simResultBox = document.getElementById("sim-result");

  // Populate default simulation webhook URL based on window.location
  if (simUrlInput && !simUrlInput.value) {
    simUrlInput.value = `${window.location.origin}/webhook`;
  }

  // Fetch Live Stats
  async function fetchStats() {
    try {
      const res = await fetch("/stats");
      if (res.ok) {
        const data = await res.json();
        statSent.textContent = data.sent;
        statQueued.textContent = data.queued;
        statDuplicates.textContent = data.duplicates_blocked;
        statFailed.textContent = data.failed;
      }
    } catch (e) {
      console.error("Error fetching stats:", e);
    }
  }

  // Fetch Rules
  async function fetchRules() {
    try {
      const res = await fetch("/rules");
      if (res.ok) {
        const rules = await res.json();
        rulesCount.textContent = rules.length;
        if (rules.length === 0) {
          rulesTableBody.innerHTML = `<tr><td colspan="3" class="empty-state">No rules registered yet.</td></tr>`;
          return;
        }
        rulesTableBody.innerHTML = rules.map(r => `
          <tr>
            <td><strong style="color: #38bdf8">${escapeHtml(r.keyword)}</strong></td>
            <td>${escapeHtml(r.dm_message)}</td>
            <td class="mono-cell">${escapeHtml(r.rule_id)}</td>
          </tr>
        `).join("");
      }
    } catch (e) {
      console.error("Error fetching rules:", e);
    }
  }

  // Fetch Recent Jobs
  async function fetchJobs() {
    try {
      const res = await fetch("/jobs?limit=50");
      if (res.ok) {
        const jobs = await res.json();
        jobsCount.textContent = `${jobs.length} jobs`;
        if (jobs.length === 0) {
          jobsTableBody.innerHTML = `<tr><td colspan="6" class="empty-state">No DM activity yet.</td></tr>`;
          return;
        }
        jobsTableBody.innerHTML = jobs.map(j => {
          const statusClass = `status-${j.status.toLowerCase()}`;
          return `
            <tr>
              <td><span class="status-pill ${statusClass}">${escapeHtml(j.status)}</span></td>
              <td class="mono-cell">${escapeHtml(j.user_id)}</td>
              <td class="mono-cell">${escapeHtml(j.comment_id)}</td>
              <td class="mono-cell">${j.dm_id ? escapeHtml(j.dm_id) : "-"}</td>
              <td>${j.retry_count}</td>
              <td class="mono-cell" style="font-size:0.75rem; color:#94a3b8;">${j.last_error ? escapeHtml(j.last_error) : "-"}</td>
            </tr>
          `;
        }).join("");
      }
    } catch (e) {
      console.error("Error fetching jobs:", e);
    }
  }

  // Create Rule Handler
  ruleForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    btnSaveRule.disabled = true;
    btnSaveRule.textContent = "Creating...";

    const payload = {
      keyword: ruleKeyword.value.trim(),
      dm_message: ruleMessage.value.trim()
    };

    try {
      const res = await fetch("/rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (res.status === 201) {
        ruleKeyword.value = "";
        ruleMessage.value = "";
        await fetchRules();
      } else {
        const err = await res.json();
        alert(`Failed to create rule: ${JSON.stringify(err)}`);
      }
    } catch (err) {
      alert(`Network error creating rule: ${err.message}`);
    } finally {
      btnSaveRule.disabled = false;
      btnSaveRule.textContent = "Create Rule";
    }
  });

  // Simulation Runner Handler
  btnStartSim.addEventListener("click", async () => {
    const webhookUrl = simUrlInput.value.trim();
    const count = parseInt(simCountInput.value) || 500;
    const duration = parseInt(simDurationInput.value) || 10;

    if (!webhookUrl) {
      alert("Please provide a valid webhook URL.");
      return;
    }

    btnStartSim.disabled = true;
    btnStartSim.textContent = "Firing Events...";
    simResultBox.style.display = "block";
    simResultBox.textContent = "Sending simulation request to mock API...";

    try {
      const res = await fetch(`/simulate/trigger?webhook_url=${encodeURIComponent(webhookUrl)}&count=${count}&duration_seconds=${duration}`, {
        method: "POST"
      });

      const data = await res.json();
      if (res.ok) {
        simResultBox.textContent = `Simulation started! Run ID: ${data.run_id}\nEvents: ${count} over ${duration}s.\nCheck /simulate/truth/${data.run_id} to verify.`;
      } else {
        simResultBox.textContent = `Simulation error (${res.status}): ${JSON.stringify(data)}`;
      }
    } catch (err) {
      simResultBox.textContent = `Simulation failed: ${err.message}`;
    } finally {
      btnStartSim.disabled = false;
      btnStartSim.textContent = "Trigger 500-Event Burst";
    }
  });

  // Manual Refresh
  refreshBtn.addEventListener("click", () => {
    fetchStats();
    fetchRules();
    fetchJobs();
  });

  function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  // Initial Load
  fetchStats();
  fetchRules();
  fetchJobs();

  // Periodic polling for live updates
  setInterval(fetchStats, 1500);
  setInterval(fetchJobs, 2500);
});
