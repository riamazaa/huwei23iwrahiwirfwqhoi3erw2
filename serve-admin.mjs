import http from 'node:http';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = __dirname;
const siteRoot = root;
const dataDir = path.resolve(__dirname, 'data');
const port = Number(process.env.PORT ?? 8787);

const SITE_PASSWORD = '02020829819898298891899821987288UIUU!UU!!UU';
const AUTH_COOKIE = 'kagama_session';
const SESSION_MAX_AGE = 30 * 24 * 60 * 60;

function hashPassword(pw) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(pw, salt, 64).toString('hex');
  return `$scrypt$${salt}$${hash}`;
}

function verifyPassword(pw, stored) {
  try {
    const parts = stored.split('$');
    const salt = parts[2];
    const hash = parts[3];
    const testHash = crypto.scryptSync(pw, salt, 64).toString('hex');
    return testHash === hash;
  } catch { return false; }
}

const passwordHash = hashPassword(SITE_PASSWORD);

function parseCookies(header) {
  const cookies = {};
  if (!header) return cookies;
  header.split(';').forEach(c => {
    const [k, ...v] = c.trim().split('=');
    if (k) cookies[k.trim()] = decodeURIComponent(v.join('='));
  });
  return cookies;
}

const sessions = new Map();

function createSession() {
  const id = crypto.randomBytes(32).toString('hex');
  sessions.set(id, { createdAt: Date.now(), expiresAt: Date.now() + SESSION_MAX_AGE * 1000 });
  return id;
}

function isValidSession(sessionId) {
  if (!sessionId) return false;
  const s = sessions.get(sessionId);
  if (!s) return false;
  if (Date.now() > s.expiresAt) { sessions.delete(sessionId); return false; }
  return true;
}

const rateLimit = new Map();

function checkRateLimit(ip) {
  const now = Date.now();
  const entry = rateLimit.get(ip);
  if (!entry || now > entry.resetAt) {
    rateLimit.set(ip, { count: 1, resetAt: now + 15 * 60 * 1000 });
    return { allowed: true, attempts: 1 };
  }
  entry.count++;
  return { allowed: entry.count <= 5, attempts: entry.count };
}

function isAuthed(request) {
  const cookies = parseCookies(request.headers.cookie);
  return isValidSession(cookies[AUTH_COOKIE]);
}

function authPageHtml(errorMsg, attemptsLeft) {
  const errDiv = errorMsg
    ? '<div class="gate-error">' + escapeHtml(errorMsg) + '</div>'
    : '';
  const attemptsMsg = attemptsLeft != null && attemptsLeft <= 2 && attemptsLeft > 0
    ? '<div class="gate-attempts">' + attemptsLeft + ' attempt' + (attemptsLeft === 1 ? '' : 's') + ' remaining before lockout</div>'
    : attemptsLeft === 0
    ? '<div class="gate-error">Too many attempts. Try again in 15 minutes.</div>'
    : '';
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>KaGaMa - Site Access</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
    @keyframes slideUp { from { transform: translateY(30px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    body {
      margin: 0; height: 100vh; display: flex; justify-content: center; align-items: center;
      background: linear-gradient(135deg, #081b2d 0%, #0a2540 50%, #0d1f35 100%);
      font-family: 'Segoe UI', Arial, sans-serif;
      animation: fadeIn 0.4s ease;
    }
    .card {
      width: 350px; max-width: 90vw; padding: 36px 30px;
      background: rgba(22, 41, 63, 0.75);
      backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px; text-align: center;
      box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5), 0 0 40px rgba(255, 59, 26, 0.06);
      animation: slideUp 0.5s ease;
    }
    .logo { width: 64px; height: 64px; border-radius: 10px; margin-bottom: 16px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
    h2 { color: #fff; font-size: 22px; margin-bottom: 6px; font-weight: 700; }
    p { color: #a0aec0; font-size: 14px; margin-bottom: 20px; }
    input {
      width: 100%; box-sizing: border-box; padding: 13px 14px; font-size: 14px;
      background: rgba(8, 27, 45, 0.9); color: #fff;
      border: 1px solid rgba(255, 59, 26, 0.4); border-radius: 6px;
      outline: none; transition: border-color 0.2s, box-shadow 0.2s;
    }
    input:focus { border-color: #ff3b1a; box-shadow: 0 0 12px rgba(255, 59, 26, 0.25); }
    input::placeholder { color: #5a6a7a; }
    button {
      width: 100%; margin-top: 16px; padding: 13px; border: none;
      background: linear-gradient(135deg, #ff3b1a, #e63200); color: #fff;
      font-size: 15px; font-weight: 700; cursor: pointer;
      border-radius: 6px; text-transform: uppercase; letter-spacing: 0.5px;
      transition: opacity 0.2s, transform 0.15s, box-shadow 0.2s;
    }
    button:hover { opacity: 0.9; transform: translateY(-1px); box-shadow: 0 6px 20px rgba(255, 59, 26, 0.3); }
    button:active { transform: translateY(0); }
    .gate-error {
      color: #ff5252; font-size: 13px; margin-bottom: 12px;
      padding: 8px 12px; background: rgba(255, 82, 82, 0.1);
      border-radius: 4px; animation: fadeIn 0.3s ease;
    }
    .gate-attempts { color: #ff9800; font-size: 11px; margin-top: 8px; }
  </style>
</head>
<body>
  <div class="card">
    <img src="/static/img/kogama-logo.webp" class="logo" alt="KaGaMa">
    <h2>Site Access</h2>
    <p>Enter the access password to continue</p>
    ${errDiv}
    <form method="POST" action="/auth">
      <input type="password" name="password" placeholder="Password" autofocus required>
      <button type="submit">ENTER</button>
    </form>
    ${attemptsMsg}
  </div>
</body>
</html>`;
}

const mimeTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.ico', 'image/x-icon'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
  ['.ttf', 'font/ttf'],
  ['.eot', 'application/vnd.ms-fontobject'],
]);

const usersFile = path.join(dataDir, 'users.json');

async function loadUsers() {
  try {
    const raw = await fs.readFile(usersFile, 'utf8');
    return JSON.parse(raw);
  } catch {
    return { users: [] };
  }
}

async function saveUsers(data) {
  await fs.writeFile(usersFile, JSON.stringify(data, null, 2), 'utf8');
}

const avatarsFile = path.join(dataDir, 'avatars.json');
const modelsFile = path.join(dataDir, 'models.json');

async function loadAvatars() {
  try {
    const raw = await fs.readFile(avatarsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return { avatars: [] }; }
}

async function saveAvatars(data) {
  await fs.writeFile(avatarsFile, JSON.stringify(data, null, 2), 'utf8');
}

async function loadModels() {
  try {
    const raw = await fs.readFile(modelsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return { models: [] }; }
}

async function saveModels(data) {
  await fs.writeFile(modelsFile, JSON.stringify(data, null, 2), 'utf8');
}

const newsFile = path.resolve(__dirname, 'data', 'news.json');

async function loadNews() {
  try {
    const raw = await fs.readFile(newsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return []; }
}

async function saveNews(data) {
  await fs.writeFile(newsFile, JSON.stringify(data, null, 2), 'utf8');
}

const commentsFile = path.resolve(__dirname, 'data', 'comments.json');

async function loadComments() {
  try {
    const raw = await fs.readFile(commentsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return {}; }
}

async function saveComments(data) {
  await fs.writeFile(commentsFile, JSON.stringify(data, null, 2), 'utf8');
}

const wallpostsFile = path.resolve(__dirname, 'data', 'wallposts.json');

async function loadWallposts() {
  try {
    const raw = await fs.readFile(wallpostsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return {}; }
}

async function saveWallposts(data) {
  await fs.writeFile(wallpostsFile, JSON.stringify(data, null, 2), 'utf8');
}

const gamecommentsFile = path.resolve(__dirname, 'data', 'gamecomments.json');

async function loadGameComments() {
  try {
    const raw = await fs.readFile(gamecommentsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return {}; }
}

async function saveGameComments(data) {
  await fs.writeFile(gamecommentsFile, JSON.stringify(data, null, 2), 'utf8');
}

const productcommentsFile = path.resolve(__dirname, 'data', 'productcomments.json');

async function loadProductComments() {
  try {
    const raw = await fs.readFile(productcommentsFile, 'utf8');
    return JSON.parse(raw);
  } catch { return {}; }
}

async function saveProductComments(data) {
  await fs.writeFile(productcommentsFile, JSON.stringify(data, null, 2), 'utf8');
}

async function readBody(request) {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks).toString('utf8');
  try { return JSON.parse(body); } catch { return body; }
}

function sendJson(response, data, status = 200) {
  const body = JSON.stringify(data);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'access-control-allow-origin': '*',
    'access-control-allow-methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'access-control-allow-headers': 'Content-Type',
  });
  response.end(body);
}

function sendHtml(response, html) {
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  response.end(html);
}

function findUser(users, query) {
  const q = String(query).toLowerCase();
  return users.find(u =>
    String(u.id) === q ||
    (u.username && u.username.toLowerCase() === q)
  );
}

function ensureUser(users, id) {
  let user = users.find(u => String(u.id) === String(id));
  if (!user) {
    user = {
      id: Number(id),
      username: `User_${id}`,
      gold: 0,
      xp: 0,
      level: 1,
      banned: false,
      softbanned: false,
      banReason: '',
      softbanExpiry: null,
      createdAt: new Date().toISOString(),
    };
    users.push(user);
  }
  return user;
}

async function handleAdminAPI(request, response) {
  const url = new URL(request.url, `http://${request.headers.host}`);
  const parts = url.pathname.split('/').filter(Boolean);
  const method = request.method;

  if (method === 'OPTIONS') {
    sendJson(response, { ok: true });
    return;
  }

  const action = parts[2];

  if (action === 'users' && method === 'GET') {
    const data = await loadUsers();
    const search = url.searchParams.get('search') || '';
    let users = data.users;
    if (search) {
      const q = search.toLowerCase();
      users = users.filter(u =>
        String(u.id).includes(q) ||
        (u.username && u.username.toLowerCase().includes(q))
      );
    }
    sendJson(response, { users });
    return;
  }

  if (action === 'user' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const user = ensureUser(data.users, id);
    if (body.username) user.username = body.username;
    await saveUsers(data);
    sendJson(response, { user });
    return;
  }

  if (action === 'ban' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    const reason = body.reason || '';
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const user = ensureUser(data.users, id);
    user.banned = true;
    user.banReason = reason;
    user.bannedAt = new Date().toISOString();
    await saveUsers(data);
    sendJson(response, { user, message: `User ${user.username} has been banned.` });
    return;
  }

  if (action === 'unban' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const user = ensureUser(data.users, id);
    user.banned = false;
    user.banReason = '';
    user.bannedAt = null;
    await saveUsers(data);
    sendJson(response, { user, message: `User ${user.username} has been unbanned.` });
    return;
  }

  if (action === 'softban' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    const hours = Number(body.hours) || 24;
    const reason = body.reason || '';
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const user = ensureUser(data.users, id);
    user.softbanned = true;
    user.softbanReason = reason;
    user.softbannedAt = new Date().toISOString();
    user.softbanExpiry = new Date(Date.now() + hours * 3600000).toISOString();
    await saveUsers(data);
    sendJson(response, { user, message: `User ${user.username} softbanned for ${hours} hours.` });
    return;
  }

  if (action === 'unsoftban' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const user = ensureUser(data.users, id);
    user.softbanned = false;
    user.softbanReason = '';
    user.softbannedAt = null;
    user.softbanExpiry = null;
    await saveUsers(data);
    sendJson(response, { user, message: `User ${user.username} has been unbanned.` });
    return;
  }

  if (action === 'grant' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    const type = body.type;
    const amount = Number(body.amount);
    if (!id || !type || isNaN(amount)) {
      sendJson(response, { error: 'Missing id, type, or amount' }, 400);
      return;
    }
    const user = ensureUser(data.users, id);
    if (type === 'gold') {
      user.gold = Math.max(0, (user.gold || 0) + amount);
    } else if (type === 'xp') {
      user.xp = Math.max(0, (user.xp || 0) + amount);
    } else if (type === 'level') {
      user.level = Math.max(1, (user.level || 1) + amount);
    } else {
      sendJson(response, { error: 'Invalid type. Use gold, xp, or level' }, 400);
      return;
    }
    await saveUsers(data);
    sendJson(response, { user, message: `Granted ${amount} ${type} to ${user.username}.` });
    return;
  }

  if (action === 'set' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    const type = body.type;
    const value = Number(body.value);
    if (!id || !type || isNaN(value)) {
      sendJson(response, { error: 'Missing id, type, or value' }, 400);
      return;
    }
    const user = ensureUser(data.users, id);
    if (type === 'gold') user.gold = Math.max(0, value);
    else if (type === 'xp') user.xp = Math.max(0, value);
    else if (type === 'level') user.level = Math.max(1, value);
    else {
      sendJson(response, { error: 'Invalid type' }, 400);
      return;
    }
    await saveUsers(data);
    sendJson(response, { user, message: `Set ${user.username}'s ${type} to ${value}.` });
    return;
  }

  if (action === 'delete' && method === 'POST') {
    const data = await loadUsers();
    const body = await readBody(request);
    const id = body.id;
    if (!id) { sendJson(response, { error: 'Missing user id' }, 400); return; }
    const idx = data.users.findIndex(u => String(u.id) === String(id));
    if (idx === -1) { sendJson(response, { error: 'User not found' }, 404); return; }
    const removed = data.users.splice(idx, 1)[0];
    await saveUsers(data);
    sendJson(response, { message: `Deleted user ${removed.username}.` });
    return;
  }

  if (action === 'publish-avatar' && method === 'POST') {
    const body = await readBody(request);
    const { name, imageUrl, price, publisher } = body;
    if (!name || !imageUrl) { sendJson(response, { error: 'Missing name or imageUrl' }, 400); return; }
    const data = await loadAvatars();
    const id = 'a-' + Date.now();
    const item = { id, name, imageUrl, price: Number(price) || 0, publisher: publisher || 'Admin', publishedAt: new Date().toISOString(), sold: 0, likes: 0 };
    data.avatars.push(item);
    await saveAvatars(data);
    sendJson(response, { item });
    return;
  }

  if (action === 'avatars' && method === 'GET') {
    const data = await loadAvatars();
    sendJson(response, { avatars: data.avatars || [] });
    return;
  }

  if (action === 'delete-avatar' && method === 'POST') {
    const body = await readBody(request);
    const { id } = body;
    if (!id) { sendJson(response, { error: 'Missing id' }, 400); return; }
    const data = await loadAvatars();
    data.avatars = (data.avatars || []).filter(a => a.id !== id);
    await saveAvatars(data);
    sendJson(response, { message: 'Deleted.' });
    return;
  }

  if (action === 'publish-model' && method === 'POST') {
    const body = await readBody(request);
    const { name, imageUrl, price, publisher } = body;
    if (!name || !imageUrl) { sendJson(response, { error: 'Missing name or imageUrl' }, 400); return; }
    const data = await loadModels();
    const id = 'i-' + Date.now();
    const item = { id, name, imageUrl, price: Number(price) || 0, publisher: publisher || 'Admin', publishedAt: new Date().toISOString(), sold: 0, likes: 0 };
    data.models.push(item);
    await saveModels(data);
    sendJson(response, { item });
    return;
  }

  if (action === 'models' && method === 'GET') {
    const data = await loadModels();
    sendJson(response, { models: data.models || [] });
    return;
  }

  if (action === 'delete-model' && method === 'POST') {
    const body = await readBody(request);
    const { id } = body;
    if (!id) { sendJson(response, { error: 'Missing id' }, 400); return; }
    const data = await loadModels();
    data.models = (data.models || []).filter(m => m.id !== id);
    await saveModels(data);
    sendJson(response, { message: 'Deleted.' });
    return;
  }

  sendJson(response, { error: 'Unknown action' }, 404);
}

function marketplaceDetailPage(item, type) {
  const isAvatar = type === 'avatar';
  const typeName = isAvatar ? 'Avatar' : 'Model';
  const bgClass = isAvatar ? 'product-image-avatar' : 'product-image-model';
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${escapeHtml(item.name)} - KaGaMa Marketplace</title>
<link rel="icon" href="/static/img/KaGaMa-logo.webp" type="image/webp">
<link rel="stylesheet" href="/static.kogstatic.com/0000/d9c1e5da76aa8de67a3be7e8541d0ba21f064294/app-less.css" type="text/css">
<link rel="stylesheet" href="/static.kogstatic.com/0000/d9c1e5da76aa8de67a3be7e8541d0ba21f064294/app-sass.css" type="text/css">
<link href="https://fonts.googleapis.com/css?family=Open+Sans:400,700,800&subset=latin" rel="stylesheet" type="text/css">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<meta name="theme-color" content="#000b1d">
</head>
<body id="root-page-mobile" class="">
<header id="pageheader"><div class="pageheader-inner"><a href="/games/" title="PlayKaGaMa" class="logo"><div class="logo-image"></div></a><nav class="menu anonymous"><ol><li class="game "><a href="/games/"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M21 6H3c-1.1 0-2 .9-2 2v8c0 1.1.9 2 2 2h18c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm-10 7H8v3H6v-3H3v-2h3V8h2v3h3v2zm4.5 2c-.83 0-1.5-.67-1.5-1.5s.67-1.5 1.5-1.5 1.5.67 1.5 1.5-.67 1.5-1.5 1.5zm4-3c-.83 0-1.5-.67-1.5-1.5S18.67 9 19.5 9s1.5.67 1.5 1.5-.67 1.5-1.5 1.5z"/></svg><span class="text">Play</span></a></li><li class="shop active"><a href="/marketplace/"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M7 18c-1.1 0-1.99.9-1.99 2S5.9 22 7 22s2-.9 2-2-.9-2-2-2zM1 2v2h2l3.6 7.59-1.35 2.45c-.16.28-.25.61-.25.96 0 1.1.9 2 2 2h12v-2H7.42c-.14 0-.25-.11-.25-.25l.03-.12.9-1.63h7.45c.75 0 1.41-.41 1.75-1.03l3.58-6.49c.08-.14.12-.31.12-.48 0-.55-.45-1-1-1H5.21l-.94-2H1zm16 16c-1.1 0-1.99.9-1.99 2s.89 2 1.99 2 2-.9 2-2-.9-2-2-2z"/></svg><span class="text">Shop</span></a></li><li class="news "><a href="/news/"><svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="white"><path d="M20 2H4c-1.1 0-1.99.9-1.99 2L2 22l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-2 12H6v-2h12v2zm0-3H6V9h12v2zm0-3H6V6h12v2z"/></svg><span class="text">News</span></a></li></ol></nav><div id="account-header"></div></div></header>
<div id="content" class="">
  <div id="content-container">
    <div id="spacer-cell"></div>
    <div id="main-content">
      <div id="notification"></div>
      <div id="mobile-page">
        <div id="mobile-page-content">
          <div id="marketplace-detail-content">
            <article id="product-detail" class="content-content product-detail-${type}" itemscope="" itemtype="http://schema.org/Product">
              <div class="product-container">
                <div class="product-purchase-container">
                  <div class="product-purchase">
                    <div class="product-image ${bgClass}" style="background-image: url(${escapeHtml(item.imageUrl)});"></div>
                  </div>
                  <div id="product-purchase-link">
                    <div>
                      <button class="pure-button pure-button-primary pure-button-xlarge purchase-button" data-product-id="${escapeHtml(item.id)}" data-product-type="${type}" data-product-price="${item.price}">
                        <i class="icon-lock"></i>Unlock
                      </button>
                      <div id="purchase-confirm-container"></div>
                    </div>
                  </div>
                </div>
                <header class="product-header">
                  <h1 class="page-header" itemprop="name">${escapeHtml(item.name)}</h1>
                  <h4>${typeName}</h4>
                  <div class="product-creator">Created by <a href="/profile/1/">${escapeHtml(item.publisher || 'Admin')}</a></div>
                  <div class="product-meta">
                    <div class="product-sold-stats">
                      <ul class="product-stat-list">
                        <li>
                          <div id="like"><div><div class="like tool-tip like-inactive" data-placement="bottom"><a href="" class="pure-button pure-button-xsmall pure-button-secondary create" data-like-id="${escapeHtml(item.id)}"><i class="icon-heart"></i>Like<span class="like-count"><span class="arrow-left"></span><span class="like-num" data-like-num="${escapeHtml(item.id)}">${item.likes || 0}</span></span></a></div></div></div>
                        </li>
                        <li class="photo"><a href="${escapeHtml(item.imageUrl)}" target="_blank"><i class="icon-camera"></i></a></li>
                        <li class="sold"><span data-sold-num="${escapeHtml(item.id)}">${item.sold || 0}</span>&nbsp;Sold</li>
                      </ul>
                    </div>
                    <div class="product-price">
                      <div class="price-gold"><i class="sprite sprite-icon_gold_dark"></i> ${item.price} Gold</div>
                    </div>
                  </div>
                </header>
              </div>
              <div class="product-comments">
                <div id="marketplace-comments"></div>
              </div>
            </article>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>
<div id="cookies-popup"></div>
<div id="chat-extended-side"></div>
<div id="birthday-modal"></div>
<script type="text/javascript">
var PRODUCT_DATA = ${JSON.stringify({ id: item.id, name: item.name, type, price: item.price, imageUrl: item.imageUrl, publisher: item.publisher || 'Admin' })};
</script>
</body>
</html>`;
}

function adminPage() {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KaGaMa Admin Panel</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0e1519; color: #e0e0e0; font-family: "Open Sans", Arial, sans-serif; }
  header { background: #0a0f14; border-bottom: 1px solid #1e2a33; padding: 12px 24px; display: flex; align-items: center; gap: 16px; position: sticky; top: 0; z-index: 100; }
  header h1 { color: #8ac943; font-size: 20px; font-weight: 800; }
  header .badge { background: #ff370f; color: #fff; font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 700; }
  .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
  .search-bar { display: flex; gap: 8px; margin-bottom: 20px; }
  .search-bar input, .search-bar button { padding: 10px 14px; border: 1px solid #2a3a45; border-radius: 4px; font-size: 14px; }
  .search-bar input { flex: 1; background: #161f27; color: #e0e0e0; }
  .search-bar button { background: #8ac943; color: #0e1519; font-weight: 700; cursor: pointer; border: none; }
  .search-bar button:hover { background: #9cd85a; }
  .panel { background: #161f27; border: 1px solid #1e2a33; border-radius: 8px; padding: 20px; margin-bottom: 20px; }
  .panel h2 { color: #8ac943; font-size: 16px; margin-bottom: 16px; border-bottom: 1px solid #1e2a33; padding-bottom: 8px; }
  .form-row { display: flex; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
  .form-row input, .form-row select { padding: 8px 12px; border: 1px solid #2a3a45; border-radius: 4px; background: #0e1519; color: #e0e0e0; font-size: 13px; }
  .form-row input { flex: 1; min-width: 120px; }
  .form-row select { min-width: 100px; }
  .btn { padding: 8px 16px; border: none; border-radius: 4px; font-size: 13px; font-weight: 700; cursor: pointer; }
  .btn-red { background: #ff370f; color: #fff; }
  .btn-red:hover { background: #ff5a33; }
  .btn-orange { background: #ff9800; color: #fff; }
  .btn-orange:hover { background: #ffb740; }
  .btn-green { background: #8ac943; color: #0e1519; }
  .btn-green:hover { background: #9cd85a; }
  .btn-blue { background: #1e9bff; color: #fff; }
  .btn-blue:hover { background: #4db8ff; }
  .btn-gray { background: #3a4a55; color: #e0e0e0; }
  .btn-gray:hover { background: #4a5a65; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #1e2a33; font-size: 13px; }
  th { color: #8ac943; font-weight: 700; background: #0e1519; position: sticky; top: 60px; }
  tr:hover { background: #1a252e; }
  .banned { color: #ff370f; font-weight: 700; }
  .softbanned { color: #ff9800; font-weight: 700; }
  .stat { display: inline-block; margin-right: 8px; }
  .actions { display: flex; gap: 4px; flex-wrap: wrap; }
  #toast { position: fixed; bottom: 20px; right: 20px; background: #1a252e; border: 1px solid #8ac943; color: #8ac943; padding: 12px 20px; border-radius: 6px; font-weight: 700; display: none; z-index: 200; }
  .gold { color: #ffd800; }
  .xp { color: #b447e8; }
  .level { color: #1e9bff; }
  .empty { color: #5a6a75; text-align: center; padding: 40px; }
</style>
</head>
<body>
<header>
  <h1>KaGaMa Admin Panel</h1>
  <span class="badge">BETA</span>
</header>
<div class="container">

  <div class="search-bar">
    <input type="text" id="searchInput" placeholder="Search by username or ID..." onkeyup="if(event.key==='Enter')searchUsers()">
    <button onclick="searchUsers()">Search</button>
    <button class="btn btn-gray" onclick="loadUsers()">Show All</button>
  </div>

  <div class="panel">
    <h2>Quick Actions</h2>
    <div class="form-row">
      <input type="number" id="actionUserId" placeholder="User ID">
      <input type="text" id="actionReason" placeholder="Reason (optional)">
      <button class="btn btn-red" onclick="banUser()">Ban</button>
      <button class="btn btn-red" onclick="unbanUser()">Unban</button>
      <button class="btn btn-orange" onclick="softbanUser()">Softban 24h</button>
      <button class="btn btn-orange" onclick="unsoftbanUser()">Remove Softban</button>
    </div>
    <div class="form-row">
      <input type="number" id="grantUserId" placeholder="User ID">
      <select id="grantType">
        <option value="gold">Gold</option>
        <option value="xp">XP</option>
        <option value="level">Level</option>
      </select>
      <input type="number" id="grantAmount" placeholder="Amount" value="100">
      <button class="btn btn-green" onclick="grantResource()">Grant</button>
      <input type="number" id="setValue" placeholder="Set to value">
      <button class="btn btn-blue" onclick="setResource()">Set</button>
    </div>
    <div class="form-row">
      <input type="number" id="createUserId" placeholder="New User ID">
      <input type="text" id="createUsername" placeholder="Username">
      <button class="btn btn-green" onclick="createUser()">Create User</button>
    </div>
  </div>

  <div class="panel">
    <h2>Users (<span id="userCount">0</span>)</h2>
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Username</th>
          <th>Gold</th>
          <th>XP</th>
          <th>Level</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="userTable"></tbody>
    </table>
    <div class="empty" id="emptyMsg">No users yet. Create one or search for an ID.</div>
  </div>
</div>

<div id="toast"></div>

<script>
const API = '/admin/api';

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.style.display = 'block';
  setTimeout(() => t.style.display = 'none', 3000);
}

async function api(action, body = {}) {
  const res = await fetch(API + '/' + action, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

async function loadUsers(search = '') {
  const url = search ? API + '/users?search=' + encodeURIComponent(search) : API + '/users';
  const res = await fetch(url);
  const data = await res.json();
  renderUsers(data.users || []);
}

function renderUsers(users) {
  const tbody = document.getElementById('userTable');
  const empty = document.getElementById('emptyMsg');
  document.getElementById('userCount').textContent = users.length;
  if (users.length === 0) {
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';
  tbody.innerHTML = users.map(u => {
    let status = '<span style="color:#8ac943">Active</span>';
    if (u.banned) status = '<span class="banned">BANNED</span>';
    else if (u.softbanned) status = '<span class="softbanned">Softbanned</span>';
    return '<tr>' +
      '<td>' + u.id + '</td>' +
      '<td>' + esc(u.username) + '</td>' +
      '<td class="gold">' + (u.gold || 0) + '</td>' +
      '<td class="xp">' + (u.xp || 0) + '</td>' +
      '<td class="level">' + (u.level || 1) + '</td>' +
      '<td>' + status + '</td>' +
      '<td class="actions">' +
        '<button class="btn btn-red" onclick="quickBan(' + u.id + ')">Ban</button>' +
        '<button class="btn btn-orange" onclick="quickSoftban(' + u.id + ')">Softban</button>' +
        '<button class="btn btn-green" onclick="quickGold(' + u.id + ')">+Gold</button>' +
      '</td>' +
    '</tr>';
  }).join('');
}

function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

function searchUsers() {
  loadUsers(document.getElementById('searchInput').value);
}

async function banUser() {
  const id = document.getElementById('actionUserId').value;
  const reason = document.getElementById('actionReason').value;
  if (!id) { toast('Enter a user ID'); return; }
  const r = await api('ban', { id: Number(id), reason });
  toast(r.message || r.error);
  loadUsers();
}

async function unbanUser() {
  const id = document.getElementById('actionUserId').value;
  if (!id) { toast('Enter a user ID'); return; }
  const r = await api('unban', { id: Number(id) });
  toast(r.message || r.error);
  loadUsers();
}

async function softbanUser() {
  const id = document.getElementById('actionUserId').value;
  const reason = document.getElementById('actionReason').value;
  if (!id) { toast('Enter a user ID'); return; }
  const r = await api('softban', { id: Number(id), reason, hours: 24 });
  toast(r.message || r.error);
  loadUsers();
}

async function unsoftbanUser() {
  const id = document.getElementById('actionUserId').value;
  if (!id) { toast('Enter a user ID'); return; }
  const r = await api('unsoftban', { id: Number(id) });
  toast(r.message || r.error);
  loadUsers();
}

async function grantResource() {
  const id = document.getElementById('grantUserId').value;
  const type = document.getElementById('grantType').value;
  const amount = Number(document.getElementById('grantAmount').value);
  if (!id || !amount) { toast('Enter user ID and amount'); return; }
  const r = await api('grant', { id: Number(id), type, amount });
  toast(r.message || r.error);
  loadUsers();
}

async function setResource() {
  const id = document.getElementById('grantUserId').value;
  const type = document.getElementById('grantType').value;
  const value = Number(document.getElementById('setValue').value);
  if (!id || isNaN(value)) { toast('Enter user ID and value'); return; }
  const r = await api('set', { id: Number(id), type, value });
  toast(r.message || r.error);
  loadUsers();
}

async function createUser() {
  const id = document.getElementById('createUserId').value;
  const username = document.getElementById('createUsername').value;
  if (!id || !username) { toast('Enter user ID and username'); return; }
  const r = await api('user', { id: Number(id), username });
  toast(r.message || 'User created');
  loadUsers();
}

async function quickBan(id) {
  if (!confirm('Ban user ' + id + '?')) return;
  const r = await api('ban', { id, reason: 'Admin action' });
  toast(r.message || r.error);
  loadUsers();
}

async function quickSoftban(id) {
  if (!confirm('Softban user ' + id + ' for 24h?')) return;
  const r = await api('softban', { id, hours: 24, reason: 'Admin action' });
  toast(r.message || r.error);
  loadUsers();
}

async function quickGold(id) {
  const amount = prompt('Amount of gold to give:', '100');
  if (!amount) return;
  const r = await api('grant', { id, type: 'gold', amount: Number(amount) });
  toast(r.message || r.error);
  loadUsers();
}

loadUsers();
</script>
</body>
</html>`;
}

const server = http.createServer(async (request, response) => {
  try {
    const url = new URL(request.url ?? '/', `http://${request.headers.host}`);
    let pathname = normalizeArchivePath(url.pathname);

    // Auth routes (exempt from gate)
    if (pathname === '/auth' && request.method === 'GET') {
      sendHtml(response, authPageHtml());
      return;
    }
    if (pathname === '/auth' && request.method === 'POST') {
      const ip = request.socket.remoteAddress || 'unknown';
      const rl = checkRateLimit(ip);
      const body = await readBody(request);
      let pw = '';
      if (typeof body === 'string') {
        const match = body.match(/password=([^&]*)/);
        if (match) pw = decodeURIComponent(match[1]);
      } else if (body && body.password) {
        pw = body.password;
      }
      if (!rl.allowed) {
        sendHtml(response, authPageHtml('Too many attempts. Try again in 15 minutes.', 0));
        return;
      }
      if (verifyPassword(pw, passwordHash)) {
        const sessionId = createSession();
        response.writeHead(302, {
          'Set-Cookie': `${AUTH_COOKIE}=${sessionId}; Path=/; Max-Age=${SESSION_MAX_AGE}; HttpOnly; SameSite=Strict`,
          'Location': '/',
        });
        response.end();
      } else {
        const remaining = 5 - rl.attempts;
        sendHtml(response, authPageHtml('Wrong password. Please try again.', remaining));
      }
      return;
    }

    // Password gate: all other routes require auth
    if (!isAuthed(request)) {
      response.writeHead(302, { 'Location': '/auth' });
      response.end();
      return;
    }

    if (pathname === '/admin' || pathname === '/admin/') {
      sendHtml(response, adminPage());
      return;
    }

    if (pathname.startsWith('/admin/api/')) {
      await handleAdminAPI(request, response);
      return;
    }

    // Users API routes
    if (pathname === '/api/users' && request.method === 'GET') {
      const data = await loadUsers();
      sendJson(response, data.users || []);
      return;
    }
    if (pathname === '/api/users' && request.method === 'POST') {
      const body = await readBody(request);
      if (!body || !body.username) { sendJson(response, { error: 'Missing username' }, 400); return; }
      const data = await loadUsers();
      const user = {
        id: body.profileId || Date.now().toString(36),
        username: body.username,
        avatarImage: body.avatarImage || '',
        createdAt: body.createdAt || new Date().toISOString(),
      };
      data.users.push(user);
      await saveUsers(data);
      sendJson(response, user, 201);
      return;
    }

    // News API routes
    if (pathname === '/api/news' && request.method === 'GET') {
      const news = await loadNews();
      sendJson(response, news);
      return;
    }
    if (pathname.startsWith('/api/news/') && request.method === 'GET') {
      const id = pathname.split('/api/news/')[1];
      const news = await loadNews();
      const article = news.find(n => n.id === id);
      if (article) { sendJson(response, article); } else { sendJson(response, { error: 'Not found' }, 404); }
      return;
    }
    if (pathname === '/api/news' && request.method === 'POST') {
      const body = await readBody(request);
      if (!body || !body.title) { sendJson(response, { error: 'Missing title' }, 400); return; }
      const news = await loadNews();
      const article = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        title: body.title,
        content: body.content || '',
        imageUrl: body.imageUrl || '',
        author: body.author || 'KaGaMa',
        createdAt: new Date().toISOString()
      };
      news.unshift(article);
      await saveNews(news);
      sendJson(response, article, 201);
      return;
    }
    if (pathname.startsWith('/api/news/') && request.method === 'DELETE') {
      const id = pathname.split('/api/news/')[1];
      const news = await loadNews();
      const idx = news.findIndex(n => n.id === id);
      if (idx === -1) { sendJson(response, { error: 'Not found' }, 404); return; }
      news.splice(idx, 1);
      await saveNews(news);
      sendJson(response, { ok: true });
      return;
    }

    // Comments API: /api/comments/{profileId}
    const commentsMatch = pathname.match(/^\/api\/comments\/(\d+)$/);
    if (commentsMatch && request.method === 'GET') {
      const pid = commentsMatch[1];
      const all = await loadComments();
      sendJson(response, all[pid] || []);
      return;
    }
    if (commentsMatch && request.method === 'POST') {
      const pid = commentsMatch[1];
      const body = await readBody(request);
      if (!body || !body.text) { sendJson(response, { error: 'Missing text' }, 400); return; }
      const all = await loadComments();
      if (!all[pid]) all[pid] = [];
      const comment = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        profileId: pid,
        authorId: body.authorId || '',
        username: body.username || 'Anonymous',
        avatarImage: body.avatarImage || '',
        text: body.text,
        createdAt: new Date().toISOString()
      };
      all[pid].unshift(comment);
      await saveComments(all);
      sendJson(response, comment, 201);
      return;
    }
    if (commentsMatch && request.method === 'DELETE') {
      const pid = commentsMatch[1];
      const body = await readBody(request);
      const commentId = body && body.commentId;
      if (!commentId) { sendJson(response, { error: 'Missing commentId' }, 400); return; }
      const all = await loadComments();
      if (all[pid]) {
        all[pid] = all[pid].filter(c => c.id !== commentId);
        await saveComments(all);
      }
      sendJson(response, { ok: true });
      return;
    }

    // Wall posts API: /api/wallposts/{profileId}
    const wallpostsMatch = pathname.match(/^\/api\/wallposts\/(\d+)$/);
    if (wallpostsMatch && request.method === 'GET') {
      const pid = wallpostsMatch[1];
      const all = await loadWallposts();
      sendJson(response, all[pid] || []);
      return;
    }
    if (wallpostsMatch && request.method === 'POST') {
      const pid = wallpostsMatch[1];
      const body = await readBody(request);
      if (!body || !body.text) { sendJson(response, { error: 'Missing text' }, 400); return; }
      const all = await loadWallposts();
      if (!all[pid]) all[pid] = [];
      const post = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        profileId: pid,
        authorId: body.authorId || '',
        username: body.username || 'Anonymous',
        avatarImage: body.avatarImage || '',
        text: body.text,
        createdAt: new Date().toISOString()
      };
      all[pid].unshift(post);
      await saveWallposts(all);
      sendJson(response, post, 201);
      return;
    }
    if (wallpostsMatch && request.method === 'DELETE') {
      const pid = wallpostsMatch[1];
      const body = await readBody(request);
      const postId = body && body.postId;
      if (!postId) { sendJson(response, { error: 'Missing postId' }, 400); return; }
      const all = await loadWallposts();
      if (all[pid]) {
        all[pid] = all[pid].filter(p => p.id !== postId);
        await saveWallposts(all);
      }
      sendJson(response, { ok: true });
      return;
    }
    if (wallpostsMatch && request.method === 'PUT') {
      const pid = wallpostsMatch[1];
      const body = await readBody(request);
      const postId = body && body.postId;
      const newText = body && body.text;
      if (!postId || !newText) { sendJson(response, { error: 'Missing postId or text' }, 400); return; }
      const all = await loadWallposts();
      if (all[pid]) {
        const post = all[pid].find(p => p.id === postId);
        if (post) { post.text = newText; await saveWallposts(all); }
      }
      sendJson(response, { ok: true });
      return;
    }

    // Game comments API: /api/gamecomments/{gameId}
    const gameCommentsMatch = pathname.match(/^\/api\/gamecomments\/(\d+)$/);
    if (gameCommentsMatch && request.method === 'GET') {
      const gid = gameCommentsMatch[1];
      const all = await loadGameComments();
      sendJson(response, all[gid] || []);
      return;
    }
    if (gameCommentsMatch && request.method === 'POST') {
      const gid = gameCommentsMatch[1];
      const body = await readBody(request);
      if (!body || !body.text) { sendJson(response, { error: 'Missing text' }, 400); return; }
      const all = await loadGameComments();
      if (!all[gid]) all[gid] = [];
      const comment = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        gameId: gid,
        authorId: body.authorId || '',
        username: body.username || 'Anonymous',
        avatarImage: body.avatarImage || '',
        text: body.text,
        createdAt: new Date().toISOString()
      };
      all[gid].unshift(comment);
      await saveGameComments(all);
      sendJson(response, comment, 201);
      return;
    }
    if (gameCommentsMatch && request.method === 'DELETE') {
      const gid = gameCommentsMatch[1];
      const body = await readBody(request);
      const commentId = body && body.commentId;
      if (!commentId) { sendJson(response, { error: 'Missing commentId' }, 400); return; }
      const all = await loadGameComments();
      if (all[gid]) {
        all[gid] = all[gid].filter(c => c.id !== commentId);
        await saveGameComments(all);
      }
      sendJson(response, { ok: true });
      return;
    }
    if (gameCommentsMatch && request.method === 'PUT') {
      const gid = gameCommentsMatch[1];
      const body = await readBody(request);
      const commentId = body && body.commentId;
      const newText = body && body.text;
      if (!commentId || !newText) { sendJson(response, { error: 'Missing commentId or text' }, 400); return; }
      const all = await loadGameComments();
      if (all[gid]) {
        const cmt = all[gid].find(c => c.id === commentId);
        if (cmt) { cmt.text = newText; await saveGameComments(all); }
      }
      sendJson(response, { ok: true });
      return;
    }

    // Product comments API: /api/productcomments/{productId}
    const productCommentsMatch = pathname.match(/^\/api\/productcomments\/([a-z0-9_-]+)$/);
    if (productCommentsMatch && request.method === 'GET') {
      const pid = productCommentsMatch[1];
      const all = await loadProductComments();
      sendJson(response, all[pid] || []);
      return;
    }
    if (productCommentsMatch && request.method === 'POST') {
      const pid = productCommentsMatch[1];
      const body = await readBody(request);
      if (!body || !body.text) { sendJson(response, { error: 'Missing text' }, 400); return; }
      const all = await loadProductComments();
      if (!all[pid]) all[pid] = [];
      const comment = {
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 8),
        productId: pid,
        authorId: body.authorId || '',
        username: body.username || 'Anonymous',
        avatarImage: body.avatarImage || '',
        text: body.text,
        createdAt: new Date().toISOString()
      };
      all[pid].unshift(comment);
      await saveProductComments(all);
      sendJson(response, comment, 201);
      return;
    }
    if (productCommentsMatch && request.method === 'DELETE') {
      const pid = productCommentsMatch[1];
      const body = await readBody(request);
      const commentId = body && body.commentId;
      if (!commentId) { sendJson(response, { error: 'Missing commentId' }, 400); return; }
      const all = await loadProductComments();
      if (all[pid]) {
        all[pid] = all[pid].filter(c => c.id !== commentId);
        await saveProductComments(all);
      }
      sendJson(response, { ok: true });
      return;
    }

    const marketplaceAvatarMatch = pathname.match(/^\/marketplace\/avatar\/([a-z0-9_-]+)(?:\/(?:index\.html)?)?(?:\/)?$/);
    const marketplaceModelMatch = pathname.match(/^\/marketplace\/model\/([a-z0-9_-]+)(?:\/(?:index\.html)?)?(?:\/)?$/);
    if (marketplaceAvatarMatch || marketplaceModelMatch) {
      const isAvatar = !!marketplaceAvatarMatch;
      const itemId = isAvatar ? marketplaceAvatarMatch[1] : marketplaceModelMatch[1];
      const dataFile = isAvatar ? avatarsFile : modelsFile;
      try {
        const raw = await fs.readFile(dataFile, 'utf8');
        const data = JSON.parse(raw);
        const items = isAvatar ? (data.avatars || []) : (data.models || []);
        const item = items.find(i => i.id === itemId);
        if (item) {
          const html = marketplaceDetailPage(item, isAvatar ? 'avatar' : 'model');
          sendHtml(response, html);
          return;
        }
      } catch {}
    }

    const profileSubpage = pathname.match(/\/profile\/(\d+)\/(friends|settings|about)(?:\/(?:index\.html)?)?(?:\/)?$/);
    if (profileSubpage && profileSubpage[1] !== '1') {
      const subpage = profileSubpage[2];
      const targetFile = path.join(siteRoot, 'profile', '1', subpage, 'index.html');
      if (await exists(targetFile)) {
        let content = await fs.readFile(targetFile);
        content = Buffer.from(injectOverrides(content.toString('utf8')), 'utf8');
        send(response, content, '.html');
        return;
      }
    }

    const profileRedirect = pathname.match(/\/profile\/(\d+)(?:\/index\.html)?(?:\/)?$/);
    if (profileRedirect && profileRedirect[1] !== '1') {
      const profileFile = path.join(siteRoot, 'profile', '1', 'index.html');
      if (await exists(profileFile)) {
        let content = await fs.readFile(profileFile);
        content = Buffer.from(injectOverrides(content.toString('utf8')), 'utf8');
        send(response, content, '.html');
        return;
      }
    }

    const filePath = await resolveFilePath(pathname);

    if (!filePath) {
      const fallback = fallbackPage(pathname);
      if (fallback) {
        send(response, injectOverrides(fallback), '.html');
        return;
      }
      response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
      response.end('Not found');
      return;
    }

    let content = await fs.readFile(filePath);
    const ext = path.extname(filePath).toLowerCase();
    if (ext === '.html') {
      content = Buffer.from(injectOverrides(content.toString('utf8')), 'utf8');
    }

    send(response, content, ext);
  } catch {
    response.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Not found');
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`KaGaMa Archive + Admin Panel`);
  console.log(`Site:  http://127.0.0.1:${port}/`);
  console.log(`Admin: http://127.0.0.1:${port}/admin`);
});

async function resolveFilePath(rawPathname) {
  const decodedPath = decodeURIComponent(rawPathname);
  const relativePath = decodedPath === '/' ? 'index.html' : decodedPath.slice(1);
  const candidates = [relativePath];

  if (relativePath && !relativePath.startsWith('games/') && !relativePath.startsWith('profile/') && !relativePath.startsWith('static/') && !relativePath.startsWith('news/') && !relativePath.startsWith('marketplace/') && !relativePath.startsWith('videos/') && !relativePath.startsWith('help/') && !relativePath.startsWith('purchase/') && !relativePath.startsWith('m/')) {
    candidates.push(path.join(relativePath, 'index.html'));
  }

  for (const candidate of candidates) {
    const resolved = path.resolve(root, candidate);
    if (!resolved.startsWith(root)) return null;

    const stat = await fs.stat(resolved).catch(() => null);
    if (stat?.isDirectory()) {
      const indexPath = path.join(resolved, 'index.html');
      if (await exists(indexPath)) return indexPath;
    } else if (stat?.isFile()) {
      return resolved;
    }
  }

  return null;
}

async function exists(filePath) {
  return Boolean(await fs.stat(filePath).catch(() => null));
}

function send(response, content, ext) {
  response.writeHead(200, {
    'content-type': mimeTypes.get(ext) ?? 'application/octet-stream',
    'cache-control': 'no-cache',
  });
  response.end(content);
}

function injectOverrides(html) {
  if (html.includes('/static/local/offline-overrides.js') || html.includes('/static/local/offline-overrides.js')) {
    return html;
  }

  html = html.replace(/"\.\.\/\.\.\/www\.KaGaMa\.com\//g, '"/');
  html = html.replace(/'\.\.\/\.\.\/www\.KaGaMa\.com\//g, "'/");
  html = html.replace(/"\.\.\/\.\.\/\.\.\/static\.kogstatic\.com\//g, '"/static.kogstatic.com/');
  html = html.replace(/'\.\.\/\.\.\/\.\.\/static\.kogstatic\.com\//g, "'/static.kogstatic.com/");
  html = html.replace(/"\.\.\/\.\.\/static\.kogstatic\.com\//g, '"/static.kogstatic.com/');
  html = html.replace(/'\.\.\/\.\.\/static\.kogstatic\.com\//g, "'/static.kogstatic.com/");

  html = html.replace(/href="\/profile\/669419960\/"/g, 'href="#"');
  html = html.replace(/href='\/profile\/669419960\/'/g, "href='#'");

  // Replace dead CDN CSS with local copies
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/fd54ee6e0520161805f6f6465d3969a261607f3d\/app-less\.css/g, '/static/local/css/app-less.css');
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/fd54ee6e0520161805f6f6465d3969a261607f3d\/app-sass\.css/g, '/static/local/css/app-sass.css');
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/d9c1e5da76aa8de67a3be7e8541d0ba21f064294\/app-less\.css/g, '/static/local/css/app-less-v2.css');
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/d9c1e5da76aa8de67a3be7e8541d0ba21f064294\/app-sass\.css/g, '/static/local/css/app-sass-v2.css');
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/6cb435bca8640999117210cf1a71d93eb793b351\/app-less\.css/g, '/static/local/css/app-less-v3.css');
  html = html.replace(/https?:\/\/static\.kogstatic\.com\/0000\/6cb435bca8640999117210cf1a71d93eb793b351\/app-sass\.css/g, '/static/local/css/app-sass-v3.css');
  // Also fix protocol-relative URLs
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/fd54ee6e0520161805f6f6465d3969a261607f3d\/app-less\.css/g, '/static/local/css/app-less.css');
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/fd54ee6e0520161805f6f6465d3969a261607f3d\/app-sass\.css/g, '/static/local/css/app-sass.css');
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/d9c1e5da76aa8de67a3be7e8541d0ba21f064294\/app-less\.css/g, '/static/local/css/app-less-v2.css');
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/d9c1e5da76aa8de67a3be7e8541d0ba21f064294\/app-sass\.css/g, '/static/local/css/app-sass-v2.css');
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/6cb435bca8640999117210cf1a71d93eb793b351\/app-less\.css/g, '/static/local/css/app-less-v3.css');
  html = html.replace(/\/\/static\.kogstatic\.com\/0000\/6cb435bca8640999117210cf1a71d93eb793b351\/app-sass\.css/g, '/static/local/css/app-sass-v3.css');

  const injection = [
    '<link rel="stylesheet" href="/static/local/offline-overrides.css">',
    '<script defer src="/static/local/offline-overrides.js"></script>',
  ].join('');

  if (html.includes('</head>')) {
    return html.replace('</head>', `${injection}</head>`);
  }

  return `${injection}${html}`;
}

function fallbackPage(rawPathname) {
  const pathname = normalizeArchivePath(rawPathname);
  const gameMatch = pathname.match(/^\/games\/play\/(\d+)/);
  const modelMatch = pathname.match(/^\/marketplace\/(?:model|avatar)\/([^/]+)/);
  const profileMatch = pathname.match(/^\/profile\/(\d+)/);

  if (profileMatch) {
    const profileId = profileMatch[1];
    if (profileId === '1') return null;
    if (profileId === '668677211') {
      return shellPage({
        title: 'Profile moved',
        heading: 'Profile relocated',
        body: 'This profile has been moved to a new location.',
        primaryHref: '/profile/1/',
        primaryText: 'Go to Profile',
      });
    }
    return null;
  }

  if (gameMatch) {
    return shellPage({
      title: `Archived Game ${gameMatch[1]}`,
      heading: 'Archived game page',
      body: 'Wayback did not have this specific game detail page in the copied snapshot.',
      primaryHref: '/games/',
      primaryText: 'Back to Games',
    });
  }

  if (modelMatch) {
    return shellPage({
      title: `Shop Item ${modelMatch[1]}`,
      heading: 'Archived shop item',
      body: 'This marketplace item was listed in the archive, but its detail page was not captured.',
      primaryHref: '/marketplace/',
      primaryText: 'Back to Shop',
    });
  }

  if (pathname.startsWith('/build/')) {
    return shellPage({
        title: 'Build - KaGaMa Archive',
      heading: 'Build mode',
      body: 'The original archived Build page returned a server error in Wayback.',
      primaryHref: '/games/',
      primaryText: 'Browse Games',
    });
  }

  if (pathname.startsWith('/news/')) {
    return shellPage({
        title: 'News - KaGaMa Archive',
      heading: 'News article unavailable',
      body: 'This news link was not captured as a separate page.',
      primaryHref: '/news/',
      primaryText: 'Back to News',
    });
  }

  if (pathname.startsWith('/videos/')) {
    return shellPage({
        title: 'Videos - KaGaMa Archive',
      heading: 'Videos',
      body: 'The local videos page has been patched with playable YouTube links.',
      primaryHref: '/videos/',
      primaryText: 'Open Videos',
    });
  }

  return null;
}

function normalizeArchivePath(rawPathname) {
  let pathname = decodeURIComponent(rawPathname);
  if (pathname.startsWith('/www.KaGaMa.com')) {
    pathname = pathname.slice('/www.KaGaMa.com'.length) || '/';
  }
  return pathname;
}

function shellPage({ title, heading, body, primaryHref, primaryText }) {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>
    body { background: #16222d; color: #fff; font-family: "Open Sans", Arial, sans-serif; margin: 0; }
    #pageheader { background: #05070a; height: 48px; left: 0; position: fixed; right: 0; top: 0; z-index: 20; }
    #pageheader .pageheader-inner { display: flex; height: 48px; }
    #pageheader .logo { display: block; height: 48px; width: 48px; }
    #pageheader .logo-image { height: 48px; width: 48px; }
    #pageheader nav a { color: #fff; display: inline-block; font-weight: 800; line-height: 48px; padding: 0 16px; text-decoration: none; text-transform: uppercase; }
    #meta-nav { list-style: none; margin: 0 0 0 auto; padding: 0; }
    #meta-nav li { display: inline-block; }
    #login-button, #signup-button { color: #fff; cursor: pointer; display: inline-block; font-weight: 800; line-height: 48px; padding: 0 14px; text-decoration: none; text-transform: uppercase; }
    #signup-button { background: #ff370f; line-height: 40px; margin: 4px 10px 0 0; }
  </style>
</head>
<body>
  <header id="pageheader">
    <div class="pageheader-inner">
      <a href="/games/" title="PlayKaGaMa" class="logo"><div class="logo-image"></div></a>
      <nav><a href="/games/">Play</a><a href="/marketplace/">Shop</a><a href="/news/">News</a><a href="/videos/">Videos</a></nav>
      <ol id="meta-nav"><li><a id="login-button" href="#">Login</a></li><li><a id="signup-button" href="#">Signup</a></li></ol>
    </div>
  </header>
  <main class="kg-generated-page">
    <h1>${escapeHtml(heading)}</h1>
    <p>${escapeHtml(body)}</p>
    <div class="kg-actions"><a href="${primaryHref}">${escapeHtml(primaryText)}</a></div>
  </main>
</body>
</html>`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}
