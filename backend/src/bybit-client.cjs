const crypto = require('crypto');
const https = require('https');

const DEMO_BASE = 'https://api-demo.bybit.com';
const MAINNET_BASE = 'https://api.bybit.com';
const RECV_WINDOW = '20000';

const apiKey = process.env.BYBIT_DEMO_API_KEY || process.env.BYBIT_API_KEY || '';
const apiSecret = process.env.BYBIT_DEMO_API_SECRET || process.env.BYBIT_API_KEY_SECRET || '';
const isDemo = process.env.EXECUTION_MODE === 'demo';
const baseUrl = isDemo ? DEMO_BASE : MAINNET_BASE;

function sign(timestamp, payload) {
  const paramStr = timestamp + apiKey + RECV_WINDOW + payload;
  return crypto.createHmac('sha256', apiSecret).update(paramStr).digest('hex');
}

function request(method, path, params, body) {
  return new Promise((resolve) => {
    if (!apiKey || !apiSecret) {
      resolve({ retCode: -1, retMsg: 'Bybit API keys not configured' });
      return;
    }

    const timestamp = Date.now().toString();
    let url = baseUrl + path;
    let payload = '';

    if (params) {
      const qs = new URLSearchParams(params).toString();
      url += '?' + qs;
      payload = qs;
    } else if (body) {
      payload = JSON.stringify(body);
    }

    const signature = sign(timestamp, payload);

    const headers = {
      'X-BAPI-API-KEY': apiKey,
      'X-BAPI-TIMESTAMP': timestamp,
      'X-BAPI-RECV-WINDOW': RECV_WINDOW,
      'X-BAPI-SIGN': signature,
      'Content-Type': 'application/json',
    };

    const data = body ? JSON.stringify(body) : null;
    const urlObj = new URL(url);

    const options = {
      hostname: urlObj.hostname,
      port: 443,
      path: urlObj.pathname + urlObj.search,
      method: method,
      headers: headers,
    };

    if (data) {
      options.headers['Content-Length'] = Buffer.byteLength(data);
    }

    const req = https.request(options, (res) => {
      let responseData = '';
      res.on('data', chunk => responseData += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(responseData);
          resolve(result);
        } catch (e) {
          resolve({ retCode: -1, retMsg: 'Failed to parse response: ' + e.message });
        }
      });
    });

    req.on('error', (err) => {
      resolve({ retCode: -1, retMsg: err.message });
    });

    req.setTimeout(10000, () => {
      req.destroy();
      resolve({ retCode: -1, retMsg: 'Request timeout' });
    });

    if (data) req.write(data);
    req.end();
  });
}

async function getWalletBalance(accountType = 'UNIFIED') {
  const result = await request('GET', '/v5/account/wallet-balance', { accountType });
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list[0] || null;
  }
  return null;
}

async function getPositions(category = 'linear', settleCoin = 'USDT') {
  const result = await request('GET', '/v5/position/list', { category, settleCoin });
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list.filter(p => parseFloat(p.size) > 0);
  }
  return [];
}

async function getOpenOrders(category = 'linear') {
  const result = await request('GET', '/v5/order/realtime', { category });
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list;
  }
  return [];
}

async function getTickers(symbol, category = 'linear') {
  const params = { category };
  if (symbol) params.symbol = symbol;
  const result = await request('GET', '/v5/market/tickers', params);
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list;
  }
  return [];
}

async function getClosedPnl(category = 'linear', limit = 50) {
  const result = await request('GET', '/v5/position/closed-pnl', { category, limit: String(limit) });
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list;
  }
  return [];
}

async function getTradeHistory(category = 'linear', limit = 50) {
  const result = await request('GET', '/v5/execution/list', { category, limit: String(limit) });
  if (result.retCode === 0 && result.result && result.result.list) {
    return result.result.list;
  }
  return [];
}

module.exports = {
  isConfigured: !!apiKey && !!apiSecret,
  isDemo,
  baseUrl,
  getWalletBalance,
  getPositions,
  getOpenOrders,
  getTickers,
  getClosedPnl,
  getTradeHistory,
};
