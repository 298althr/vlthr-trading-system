const { Router } = require('express');
const { spawn } = require('child_process');
const path = require('path');

module.exports = function createRefreshRouter(syncFromPostgres, sendSSEToAll, refreshState) {
  const router = Router();

  router.post('/api/refresh', (req, res) => {
    if (refreshState.running) {
      return res.status(409).json({ error: 'Refresh already in progress. Please wait.' });
    }
    refreshState.running = true;

    const enginePath = process.env.ENGINE_ROOT || path.join(__dirname, '..', '..', '..');
    // Use portfolio orchestrator (run_pipeline.py) instead of legacy refresh_pipeline.py
    const scriptPath = path.join(enginePath, 'paper_trade_unzipped', 'vlthr-signal-dashboard', 'engine', 'run_pipeline.py');
    const args = [scriptPath];
    const pythonExe = process.platform === 'win32' ? 'python' : 'python3';
    const proc = spawn(pythonExe, args, {
      cwd: enginePath,
      env: { ...process.env, PYTHONUNBUFFERED: '1' }
    });

    const progress = { step: 0, total: 4, message: 'Starting refresh...', detail: '' };
    let summary = { signals: 0, expired: 0, refreshed: 0, pending: 0 };

    proc.stdout.on('data', (data) => {
      const lines = data.toString().split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;

        // Legacy [PIPELINE] format support
        const pipeMatch = trimmed.match(/\[PIPELINE\]\s+(\S+)\s+\|\s+(.*)/);
        if (pipeMatch) {
          const step = pipeMatch[1];
          const msg = pipeMatch[2];
          progress.step = parseInt(step.split('/')[0]) || 0;
          progress.message = msg;
          sendSSEToAll('refreshProgress', progress);
          console.log(`[Pipeline] ${trimmed}`);
          continue;
        }

        // New portfolio orchestrator Step X format
        const stepMatch = trimmed.match(/Step\s+(\d+)/i);
        if (stepMatch) {
          const s = parseInt(stepMatch[1]);
          // Map pipeline steps to progress 1-4
          if (s === 1) progress.step = 1;
          else if (s === 2 || s === 3) progress.step = 2;
          else if (s === 4 || s === 5) progress.step = 3;
          else if (s >= 6) progress.step = 4;
          progress.message = trimmed;
          sendSSEToAll('refreshProgress', progress);
          console.log(`[Pipeline] ${trimmed}`);
          continue;
        }

        // Parse Result dict for summary counts
        const resMatch = trimmed.match(/'signals_scanned':\s*(\d+).*?'signals_approved':\s*(\d+)/);
        if (resMatch) {
          summary = {
            signals: parseInt(resMatch[2]),
            expired: 0,
            refreshed: parseInt(resMatch[1]),
            pending: 0
          };
        }

        console.log(`[Pipeline] ${trimmed}`);
      }
    });

    proc.stderr.on('data', (data) => {
      const text = data.toString().trim();
      if (text) {
        progress.message = text.substring(0, 200);
        sendSSEToAll('refreshProgress', progress);
        console.error(`[Pipeline Err] ${text}`);
      }
    });

    proc.on('close', async (code) => {
      refreshState.running = false;
      progress.step = 4;
      progress.message = code === 0 ? 'Complete' : `Failed (code ${code})`;
      sendSSEToAll('refreshProgress', progress);

      if (code === 0) {
        await syncFromPostgres();
        sendSSEToAll('refreshComplete', { success: true, ...summary });
        console.log(`[Refresh] Complete: ${JSON.stringify(summary)}`);
      } else {
        sendSSEToAll('refreshComplete', { success: false, error: `Pipeline exited with code ${code}` });
      }
    });

    res.json({ success: true, started: true, message: 'Refresh started. Watch progress via SSE.' });
  });

  return router;
};
