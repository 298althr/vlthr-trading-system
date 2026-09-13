const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3001;
const DB_PATH = path.join(__dirname, 'db.json');

app.use(cors());
app.use(express.json());

// In-memory data copy from db.json
let database = { signals: [], history: [] };

function loadDatabase() {
  try {
    if (fs.existsSync(DB_PATH)) {
      const content = fs.readFileSync(DB_PATH, 'utf8');
      database = JSON.parse(content);
    } else {
      console.warn("db.json not found, using empty state");
    }
  } catch (err) {
    console.error("Error reading database:", err);
  }
}

function saveDatabase() {
  try {
    fs.writeFileSync(DB_PATH, JSON.stringify(database, null, 2), 'utf8');
  } catch (err) {
    console.error("Error writing to database:", err);
  }
}

loadDatabase();

// SSE Clients
let clients = [];

function sendSSEToAll(type, data) {
  const message = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  clients.forEach(client => client.res.write(message));
}

// SSE Connection Endpoint
app.get('/api/signals/stream', (req, res) => {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive'
  });
  
  const clientId = Date.now();
  const newClient = { id: clientId, res };
  clients.push(newClient);
  
  // Send initial load
  res.write(`event: initial\ndata: ${JSON.stringify({ signals: database.signals, history: database.history })}\n\n`);
  
  req.on('close', () => {
    clients = clients.filter(c => c.id !== clientId);
  });
});

// REST API Endpoints
app.get('/api/signals', (req, res) => {
  res.json(database.signals);
});

app.get('/api/history', (req, res) => {
  res.json(database.history);
});

// Action endpoint (Trade/Ignore)
app.post('/api/signals/:id/action', (req, res) => {
  const { id } = req.params;
  const { action } = req.body; // "Executed" or "Ignored"
  
  if (!['Executed', 'Ignored'].includes(action)) {
    return res.status(400).json({ error: "Invalid action type. Must be 'Executed' or 'Ignored'." });
  }

  const signalIdx = database.signals.findIndex(s => s.id === id);
  if (signalIdx === -1) {
    return res.status(404).json({ error: "Signal not found" });
  }

  const signal = database.signals[signalIdx];
  
  // Calculate a random mockup P&L for executed trades, or "--" for ignored
  let pnl = "--";
  if (action === "Executed") {
    const isWin = Math.random() > 0.35; // 65% win rate
    const pnlVal = (Math.random() * 5 + 0.1).toFixed(1);
    pnl = `${isWin ? '+' : '-'}${pnlVal}%`;
  }

  const historyItem = {
    id: signal.id,
    asset: signal.asset,
    type: signal.type,
    grade: signal.grade,
    price: signal.price,
    status: action,
    pnl: pnl,
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    timestamp: Date.now()
  };

  // Move from active signals to history
  database.signals.splice(signalIdx, 1);
  database.history.unshift(historyItem);
  saveDatabase();

  // Stream updates to all clients
  sendSSEToAll('update', { signals: database.signals, history: database.history });
  
  res.json({ success: true, historyItem });
});

// Simulate a new incoming signal (Database Drop Simulation)
const ASSETS = ['BTC/USD', 'ETH/USD', 'SOL/USD', 'ADA/USD', 'DOT/USD', 'AVAX/USD', 'LINK/USD', 'XRP/USD'];
const GRADES = ['A+', 'A', 'B', 'C'];
const DIRECTIONS = ['LONG', 'SHORT'];
const TRAFFIC_LIGHTS = { 'LONG': 'GREEN', 'SHORT': 'RED', 'WAIT': 'AMBER' };

app.post('/api/signals/simulate', (req, res) => {
  const asset = ASSETS[Math.floor(Math.random() * ASSETS.length)];
  const grade = GRADES[Math.floor(Math.random() * GRADES.length)];
  const direction = DIRECTIONS[Math.floor(Math.random() * DIRECTIONS.length)];
  
  // Wait states occur randomly
  const type = Math.random() > 0.8 ? 'WAIT' : direction;
  const trafficLight = TRAFFIC_LIGHTS[type];
  
  // Set prices dynamically
  let price = parseFloat((Math.random() * 1000 + 10).toFixed(2));
  if (asset.startsWith('BTC')) price = parseFloat((60000 + Math.random() * 8000).toFixed(2));
  else if (asset.startsWith('ETH')) price = parseFloat((3000 + Math.random() * 600).toFixed(2));
  
  const tp = parseFloat((type === 'LONG' ? price * 1.05 : price * 0.95).toFixed(2));
  const sl = parseFloat((type === 'LONG' ? price * 0.97 : price * 1.03).toFixed(2));
  
  const idNum = Math.floor(1000 + Math.random() * 9000);
  const newSignal = {
    id: `SIG-${idNum}-${grade.charAt(0)}`,
    asset,
    type,
    grade,
    price,
    tp,
    sl,
    time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    trafficLight,
    timestamp: Date.now()
  };

  database.signals.unshift(newSignal);
  saveDatabase();

  // Push update instantly via SSE
  sendSSEToAll('update', { signals: database.signals, history: database.history });

  res.json({ success: true, signal: newSignal });
});

app.listen(PORT, () => {
  console.log(`Backend server listening on port ${PORT}`);
});
