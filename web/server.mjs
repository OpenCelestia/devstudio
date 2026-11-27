#!/usr/bin/env node
/*
 * server.mjs - PC sync tool, zero-dependency Node server.
 *
 *   devstudio web [--port 8081] [--product anypi_nova]
 *   node server.mjs --port 8081 --repo <仓库根> --tree <WSL 树> --product anypi_nova
 *
 * Bridges the browser UI (public/index.html) and the device agent:
 *   /video      multipart/x-mixed-replace MJPEG relay of device FRAME frames
 *   /events     SSE stream: hello/status/metrics/log/build events
 *   /api/exec   POST {cmd} -> EXEC frame, waits EXEC_RESULT
 *   /api/reboot POST {}    -> REBOOT frame
 *   /api/snap   POST {}    -> SNAPSHOT frame, replies image/jpeg
 *   /api/build  POST {module} -> WSL build (streams log via SSE)
 *   /api/push   POST {module} -> compile, then push. USB apps replace
 *                 /mnt/install/apps/<id>. User-area content is repacked as
 *                 usr.sqsh4 and written to the APP mtd with mtd_debug.
 *
 * Build steps pipe wsl_build.sh to bash on stdin. Windows uses wsl.exe;
 * Linux and macOS run bash directly. Do not pass that script as an argument:
 * wsl.exe mangles quotes.
 */
import net from 'node:net';
import http from 'node:http';
import https from 'node:https';
import os from 'node:os';
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, sep } from 'node:path';
import { llmProvider, publicProviders } from './llm-providers.mjs';

const HOST_FS_TRANSIENT = new Set(['EINVAL', 'EBUSY', 'EPERM', 'EACCES', 'EAGAIN', 'EIO', 'UNKNOWN', 'ETIMEDOUT', 'ECONNRESET']);

function isHostFsTransient(e) {
    return !!(e && HOST_FS_TRANSIENT.has(e.code));
}

/* The repo may sit on an SMB share that Linux edits at the same time; opens then fail briefly. */
function hostFsRetry(fn) {
    let last;
    for (let i = 0; i < 6; i++) {
        try { return fn(); }
        catch (e) {
            if (!isHostFsTransient(e)) throw e;
            last = e;
            Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 300 * (i + 1));
        }
    }
    throw last;
}

const { existsSync } = fs;
const STUDIO_STARTED = Date.now();

/* Files under devstudio/web changed after this process started, so it is running old code. */
function studioStaleFiles() {
    const dir = dirname(fileURLToPath(import.meta.url));
    const names = ['server.mjs', 'llm-providers.mjs', 'wsl_build.sh', join('public', 'index.html')];
    return names.filter(n => {
        try { return fs.statSync(join(dir, n)).mtimeMs > STUDIO_STARTED + 1000; } catch { return false; }
    });
}
const readFileSync = (...a) => hostFsRetry(() => fs.readFileSync(...a));
const writeFileSync = (...a) => hostFsRetry(() => fs.writeFileSync(...a));
const mkdirSync = (...a) => hostFsRetry(() => fs.mkdirSync(...a));
const rmSync = (...a) => hostFsRetry(() => fs.rmSync(...a));
const readdirSync = (...a) => hostFsRetry(() => fs.readdirSync(...a));

/* ---------------- args ---------------- */
const argv = process.argv.slice(2);
function argOf(name, def) {
    const i = argv.indexOf(name);
    if (i >= 0 && argv[i + 1]) return argv[i + 1];
    const eq = argv.find(a => a.startsWith(name + '='));
    return eq ? eq.split('=').slice(1).join('=') : def;
}

const HTTP_PORT = parseInt(argOf('--port', '8081'), 10);
const WSL_TREE = argOf('--tree', '~/celestia/develop');
const WSL_PRODUCT = argOf('--product', 'anypi_nova');
const ROOT = join(dirname(fileURLToPath(import.meta.url)));
const DEVICE_FILE = join(ROOT, 'device.json');

function loadProduct() {
    const fallback = {
        id: WSL_PRODUCT,
        agentModule: 'APP_STUDIO',
        agentId: 'studio',
        agentName: '同步代理(Sync Agent)',
        agentSrc: 'application/services/studio',
        relaunch: '',
        procAlias: {},
        builtins: ['studio'],
        usbRoot: '/mnt/install/apps',
        usrApps: [],
    };
    try {
        const j = JSON.parse(readFileSync(join(ROOT, 'products', `${WSL_PRODUCT}.json`), 'utf8'));
        return {
            ...fallback,
            ...j,
            procAlias: j.procAlias && typeof j.procAlias === 'object' ? j.procAlias : {},
            builtins: Array.isArray(j.builtins) ? j.builtins : fallback.builtins,
            usrApps: Array.isArray(j.usrApps) ? j.usrApps : [],
        };
    } catch {
        return fallback;
    }
}
const product = loadProduct();

let uiLang = 'zh';
function tr(zh, en) { return uiLang === 'en' ? en : zh; }
function providerName(spec) {
    if (!spec) return '';
    return uiLang === 'en' && spec.nameEn ? spec.nameEn : (spec.name || '');
}
function normalizeDevice(raw) {
    let s = String(raw || '').trim();
    if (!s) return '';
    if (!s.includes(':')) s += ':7777';
    const m = s.match(/^([A-Za-z0-9.-]+):(\d+)$/);
    if (!m) throw new Error(tr('IP 格式不正确', 'Invalid IP'));
    const port = parseInt(m[2], 10);
    if (!(port >= 1 && port <= 65535)) throw new Error(tr('端口不正确', 'Invalid port'));
    const host = m[1];
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
        const ok = host.split('.').every(n => /^\d{1,3}$/.test(n) && Number(n) <= 255);
        if (!ok) throw new Error(tr('IP 格式不正确', 'Invalid IP'));
    }
    return `${host}:${port}`;
}

function loadSavedDevice() {
    try { return normalizeDevice(JSON.parse(readFileSync(DEVICE_FILE, 'utf8')).device || ''); }
    catch { return ''; }
}

let devAddr = '';
try { devAddr = normalizeDevice(argOf('--device', '')) || loadSavedDevice(); }
catch { devAddr = loadSavedDevice(); }
let connectGen = 0;

/* ---------------- frame types (mirror studio_internal.h) ---------------- */
const PT = {
    HELLO: 0x01, METRICS: 0x02, LOG: 0x03, FRAME: 0x04, SNAPSHOT: 0x05,
    EXEC: 0x07, EXEC_RESULT: 0x08, REBOOT: 0x09, FILE_GET: 0x0A, FILE_PUT: 0x0B,
    APP_PUSH: 0x0C, APP_RESULT: 0x0D, ERR: 0x0E,     PING: 0x0F, PONG: 0x10, SUB: 0x11, INPUT: 0x12,
};

/* ---------------- device connection ---------------- */
const dev = {
    socket: null,
    connected: false,
    hello: null,
    buf: Buffer.alloc(0),
    want: 8,
    phase: 'hdr',
    curType: 0,
    waiters: new Map(),   // type -> [{resolve, timer}]
    lastFrame: null,      // Buffer of latest JPEG
    frameSeq: 0,
    frameOn: null,        // last SUB frame flag sent to the device
};

const sseClients = new Set();
const mjpegClients = new Set();

function sseSend(event, data) {
    const line = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
    for (const res of sseClients) {
        try { res.write(line); } catch { sseClients.delete(res); }
    }
}

function deviceStatus() {
    return { connected: dev.connected, hello: dev.hello, device: devAddr };
}

function setConnected(ok) {
    if (dev.connected !== ok) {
        dev.connected = ok;
        sseSend('status', deviceStatus());
        if (!ok) dev.hello = null;
    }
}

/* The agent lives in the main process. Relaunch kills that process, so a
 * TCP connect is not proof the agent is back. Ready means HELLO arrived. */
let helloTimer = null;
let linkLossLogged = false;
function clearHelloTimer() {
    if (helloTimer) { clearTimeout(helloTimer); helloTimer = null; }
}
function failLink(reason) {
    const sock = dev.socket;
    if (!sock) return;
    if (!linkLossLogged) {
        linkLossLogged = true;
        logLocal(tr(`连接中断（${reason}），正在重连`, `Link lost (${reason}), reconnecting`));
    }
    clearHelloTimer();
    sock.destroy();
}

function resetDevBuf() {
    dev.buf = Buffer.alloc(0);
    dev.phase = 'hdr';
    dev.want = 8;
    for (const w of dev.waiters.values())
        for (const it of w) { clearTimeout(it.timer); it.resolve(null); }
    dev.waiters.clear();
}

function dropSocket() {
    const sock = dev.socket;
    dev.socket = null;
    if (!sock) return;
    sock.removeAllListeners();
    sock.destroy();
}

function connectDevice() {
    const addr = devAddr;
    if (!addr) return;
    const gen = connectGen;
    const [host, port] = addr.split(':');
    const sock = net.connect({ host, port: parseInt(port || '7777', 10) });
    sock.setNoDelay(true);
    dev.socket = sock;
    dev.frameOn = null;

    sock.on('connect', () => {
        if (gen !== connectGen || dev.socket !== sock) { sock.destroy(); return; }
        clearHelloTimer();
        helloTimer = setTimeout(() => {
            if (gen !== connectGen || dev.socket !== sock || dev.hello) return;
            failLink(tr('没有收到代理握手', 'no agent hello'));
        }, 4000);
    });
    sock.on('data', chunk => {
        try { onDeviceData(chunk); }
        catch (e) { failLink(e.message || 'bad frame'); }
    });
    sock.on('error', () => {});
    sock.on('close', () => {
        clearHelloTimer();
        if (dev.socket === sock) dev.socket = null;
        if (gen !== connectGen) return;
        const wasReady = dev.connected;
        setConnected(false);
        resetDevBuf();
        if (wasReady && !linkLossLogged)
            logLocal(tr('代理已断开，正在重连', 'Agent disconnected, reconnecting'));
        linkLossLogged = false;
        setTimeout(() => { if (gen === connectGen) connectDevice(); }, 2000);
    });
}

function useDevice(raw) {
    const addr = normalizeDevice(raw);
    if (!addr) throw new Error(tr('请填写设备 IP', 'Enter the device IP'));
    connectGen++;
    dropSocket();
    setConnected(false);
    resetDevBuf();
    devAddr = addr;
    writeFileSync(DEVICE_FILE, JSON.stringify({ device: addr }) + '\n');
    connectDevice();
    sseSend('status', deviceStatus());
    return addr;
}

function sendFrame(type, payload) {
    if (!dev.socket || !dev.connected) return false;
    const hdr = Buffer.alloc(8);
    hdr.writeUInt32LE(type, 0);
    hdr.writeUInt32LE(payload ? payload.length : 0, 4);
    try {
        dev.socket.write(Buffer.concat([hdr, payload ? Buffer.from(payload) : Buffer.alloc(0)]));
        return true;
    } catch { return false; }
}

function sendSub(sub) { sendFrame(PT.SUB, Buffer.from(JSON.stringify(sub))); }

/* The page asks for the view. A lingering MJPEG <img> must not keep encoding. */
let viewOn = true;

/* Push frames only while the sync view is open and a browser is receiving it. */
function syncDeviceFrames() {
    const frame = viewOn && mjpegClients.size > 0;
    if (!dev.connected || dev.frameOn === frame) return;
    if (!sendSub({ metrics: true, log: true, frame, scale: 1 })) return;
    dev.frameOn = frame;
    logLocal(frame ? tr('同步画面开始推送', 'Sync view started') : tr('同步画面已停止推送', 'Sync view stopped'));
}

function stopViewers() {
    const all = [...mjpegClients];
    mjpegClients.clear();
    for (const res of all) {
        try { res.end(); } catch { /* already closed */ }
    }
}

function removeWaiter(entry) {
    const list = dev.waiters.get(entry.type) || [];
    const i = list.indexOf(entry);
    if (i >= 0) list.splice(i, 1);
    clearTimeout(entry.timer);
}
function waitFor(type, ms) {
    let entry;
    const p = new Promise(resolve => {
        entry = {
            type,
            resolve,
            timer: setTimeout(() => {
                removeWaiter(entry);
                resolve(null);
            }, ms),
        };
        if (!dev.waiters.has(type)) dev.waiters.set(type, []);
        dev.waiters.get(type).push(entry);
    });
    p.cancel = () => {
        removeWaiter(entry);
        entry.resolve = () => {};
    };
    return p;
}

function onDeviceData(chunk) {
    dev.buf = dev.buf.length ? Buffer.concat([dev.buf, chunk]) : chunk;
    for (;;) {
        if (dev.phase === 'hdr') {
            if (dev.buf.length < 8) return;
            dev.curType = dev.buf.readUInt32LE(0);
            const len = dev.buf.readUInt32LE(4);
            if (len > 64 * 1024 * 1024) { dev.socket.destroy(); return; }
            dev.buf = dev.buf.subarray(8);
            dev.want = len;
            dev.phase = 'payload';
        }
        if (dev.phase === 'payload') {
            if (dev.buf.length < dev.want) return;
            const payload = dev.buf.subarray(0, dev.want);
            dev.buf = dev.buf.subarray(dev.want);
            dev.phase = 'hdr';
            dispatch(dev.curType, payload);
        }
    }
}

function dispatch(type, payload) {
    // wake waiters of this type (exec/file/push replies)
    const list = dev.waiters.get(type);
    if (list && list.length) {
        const it = list.shift();
        clearTimeout(it.timer);
        it.resolve(payload.length ? Buffer.from(payload) : Buffer.alloc(0));
    }
    switch (type) {
        case PT.HELLO:
            clearHelloTimer();
            dev.hello = JSON.parse(payload.toString('utf8'));
            setConnected(true);
            sseSend('hello', dev.hello);
            syncDeviceFrames();
            break;
        case PT.METRICS:
            sseSend('metrics', JSON.parse(payload.toString('utf8')));
            break;
        case PT.LOG:
            sseSend('log', JSON.parse(payload.toString('utf8')));
            break;
        case PT.FRAME:
            dev.lastFrame = Buffer.from(payload);
            dev.frameSeq++;
            for (const res of mjpegClients) {
                res.write('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' +
                    payload.length + '\r\n\r\n');
                res.write(payload);
                res.write('\r\n');
            }
            break;
        case PT.ERR:
            sseSend('device_err', { msg: payload.toString('utf8') });
            break;
        default: break;
    }
}

function logLocal(msg) { sseSend('log', { ts: Math.floor(Date.now() / 1000), level: 6, tag: 'pc', text: msg }); }

/* ---------------- WSL build / package ---------------- */
function tailText(s, n = 40) {
    return String(s || '').trim().split(/\r?\n/).slice(-n).join('\n');
}

function uniqueCaptures(text, re, limit) {
    const out = [];
    const flags = re.flags.includes('g') ? re.flags : re.flags + 'g';
    const rx = new RegExp(re.source, flags);
    let m;
    while ((m = rx.exec(text)) && out.length < limit) {
        if (m[1] && !out.includes(m[1])) out.push(m[1]);
    }
    return out;
}

/* A compiler path counts when it is inside this tablet app. A bare name counts
 * when that file is sitting in the app directory. */
function hitInApp(file, target) {
    const f = String(file || '').replace(/\\/g, '/');
    if (f.includes('application/packages/') || f.includes('/tablet/')) return f;
    const id = appIdOf(target || '');
    const base = f.split('/').pop();
    if (!id || !/^[\w.+-]+\.(?:c|h|cpp)$/i.test(base || '')) return '';
    const rel = `application/packages/tablet/${id}/${base}`;
    if (existsSync(join(repoDir(), rel))) return rel;
    return '';
}

function collectBuildHits(text, target) {
    const hits = [];
    let from = '';
    let fromLine = '';
    for (const raw of String(text || '').split(/\r?\n/)) {
        const line = raw.replace(/\\/g, '/').trim();
        const inc = line.match(/In file included from (.+?\.(?:c|h|cpp)):(\d+)/i);
        if (inc) {
            const where = hitInApp(inc[1], target);
            if (where) {
                from = where;
                fromLine = inc[2];
            }
            continue;
        }
        const err = line.match(/^(.+?\.(?:c|h|cpp)):(\d+):(?:(\d+):)?\s*((?:fatal )?error):\s*(.*)$/i);
        if (!err) continue;
        const direct = hitInApp(err[1], target);
        const msg = `${err[4]}: ${String(err[5] || '').trim()}`;
        if (direct) hits.push({ file: direct, line: err[2], col: err[3] || '', msg });
        else if (from) hits.push({ file: from, line: fromLine, col: '', msg: `${err[1]}:${err[2]} ${msg}` });
        if (hits.length >= 8) break;
    }
    return hits;
}

/* source: the model can edit the app. makefile: the tool repairs the rule.
 * infra: kernel/U-Boot/OpenSBI races, retry unchanged. artifact: build
 * returned success but this app was not installed. */
function classifyBuildLog(text, target) {
    const s = String(text || '');
    const hits = collectBuildHits(s, target);
    const undef = uniqueCaptures(s, /undefined reference to [`']([^'`]+)[`']/gi, 6);
    const multi = uniqueCaptures(s, /multiple definition of [`']?([^'`\s]+)[`']?/gi, 6);
    const infra = /fixdep:|opensbi build failed|kernel build failed|u-boot\.cfg|os\/kernel\/|os\/uboot\/|os\/opensbi\//.test(s);
    const pack = kind => ({ kind, hits, undef, multi });
    if (/FATAL: artifact not found/.test(s)) return pack('artifact');
    if (isMakefileDefect(s) && !hits.length) return pack('makefile');
    if (infra && !hits.length) return pack('infra');
    if (hits.length || undef.length || multi.length) return pack('source');
    return pack('unknown');
}

function isAppSourceError(text, target) {
    return classifyBuildLog(text, target).kind === 'source';
}

let wslChain = Promise.resolve();
function wslRun(env, onLine) {
    const job = wslChain.then(() => wslRunNow(env, onLine), () => wslRunNow(env, onLine));
    wslChain = job.then(() => {}, () => {});
    return job;
}

function wslRunNow(env, onLine) {
    /* wsl.exe only copies Windows variables named in WSLENV into bash. */
    const passed = Object.entries(env || {}).filter(([k]) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(k));
    const names = new Set(String(process.env.WSLENV || '').split(':').filter(Boolean));
    for (const [k] of passed) names.add(k);
    const childEnv = { ...process.env, WSLENV: [...names].join(':') };
    for (const [k, v] of passed) childEnv[k] = String(v ?? '');
    return new Promise((resolve, reject) => {
        /* A UNC cwd makes wsl.exe fail to translate the working directory. */
        let child;
        try { child = spawnBash(childEnv); }
        catch (e) { reject(e); return; }
        if (llmJob) llmJob.track(child);
        child.stdin.write(readFileSync(join(ROOT, 'wsl_build.sh')));
        child.stdin.end();
        let out = '', err = '';
        child.stdout.on('data', d => {
            out += d.toString();
            if (onLine) onLine(d.toString());
        });
        child.stderr.on('data', d => {
            err += d.toString();
            if (onLine) onLine(d.toString());
        });
        child.on('close', rc => {
            if (rc === 0) { resolve({ out, err }); return; }
            const all = `${out}\n${err}`;
            const error = new Error(tailText(all) || `exit ${rc}`);
            error.missingArtifact = /FATAL: artifact not found/.test(all);
            error.makefileDefect = isMakefileDefect(all);
            error.treeRace = !error.missingArtifact && !error.makefileDefect && !isAppSourceError(all, env && env.MODULE);
            reject(error);
        });
        child.on('error', reject);
    });
}

function readHostText(path) {
    return hostFsRetry(() => readFileSync(path, 'utf8'));
}

function writeHostText(path, text) {
    hostFsRetry(() => writeFileSync(path, text));
}

let buildTreeSame = null;

/* True when the WSL build tree is this repo itself, so syncing would rewrite files in place. */
async function buildTreeIsRepo() {
    if (buildTreeSame !== null) return buildTreeSame;
    if (process.platform !== 'win32') return (buildTreeSame = false);
    const name = '.studio-tree-probe';
    const token = `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const tree = WSL_TREE.startsWith('~/') ? `$HOME/${WSL_TREE.slice(2)}` : WSL_TREE;
    try {
        writeHostText(join(repoDir(), name), token);
        const seen = (await bashStdout(`cat ${tree}/${name} 2>/dev/null; rm -f ${tree}/${name}\n`)).toString().trim();
        buildTreeSame = seen === token;
    } catch {
        buildTreeSame = false;
    }
    try { if (existsSync(join(repoDir(), name))) hostFsRetry(() => rmSync(join(repoDir(), name), { force: true })); }
    catch { /* Linux side already removed it or will on the next probe */ }
    return buildTreeSame;
}

function enableTabletConfig(id) {
    const cfgRel = join('project', product.id || WSL_PRODUCT, 'config.mk');
    const cfg = join(repoDir(), cfgRel);
    if (!existsSync(cfg)) return '';
    const macro = 'CONFIG_APP_TABLET_' + id.toUpperCase();
    const text = readHostText(cfg);
    const line = `${macro} = y`;
    const re = new RegExp(`^${macro}\\s*=.*$`, 'm');
    const next = re.test(text)
        ? text.replace(re, line)
        : `${text.replace(/\s*$/, '')}\n${line}\n`;
    if (next === text) return '';
    writeHostText(cfg, next);
    return cfgRel.split(sep).join('/');
}

/* Enable the app's config switch on every host. The tablet Makefile discovers
 * apps from their directories (app.json + optional app.mk), so no Makefile
 * wiring is needed — only the CONFIG_APP_TABLET_<ID> switch. Windows then
 * copies the result into the WSL tree. */
async function prepareTabletBuild(moduleName, onLine) {
    if (!/^APP_TABLET_/.test(String(moduleName || ''))) return;
    if (repairTabletMakefile()) {
        onLine(tr(
            '编译规则有一行连到了下一行，已拆开。\n',
            'A build rule was stuck to the next line. It has been split apart.\n',
        ));
    }
    const id = appIdOf(moduleName);
    const rel = id ? join('application', 'packages', 'tablet', id) : '';
    if (!rel || !existsSync(join(repoDir(), rel))) return;
    const cfgShown = enableTabletConfig(id);
    if (cfgShown) {
        onLine(tr(
            `已在 ${cfgShown} 打开 CONFIG_APP_TABLET_${id.toUpperCase()}。Makefile 按应用目录自动接线。\n`,
            `Turned on CONFIG_APP_TABLET_${id.toUpperCase()} in ${cfgShown}. The Makefile discovers apps from their directories.\n`,
        ));
    }
    if (process.platform !== 'win32') return;
    if (await buildTreeIsRepo()) {
        onLine(tr(
            `编译树就是这份源码（${WSL_TREE}），不用再同步。\n`,
            `The build tree is this source tree (${WSL_TREE}), so nothing needs syncing.\n`,
        ));
        return;
    }
    const cfgRel = join('project', product.id || WSL_PRODUCT, 'config.mk');
    const relPosix = rel.split(sep).join('/');
    const cfgPosix = cfgRel.split(sep).join('/');
    const tarPath = join(os.homedir(), `studio-sync-${id}.tar`);
    const tarArgs = ['-cf', tarPath, '-C', repoDir(), relPosix];
    if (existsSync(join(repoDir(), cfgRel))) tarArgs.push(cfgPosix);
    await new Promise((resolve, reject) => {
        const child = spawn('tar', tarArgs, { cwd: hostCwd() });
        let err = '';
        child.stderr.on('data', d => { err += d.toString(); });
        child.on('close', rc => (rc === 0 ? resolve() : reject(new Error(err.trim() || `tar exit ${rc}`))));
        child.on('error', reject);
    });
    const wslTar = tarPath.replace(/\\/g, '/').replace(/^([A-Za-z]):/, (_, d) => `/mnt/${d.toLowerCase()}`);
    const tree = WSL_TREE.startsWith('~/') ? `$HOME/${WSL_TREE.slice(2)}` : WSL_TREE;
    onLine(tr(
        `把 ${id} 的源码同步到编译树，编译才会用到刚改的文件。\n`,
        `Syncing ${id} into the build tree so the compile uses the files just edited.\n`,
    ));
    await bashStdout(`set -e\ntar -xf '${wslTar}' -C ${tree}\necho SYNC ${id}\n`, onLine);
}

async function wslBuild(moduleName, onLine) {
    for (let i = 1; ; i++) {
        try {
            await prepareTabletBuild(moduleName, onLine);
            break;
        } catch (e) {
            if (llmCancelled()) return false;
            if (!isHostFsTransient(e) || i >= 3) throw e;
            onLine(tr(
                `读写构建文件暂时失败（${e.code}），2 秒后重试（${i + 1}/3）。\n`,
                `Reading or writing a build file failed for a moment (${e.code}). Retrying in 2 seconds (${i + 1}/3).\n`,
            ));
            await delay(2000);
        }
    }
    let last;
    let makefileRepaired = false;
    for (let i = 1; i <= 3; i++) {
        try {
            if (i > 1) onLine(tr(`编译未通过，正在第 ${i} 次编译 ${moduleName}\n`, `Build failed, compiling ${moduleName} again (${i})\n`));
            const { out } = await wslRun({
                MODE: 'build', TREE: WSL_TREE, PRODUCT: WSL_PRODUCT, MODULE: moduleName,
            }, onLine);
            const m = out.split('\n').map(l => l.trim()).find(l => l.startsWith('ARTIFACT:'));
            if (!m) throw new Error('build finished but no ARTIFACT line');
            return m.slice('ARTIFACT:'.length);
        } catch (e) {
            if (llmCancelled()) return false;
            last = e;
            if (e.makefileDefect && !makefileRepaired && repairTabletMakefile()) {
                makefileRepaired = true;
                onLine(tr(
                    '编译规则连到了下一行，已拆开，重新编译。\n',
                    'A build rule was stuck to the next line. Split it apart and compiling again.\n',
                ));
                continue;
            }
            if (e.missingArtifact || e.makefileDefect || !e.treeRace || i === 3) break;
            onLine(tr('这次失败来自构建过程，不改源码，准备重编\n', 'This failure is from the build itself. Source stays unchanged; rebuilding.\n'));
        }
    }
    throw last;
}

/* Binary stdout from bash -s. tar/cat of images must not be decoded as text. */
function bashStdout(script, onLine) {
    return new Promise((resolve, reject) => {
        let child;
        try { child = spawnBash({ ...process.env }); }
        catch (e) { reject(e); return; }
        if (llmJob) llmJob.track(child);
        child.stdin.write(script);
        child.stdin.end();
        const chunks = [];
        let err = '';
        child.stdout.on('data', d => chunks.push(Buffer.from(d)));
        child.stderr.on('data', d => {
            err += d.toString();
            if (onLine) onLine(d.toString());
        });
        child.on('close', rc => {
            if (rc === 0) resolve(Buffer.concat(chunks));
            else reject(new Error((err || '').trim() || `bash exit ${rc}`));
        });
        child.on('error', reject);
    });
}

async function wslPackage(moduleName, onLine) {
    const { out } = await wslRun({
        MODE: 'package', TREE: WSL_TREE, PRODUCT: WSL_PRODUCT, MODULE: moduleName,
    }, onLine);
    const m = out.split('\n').map(l => l.trim()).find(l => l.startsWith('MANIFEST:'));
    if (!m) {
        const tail = out.trim().split('\n').slice(-6).join(' | ');
        throw new Error(tail ? `package finished but no MANIFEST line: ${tail}` : 'package finished but no MANIFEST line');
    }
    const manifest = JSON.parse(m.slice('MANIFEST:'.length));

    const pkg = String(manifest.pkg || '');
    if (!/^[A-Za-z0-9._-]+$/.test(pkg)) throw new Error('bad package name');
    const buf = await bashStdout(`tar -C /tmp/studio_push_stage/${pkg} -czf - .\n`, onLine);
    return { manifest, tgz: buf };
}

function sendFrameAsync(type, payload) {
    return new Promise((resolve, reject) => {
        if (!dev.socket || !dev.connected) { reject(new Error('device not connected')); return; }
        const body = payload ? Buffer.from(payload) : Buffer.alloc(0);
        const hdr = Buffer.alloc(8);
        hdr.writeUInt32LE(type, 0);
        hdr.writeUInt32LE(body.length, 4);
        dev.socket.write(Buffer.concat([hdr, body]), err => (err ? reject(err) : resolve()));
    });
}

function deviceExec(cmd, ms = 60000) {
    if (!dev.connected) return Promise.reject(new Error('device not connected'));
    const p = waitFor(PT.EXEC_RESULT, ms);
    if (!sendFrame(PT.EXEC, Buffer.from(JSON.stringify({ cmd })))) {
        p.cancel();
        if (dev.connected) failLink(tr('命令发送失败', 'command send failed'));
        return Promise.reject(new Error('device send failed'));
    }
    return p.then(res => {
        if (!res) {
            if (dev.connected && dev.socket) failLink(tr('命令超时', 'command timed out'));
            throw new Error('device exec timeout');
        }
        try { return JSON.parse(res.toString('utf8')); }
        catch (e) {
            failLink(tr('命令结果无法解析', 'command result was not valid'));
            throw e;
        }
    });
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

/* After relaunch the old HELLO object is gone. Wait until a new one arrives. */
async function waitForAgent(ms = 20000) {
    const seen = dev.hello;
    const start = Date.now();
    while (Date.now() - start < 4000 && dev.connected && dev.hello === seen)
        await delay(200);
    while (Date.now() - start < ms) {
        if (dev.connected && dev.hello && dev.hello !== seen) return true;
        await delay(200);
    }
    return !!(dev.connected && dev.hello);
}

const FILE_ALLOW_PREFIXES = [
    '/tmp/', '/usr/apps/', '/etc/config/', '/mnt/', '/usr/data/', '/usr/lib/',
];
const FILE_EDIT_MAX = 512 * 1024;

function pathUnderAllow(p) {
    return FILE_ALLOW_PREFIXES.some(pre => {
        const root = pre.replace(/\/$/, '');
        return p === root || p.startsWith(pre);
    });
}

function normalizeDevPath(path) {
    let p = String(path || '').trim().replace(/\\/g, '/');
    if (!p.startsWith('/') || p.includes('..')) return '';
    p = p.replace(/\/+$/, '') || '/';
    if (!pathUnderAllow(p)) return '';
    return p;
}

function bufferLooksText(buf) {
    const b = Buffer.isBuffer(buf) ? buf : Buffer.from(buf);
    const n = Math.min(b.length, 4096);
    for (let i = 0; i < n; i++) {
        if (b[i] === 0) return false;
    }
    return true;
}

function shSingleQuoted(s) {
    return `'${String(s).replace(/'/g, `'\\''`)}'`;
}

async function fileGetViaExec(p) {
    const j = await deviceExec(`cat ${shSingleQuoted(p)} 2>&1`, 60000);
    const out = String(j.out ?? '');
    if (j.exit !== 0) {
        throw new Error(out.trim() || tr('无法打开文件', 'Cannot open file'));
    }
    const data = Buffer.from(out, 'utf8');
    if (data.length > FILE_EDIT_MAX) {
        throw new Error(tr(
            '文件过大（exec 上限 64KB）。请更新设备上的 studio，或命令行查看。',
            'The file is too large for exec fallback (64KB). Update studio on the device, or use the command line.',
        ));
    }
    if (!bufferLooksText(data)) {
        throw new Error(tr('二进制文件不能在页面里编辑', 'Binary files cannot be edited in the page'));
    }
    return { path: p, size: data.length, content: data.toString('utf8') };
}

const FILE_GET_WAIT_MS = 25000;

async function fileGet(path) {
    const p = normalizeDevPath(path);
    if (!p) throw new Error(tr('路径不在允许范围内', 'Path is not allowed'));
    if (!dev.connected) throw new Error(tr('设备未连接', 'Device not connected'));
    const wait = waitFor(PT.FILE_GET, FILE_GET_WAIT_MS);
    try {
        await sendFrameAsync(PT.FILE_GET, Buffer.from(JSON.stringify({ path: p })));
    } catch (e) {
        wait.cancel();
        throw e;
    }
    const res = await wait;
    if (!res || res.length < 8) return fileGetViaExec(p);
    const status = res.readUInt32LE(0);
    const size = res.readUInt32LE(4);
    if (status === 2) {
        throw new Error(tr(
            '文件过大，页面最多编辑 512KB。请在命令行用 cat、head 或 sed -n 查看。',
            'The file is too large for the editor (512KB max). Use cat, head, or sed -n in the command line.',
        ));
    }
    if (status === 1) throw new Error(tr('无法打开文件', 'Cannot open file'));
    if (status !== 0) throw new Error(tr(`读取失败 (${status})`, `Read failed (${status})`));
    const data = res.subarray(8, 8 + size);
    if (data.length > FILE_EDIT_MAX) {
        throw new Error(tr(
            '文件过大，页面最多编辑 512KB。请在命令行用 cat、head 或 sed -n 查看。',
            'The file is too large for the editor (512KB max). Use cat, head, or sed -n in the command line.',
        ));
    }
    if (!bufferLooksText(data)) {
        throw new Error(tr('二进制文件不能在页面里编辑', 'Binary files cannot be edited in the page'));
    }
    return { path: p, size: data.length, content: data.toString('utf8') };
}

async function pathKind(raw) {
    const p = normalizeDevPath(raw);
    if (!p || !dev.connected) return 'none';
    const j = await deviceExec(
        `if [ -d ${shSingleQuoted(p)} ]; then echo dir; elif [ -f ${shSingleQuoted(p)} ]; then echo file; else echo none; fi`,
        15000,
    );
    const k = String(j.out || '').trim();
    return k === 'dir' || k === 'file' ? k : 'none';
}

async function fileListDir(raw) {
    const p = normalizeDevPath(raw);
    if (!p) throw new Error(tr('路径不在允许范围内', 'Path is not allowed'));
    if (!dev.connected) throw new Error(tr('设备未连接', 'Device not connected'));
    const j = await deviceExec(`ls -1p ${shSingleQuoted(p)} 2>&1`, 30000);
    const out = String(j.out || '').trim();
    if (j.exit !== 0) throw new Error(out || tr('无法列出目录', 'Cannot list directory'));
    const names = out.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const entries = names.map(name => {
        const dir = name.endsWith('/');
        const base = name.replace(/\/$/, '');
        return { name: base, dir, path: `${p}/${base}` };
    }).sort((a, b) => (b.dir - a.dir) || a.name.localeCompare(b.name));
    return { path: p, entries };
}

async function mntStatus() {
    const ran = await deviceExec('mount 2>&1 | grep " on /mnt " || true', 15000);
    const line = String(ran.out || '').trim();
    const readOnly = line.length > 0 && /\(ro[,)]|\bro,|\bro\)/.test(line);
    return { line, readOnly };
}

async function remountMntRw() {
    return deviceExec('mount -o remount,rw /mnt 2>&1', 15000);
}

async function filePutFrame(path, body) {
    const pbuf = Buffer.from(path);
    const hdr = Buffer.alloc(8);
    hdr.writeUInt32LE(pbuf.length, 0);
    hdr.writeUInt32LE(body.length, 4);
    const ms = 20000 + Math.ceil(body.length / (64 * 1024)) * 1000;
    const done = waitFor(PT.EXEC_RESULT, ms);
    const err = waitFor(PT.ERR, ms);
    try {
        await sendFrameAsync(PT.FILE_PUT, Buffer.concat([hdr, pbuf, body]));
    } catch (e) {
        done.cancel();
        err.cancel();
        return { ok: false, error: String(e.message || e) };
    }
    const first = await Promise.race([
        done.then(res => ({ kind: 'done', res })),
        err.then(res => ({ kind: 'err', res })),
    ]);
    if (first.kind === 'done') err.cancel(); else done.cancel();
    if (!first.res) return { ok: false, timeout: true, error: `file put timeout ${path}` };
    let j = {};
    try { j = JSON.parse(first.res.toString('utf8')); } catch { j = {}; }
    if (first.kind === 'err') {
        const msg = j.error || first.res.toString('utf8');
        return /file_put/.test(msg) ? { ok: false, error: msg } : { ok: false, timeout: true, error: msg };
    }
    if (j.path && j.path !== path) return { ok: false, timeout: true, error: `file put reply was for ${j.path}` };
    if (!j.ok) return { ok: false, error: j.error || tr(`写入失败 ${path}`, `Write failed ${path}`) };
    return { ok: true, ...j };
}

async function deviceFileSize(path) {
    try {
        const j = await deviceExec(`wc -c < ${shSingleQuoted(path)} 2>/dev/null || echo -1`, 15000);
        const n = parseInt(String(j.out || '').trim(), 10);
        return Number.isFinite(n) ? n : -1;
    } catch {
        return -1;
    }
}

async function filePutViaExec(path, body) {
    const q = shSingleQuoted(path);
    const part = `${path}.part`;
    const qp = shSingleQuoted(part);
    const b64 = body.toString('base64');
    const step = 6000;
    let r = await deviceExec(`mkdir -p "$(dirname ${q})" && : > ${qp} && echo OK`, 15000);
    if (!/OK/.test(String(r.out || ''))) throw new Error(String(r.out || '').trim() || `cannot create ${part}`);
    for (let i = 0; i < b64.length; i += step) {
        r = await deviceExec(`printf '%s' '${b64.slice(i, i + step)}' | base64 -d >> ${qp} && echo OK`, 20000);
        if (!/OK/.test(String(r.out || ''))) throw new Error(String(r.out || '').trim() || `chunk write failed at ${i}`);
    }
    r = await deviceExec(`mv -f ${qp} ${q} && wc -c < ${q}`, 15000);
    const n = parseInt(String(r.out || '').trim(), 10);
    if (n !== body.length) throw new Error(tr(
        `${path} 写入后大小 ${Number.isFinite(n) ? n : '未知'}，应为 ${body.length}`,
        `${path} is ${Number.isFinite(n) ? n : 'unknown'} bytes after writing, expected ${body.length}`,
    ));
    return { ok: true, path, via: 'exec' };
}

async function filePut(path, data, onLine) {
    const pbuf = Buffer.from(path);
    const body = Buffer.isBuffer(data) ? data : Buffer.from(data);
    if (pbuf.length > 255) throw new Error('path too long');
    if (body.length > 16 * 1024 * 1024) throw new Error(`payload too large: ${body.length}`);
    const say = text => { if (onLine) onLine(text.endsWith('\n') ? text : text + '\n'); };
    let r = await filePutFrame(path, body);
    if (r.ok) return r;
    if (!r.timeout) throw new Error(r.error);
    if (await deviceFileSize(path) === body.length) return { ok: true, path, verified: true };
    say(tr(
        `上传 ${path} 没有收到设备回复，重连同步代理后再传一次。`,
        `No reply for ${path}. Reconnecting the sync agent and sending it again.`,
    ));
    if (dev.connected && dev.socket) failLink(tr('文件发送超时', 'file send timed out'));
    if (!await waitForAgent(30000)) throw new Error(tr('同步代理没有重新连上', 'The sync agent did not reconnect'));
    r = await filePutFrame(path, body);
    if (r.ok) return r;
    if (!r.timeout) throw new Error(r.error);
    if (await deviceFileSize(path) === body.length) return { ok: true, path, verified: true };
    if (body.length > 2 * 1024 * 1024) throw new Error(r.error);
    say(tr(
        `重传仍无回复，改用命令分段写入 ${path}（${body.length} 字节）。`,
        `Still no reply. Writing ${path} in chunks through commands (${body.length} bytes).`,
    ));
    return filePutViaExec(path, body);
}

/* USB app: replace the directory on /mnt/install. User area is a separate path. */
function usbInstallScript(dest) {
    if (!/^\/mnt\/install\/[A-Za-z0-9_./-]+$/.test(dest) || dest.includes('..'))
        throw new Error(`bad install dest ${dest}`);
    return `#!/bin/sh
DEST=${dest}
echo "place=usb dest=$DEST"
if [ ! -d /mnt/install ]; then
  echo FAIL usb not mounted
  exit 1
fi
rm -rf /tmp/studio_pkg "$DEST.bak"
mkdir -p /tmp/studio_pkg /mnt/install/.app_bak
tar -xzf /tmp/studio_push.tar.gz -C /tmp/studio_pkg || { echo FAIL unpack; exit 1; }
mkdir -p "$(dirname "$DEST")"
BAK=/mnt/install/.app_bak/$(basename "$DEST")
rm -rf "$BAK"
if [ -d "$DEST" ]; then mv "$DEST" "$BAK" || { echo FAIL backup; exit 1; }; fi
# /tmp is tmpfs and /mnt is vfat. mv across them tries to keep the Linux owner and fails.
if cp -R /tmp/studio_pkg "$DEST"; then
  rm -rf /tmp/studio_pkg
  chmod -R a+rX "$DEST" 2>/dev/null || true
  find "$DEST" -type f \\( -name '*.so' -o -name '*.sh' \\) -exec chmod 755 {} + 2>/dev/null || true
  echo OK usb "$DEST"
  exit 0
fi
rm -rf "$DEST"
if [ -d "$BAK" ]; then mv "$BAK" "$DEST"; fi
echo FAIL copy
exit 1
`;
}

/* APP partition is usr.sqsh4. Discover it by name, then erase and write. */
function flashUsrScript() {
    return `#!/bin/sh
img=/tmp/usr.sqsh4
[ -f "$img" ] || { echo FAIL missing image; exit 1; }
command -v mtd_debug >/dev/null 2>&1 || { echo FAIL mtd_debug missing; exit 1; }
info=\`grep '"APP"' /proc/mtd\`
[ -n "$info" ] || { echo FAIL no APP partition in /proc/mtd; exit 1; }
dev=/dev/\`echo "$info" | cut -d: -f1\`
hexsz=\`echo "$info" | cut -d' ' -f2\`
hexeb=\`echo "$info" | cut -d' ' -f3\`
psize=\$((0x$hexsz))
eb=\$((0x$hexeb))
isize=$(wc -c < "$img")
echo "APP $dev size=$psize image=$isize"
if [ "$isize" -gt "$psize" ]; then
  echo FAIL image larger than APP partition
  exit 1
fi
if [ "$eb" -gt 1 ]; then
  rem=$((isize % eb))
  if [ "$rem" -ne 0 ]; then
    need=$((eb - rem))
    dd if=/dev/zero bs=1 count=$need 2>/dev/null | tr '\\000' '\\377' >> "$img"
    isize=$((isize + need))
  fi
fi
echo "mtd_debug erase $dev 0 $psize"
mtd_debug erase "$dev" 0 "$psize" || { echo FAIL erase; exit 1; }
echo "mtd_debug write $dev 0 $isize $img"
mtd_debug write "$dev" 0 "$isize" "$img" || { echo FAIL write; exit 1; }
sync
echo OK usr "$dev"
sync
# /bin/sh often has no /sbin on PATH, so a bare reboot never runs.
/sbin/reboot >/dev/null 2>&1 &
exit 0
`;
}

async function wslUsrImage(moduleName, onLine) {
    const { out } = await wslRun({
        MODE: 'usrimage', TREE: WSL_TREE, PRODUCT: WSL_PRODUCT, MODULE: moduleName,
    }, onLine);
    const line = out.split('\n').map(l => l.trim()).find(l => l.startsWith('USRIMAGE:'));
    if (!line) {
        const tail = out.trim().split('\n').slice(-6).join(' | ');
        throw new Error(tail || 'usr image was not built');
    }
    return bashStdout('cat /tmp/studio_usr.sqsh4\n', onLine);
}

async function ensureAgentAlive(onLine) {
    if (dev.connected) {
        const pong = waitFor(PT.PONG, 5000);
        if (sendFrame(PT.PING, Buffer.alloc(0)) && await pong) return;
        pong.cancel();
    }
    onLine(tr('同步代理没有应答，正在重连。\n', 'The sync agent did not answer. Reconnecting.\n'));
    if (dev.connected && dev.socket) failLink(tr('推送前无应答', 'no answer before push'));
    if (!await waitForAgent(30000)) throw new Error(tr('同步代理没有重新连上', 'The sync agent did not reconnect'));
}

async function pushToDevice(moduleName, onLine) {
    if (!dev.connected && !await waitForAgent(15000)) throw new Error('device not connected');
    const packed = await wslPackage(moduleName, onLine);
    await ensureAgentAlive(onLine);
    const manifest = packed.manifest;
    const place = manifest.place === 'usr' ? 'usr' : 'usb';
    if (place === 'usr') {
        onLine(tr(
            `用户区 ${manifest.dest}：重新打包 usr.sqsh4，烧录 APP 分区\n`,
            `User area ${manifest.dest}: repack usr.sqsh4 and flash the APP partition\n`,
        ));
        const img = await wslUsrImage(moduleName, onLine);
        if (img.length > 16 * 1024 * 1024) throw new Error(`usr.sqsh4 too large: ${img.length}`);
        onLine(tr(`烧录镜像 ${img.length} 字节\n`, `Flashing image, ${img.length} bytes\n`));
        await filePut('/tmp/usr.sqsh4', img, onLine);
        await filePut('/tmp/nova_flash_usr.sh', flashUsrScript(), onLine);
        const ran = await deviceExec('/bin/sh /tmp/nova_flash_usr.sh 2>&1', 180000);
        const out = String(ran.out || '');
        onLine(out.endsWith('\n') ? out : out + '\n');
        if (!ran.ok || !/^OK /m.test(out))
            throw new Error(out.trim() || 'flash failed');
        sendFrame(PT.REBOOT, Buffer.alloc(0));
        return { ok: true, place, dest: manifest.dest, reboot: true, app_id: manifest.app_id, log: out.trim() };
    }
    const tgz = packed.tgz;
    if (tgz.length > 16 * 1024 * 1024) throw new Error(`payload too large: ${tgz.length}`);
    onLine(tr(
        `U 盘 ${manifest.dest}：直接替换目录（${tgz.length} 字节）\n`,
        `USB ${manifest.dest}: replace the directory (${tgz.length} bytes)\n`,
    ));
    await filePut('/tmp/studio_push.tar.gz', tgz, onLine);
    await filePut('/tmp/nova_install.sh', usbInstallScript(manifest.dest), onLine);
    const ran = await deviceExec('/bin/sh /tmp/nova_install.sh 2>&1', 120000);
    const out = String(ran.out || '');
    onLine(out.endsWith('\n') ? out : out + '\n');
    if (!ran.ok || !/^OK /m.test(out))
        throw new Error(out.trim() || 'install failed');
    return { ok: true, place, dest: manifest.dest, reboot: false, app_id: manifest.app_id, version: manifest.version, log: out.trim() };
}

/* ---------------- serial / wsl / llm panes ---------------- */
function paneSend(id, text) {
    sseSend('pane', { id, text: String(text).replace(/\r/g, '') });
}

let serialChild = null;

function listSerialPorts() {
    return new Promise(resolve => {
        const child = spawn('powershell.exe', ['-NoProfile', '-Command',
            '[System.IO.Ports.SerialPort]::GetPortNames()']);
        let out = '';
        child.stdout.on('data', d => { out += d.toString(); });
        child.on('close', () => {
            resolve(out.split(/\r?\n/).map(s => s.trim()).filter(s => /^COM\d+$/i.test(s)));
        });
        child.on('error', () => resolve([]));
    });
}

function closeSerial() {
    if (!serialChild) return;
    try { serialChild.stdin.write('__CLOSE__\n'); } catch { /* already gone */ }
    const cur = serialChild;
    setTimeout(() => { try { cur.kill(); } catch { /* ignore */ } }, 500);
    serialChild = null;
}

function openSerial(port, baud) {
    closeSerial();
    if (!/^COM\d+$/i.test(port)) throw new Error('bad port');
    const baudN = parseInt(baud, 10) || 115200;
    const failMsg = tr(
        '串口打开失败: " + $_.Exception.Message + "。请先关闭正在占用该口的终端。',
        'Failed to open the serial port: " + $_.Exception.Message + ". Close the terminal that is using this port.',
    );
    const script = `
$ErrorActionPreference = 'Stop'
$p = New-Object System.IO.Ports.SerialPort '${port}', ${baudN}
$p.ReadTimeout = 200
$p.DtrEnable = $false
$p.RtsEnable = $false
$p.Encoding = New-Object System.Text.UTF8Encoding $false
try { $p.Open() } catch {
  Write-Output ("${failMsg}")
  exit 1
}
Write-Output "OPEN ${port} ${baudN}"
$ps = [powershell]::Create()
[void]$ps.AddScript({
  param($sp)
  while ($sp.IsOpen) {
    try { $s = $sp.ReadExisting() } catch { $s = $null }
    if ($s) { [Console]::Out.Write($s); [Console]::Out.Flush() }
    Start-Sleep -Milliseconds 40
  }
}).AddArgument($p)
$null = $ps.BeginInvoke()
try {
  while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line -or $line -eq '__CLOSE__') { break }
    $p.WriteLine($line)
  }
} finally {
  if ($p.IsOpen) { $p.Close() }
}
`;
    serialChild = spawn('powershell.exe', ['-NoProfile', '-Command', script], { stdio: ['pipe', 'pipe', 'pipe'] });
    let pending = '';
    const on = buf => {
        pending += buf.toString();
        let i;
        while ((i = pending.search(/\r?\n/)) >= 0) {
            const line = pending.slice(0, i);
            pending = pending.slice(i).replace(/^\r?\n/, '');
            if (line) sseSend('log', { ts: Date.now() / 1000, level: 6, tag: 'serial', text: line });
        }
        if (pending.length > 4000) {
            sseSend('log', { ts: Date.now() / 1000, level: 6, tag: 'serial', text: pending });
            pending = '';
        }
    };
    serialChild.stdout.on('data', on);
    serialChild.stderr.on('data', on);
    serialChild.on('close', rc => sseSend('log', { ts: Date.now() / 1000, level: 6, tag: 'serial', text: tr(`串口已断开 (${rc})`, `Serial closed (${rc})`) }));
}

function runWsl(cmd) {
    let child;
    try { child = spawnBash({ ...process.env }); }
    catch (e) { paneSend('wsl', `\n[error] ${e.message}\n`); return; }
    child.stdin.write(`cd ${WSL_TREE} 2>/dev/null || cd ~/celestia/develop || exit 1\n${cmd}\n`);
    child.stdin.end();
    child.stdout.on('data', d => paneSend('wsl', d.toString()));
    child.stderr.on('data', d => paneSend('wsl', d.toString()));
    child.on('close', rc => paneSend('wsl', `\n[exit ${rc}]\n`));
    child.on('error', e => paneSend('wsl', `\n[error] ${e.message}\n`));
}

/* Known app names are discovered from the app tree itself: every tablet app
 * directory carries an app.json whose name is 中文(English). No hardcoded app
 * list lives in the tool. */
function knownAppPairs() {
    const pairs = [];
    const root = join(repoDir(), 'application', 'packages', 'tablet');
    let entries = [];
    try { entries = readdirSync(root, { withFileTypes: true }); } catch { entries = []; }
    for (const d of entries) {
        if (!d.isDirectory() || d.name === 'launcher') continue;
        const j = readAppJsonObject(d.name);
        if (!j || typeof j.name !== 'string') continue;
        const m = j.name.trim().match(/^(.+?)\(([^()]+)\)\s*$/);
        if (!m) continue;
        const zh = m[1].trim();
        const en = tidyEn(m[2]);
        if (zh && en) pairs.push({ zh, en, id: typeof j.app_id === 'string' ? j.app_id : d.name });
    }
    return pairs;
}

function hasHan(s) {
    return /[\u3400-\u9fff]/.test(s);
}

function repoDir() {
    const fromArg = argOf('--repo', '');
    if (fromArg) return resolve(fromArg);
    return join(ROOT, '..', '..');
}

function hostCwd() {
    const home = os.homedir();
    if (home && existsSync(home)) return home;
    throw new Error(tr('找不到当前用户目录。请在已登录的用户下运行 devstudio web。', 'Home directory was not found. Run devstudio web from a signed-in user.'));
}

/* Windows builds go through wsl.exe. Linux and macOS run bash directly. */
function spawnBash(env) {
    if (process.platform === 'win32')
        return spawn('wsl.exe', ['-e', 'bash', '-s'], { env: { ...env, WSL_UTF8: '1' }, cwd: hostCwd() });
    return spawn('bash', ['-s'], { env, cwd: hostCwd() });
}

const LLM_FILE = join(ROOT, 'llm.json');

function loadLlm() {
    const def = { provider: 'cursor', model: '', baseUrl: '', keys: {} };
    try {
        const j = JSON.parse(readFileSync(LLM_FILE, 'utf8'));
        const provider = llmProvider(j.provider) ? j.provider : 'cursor';
        const keys = {};
        if (j.keys && typeof j.keys === 'object') {
            for (const [k, v] of Object.entries(j.keys)) {
                if (llmProvider(k) && v) keys[k] = String(v);
            }
        }
        if (j.apiKey && !keys[provider]) keys[provider] = String(j.apiKey);
        return {
            provider,
            model: String(j.model || ''),
            baseUrl: String(j.baseUrl || ''),
            keys,
        };
    } catch {
        return def;
    }
}

function saveLlm(next) {
    writeFileSync(LLM_FILE, JSON.stringify({
        provider: next.provider,
        model: next.model,
        baseUrl: next.baseUrl,
        keys: next.keys,
    }, null, 2) + '\n');
}

function activeKey(cfg) {
    return cfg.keys?.[cfg.provider] || '';
}

function publicLlm(cfg) {
    const saved = {};
    for (const p of publicProviders()) saved[p.id] = !!cfg.keys?.[p.id];
    return {
        provider: cfg.provider,
        model: cfg.model,
        baseUrl: cfg.baseUrl,
        hasKey: !!activeKey(cfg),
        saved,
        providers: publicProviders(),
    };
}

function agentRoots() {
    const home = os.homedir();
    const local = process.env.LOCALAPPDATA || join(home, 'AppData', 'Local');
    return [
        join(local, 'cursor-agent', 'versions'),
        join(home, 'AppData', 'Roaming', 'Cursor', 'User', 'globalStorage', 'anysphere.cursor-agent-worker', 'agent-cli', '.local', 'share', 'cursor-agent', 'versions'),
        join(home, '.local', 'share', 'cursor-agent', 'versions'),
    ];
}

function agentBin() {
    let bestName = '';
    let bestDir = '';
    for (const root of agentRoots()) {
        if (!existsSync(root)) continue;
        const names = readdirSync(root, { withFileTypes: true })
            .filter(d => d.isDirectory() && !d.name.endsWith('.previous')
                && existsSync(join(root, d.name, 'index.js')))
            .map(d => d.name)
            .sort();
        const name = names[names.length - 1];
        if (name && name >= bestName) {
            bestName = name;
            bestDir = join(root, name);
        }
    }
    if (!bestDir)
        throw new Error(tr('未找到 Cursor agent。请安装 Cursor，再在大模型页点登录。', 'Cursor agent was not found. Install Cursor, then sign in on the Models page.'));
    const bundled = join(bestDir, process.platform === 'win32' ? 'node.exe' : 'node');
    return { node: existsSync(bundled) ? bundled : process.execPath, js: join(bestDir, 'index.js') };
}

function cursorCli(args) {
    const { node, js } = agentBin();
    const env = { ...process.env };
    delete env.CURSOR_API_KEY;
    return spawn(node, [js, ...args], { cwd: hostCwd(), env, windowsHide: true });
}

function readProc(child, ms) {
    return new Promise(resolve => {
        let out = '';
        let done = false;
        const finish = result => { if (!done) { done = true; resolve(result); } };
        const timer = setTimeout(() => finish({ rc: null, out, pending: true, child }), ms);
        const take = d => { out += d.toString(); };
        child.stdout.on('data', take);
        child.stderr.on('data', take);
        child.on('error', e => { clearTimeout(timer); finish({ rc: 1, out: e.message }); });
        child.on('close', rc => { clearTimeout(timer); finish({ rc, out }); });
    });
}

function cursorLoggedIn(text) {
    const s = String(text || '');
    return /logged in/i.test(s) && !/not logged in/i.test(s);
}

let cursorLoginChild = null;

async function cursorStatus() {
    let child;
    try { child = cursorCli(['status', '--format', 'text']); }
    catch (e) { return { ok: false, text: e.message }; }
    const r = await readProc(child, 20000);
    if (r.pending) {
        try { child.kill(); } catch { /* ignore */ }
        return { ok: false, text: tr('查询登录状态超时', 'Sign-in status timed out') };
    }
    if (cursorLoggedIn(r.out)) return { ok: true, text: tr('已登录', 'Signed in') };
    return { ok: false, text: tr('未登录', 'Not signed in') };
}

async function cursorLogin() {
    if (cursorLoginChild && cursorLoginChild.exitCode == null && !cursorLoginChild.killed) {
        const st = await cursorStatus();
        if (st.ok) return st;
        return { ok: false, text: tr('浏览器登录尚未完成', 'Browser sign-in is not finished') };
    }
    let child;
    try { child = cursorCli(['login']); }
    catch (e) { return { ok: false, text: e.message }; }
    cursorLoginChild = child;
    const r = await readProc(child, 12000);
    if (r.pending) return { ok: false, text: tr('已打开浏览器，完成登录后再点一次登录', 'The browser is open. Finish sign-in, then click Sign in again') };
    cursorLoginChild = null;
    if (r.rc === 0 || cursorLoggedIn(r.out)) return { ok: true, text: tr('已登录', 'Signed in') };
    const safe = String(r.out || '').replace(/[\w.+-]+@[\w.-]+/g, '').replace(/\s+/g, ' ').trim();
    return { ok: false, text: safe.slice(0, 120) || tr('登录未完成', 'Sign-in was not finished') };
}

function spawnAgent(prompt, { write = true, command = '' } = {}) {
    const { node, js } = agentBin();
    const cfg = loadLlm();
    const spec = llmProvider(cfg.provider) || llmProvider('cursor');
    const key = activeKey(cfg);
    const useLogin = spec.auth === 'login';
    if (!useLogin && !key) throw new Error(tr('请填写自己的 API Key。', 'Enter your API key.'));
    const args = [js];
    if (!useLogin) args.push('--api-key', key);
    if (command) {
        args.push(command);
    } else {
        args.push('-p', '--force', '--trust', '--sandbox', 'disabled', '--workspace', repoDir(), '--output-format', 'text');
        if (cfg.model) args.push('--model', cfg.model);
        if (!write) args.push('--mode', 'ask');
        args.push(prompt);
    }
    const env = { ...process.env };
    delete env.CURSOR_API_KEY;
    if (!useLogin && key) env.CURSOR_API_KEY = key;
    return spawn(node, args, { cwd: hostCwd(), env, windowsHide: true });
}

const NET_RETRY_CODES = new Set([
    'ECONNRESET', 'EPIPE', 'ECONNABORTED',
    'ENETUNREACH', 'EHOSTUNREACH', 'ECONNREFUSED',
    'ENOTFOUND', 'EAI_AGAIN', 'EADDRNOTAVAIL', 'ETIMEDOUT',
]);

function isChatTimeoutError(e) {
    if (!e) return false;
    if (e.name === 'TimeoutError') return true;
    const c = e.cause;
    if (c?.code === 'UND_ERR_HEADERS_TIMEOUT' || c?.code === 'UND_ERR_BODY_TIMEOUT' || c?.code === 'ECONNRESET') return true;
    return /aborted due to timeout|socket hang up|ETIMEDOUT|llm idle timeout/i.test(String(e.message || e));
}

function isChatRetryable(e) {
    if (isChatTimeoutError(e)) return true;
    if (!e || e.name === 'AbortError') return false;
    const code = e.code || e.cause?.code || '';
    if (NET_RETRY_CODES.has(code)) return true;
    return /\baborted\b|socket hang up|ECONNRESET|EPIPE|ENETUNREACH|EHOSTUNREACH|ECONNREFUSED|ENOTFOUND|EAI_AGAIN|ETIMEDOUT/i.test(String(e.message || e));
}

function llmChatSignal() {
    return llmJob?.signal;
}

/* Streaming idle timeout: no SSE bytes for this long => the link is dead; retry
 * reconnects. Streaming resets it on every chunk, and content is printed live,
 * so the user sees the request working. */
const LLM_STREAM_IDLE_MS = (() => {
    const s = Number(process.env.STUDIO_LLM_IDLE_S) || Number(process.env.STUDIO_LLM_IDLE_MS) || 0;
    return s > 0 && s < 1000 ? s * 1000 : (s || 60000);
})();
/* Buffered (non-stream) requests are silent until the whole reply is generated,
 * so they get a longer idle timeout plus a heartbeat line every 15s. */
const LLM_BUFFERED_IDLE_MS = (() => {
    const s = Number(process.env.STUDIO_LLM_BUFFERED_S) || 0;
    return s > 0 && s < 1000 ? s * 1000 : (s || 300000);
})();

function llmHttpRequest(urlStr, headers, body, signal, idleMs = LLM_STREAM_IDLE_MS) {
    const u = new URL(urlStr);
    const lib = u.protocol === 'http:' ? http : https;
    const payload = Buffer.from(body);
    return new Promise((resolve, reject) => {
        const req = lib.request({
            hostname: u.hostname,
            port: u.port || undefined,
            path: `${u.pathname}${u.search}`,
            method: 'POST',
            headers: { ...headers, 'Content-Length': payload.length },
        }, resolve);
        req.setTimeout(idleMs, () => req.destroy(Object.assign(
            new Error(`llm idle timeout: no data for ${Math.round(idleMs / 1000)}s`),
            { name: 'TimeoutError' },
        )));
        req.on('error', reject);
        const stop = () => req.destroy(Object.assign(new Error('aborted'), { name: 'AbortError' }));
        if (signal) {
            if (signal.aborted) { stop(); return; }
            signal.addEventListener('abort', stop, { once: true });
        }
        req.end(payload);
    });
}

function startWaitHeartbeat(pane, note) {
    if (!pane || !paneSend) return { stop() { } };
    const t0 = Date.now();
    const timer = setInterval(() => {
        paneSend(pane, tr(
            `…${note}，已等 ${Math.round((Date.now() - t0) / 1000)} 秒\n`,
            `…${note}, ${Math.round((Date.now() - t0) / 1000)}s elapsed\n`,
        ));
    }, 15000);
    return { stop: () => clearInterval(timer) };
}

async function llmBufferedRequest(pane, urlStr, headers, body, signal) {
    const hb = startWaitHeartbeat(pane, tr('仍在等待模型回复', 'still waiting for the model reply'));
    try {
        const res = await llmHttpRequest(urlStr, headers, body, signal, LLM_BUFFERED_IDLE_MS);
        const raw = await readHttpBody(res);
        return { res, raw };
    } finally {
        hb.stop();
    }
}

function readHttpBody(res) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
        res.on('error', reject);
    });
}

function chatSystemPrompt(writing) {
    if (!writing) {
        return tr('简洁回答。', 'Be concise.');
    }
    return tr(
        '你是源码编辑助手。回复只写一个 JSON 对象，第一个字符是 { 。改已经附上的文件用 {"edits":[{"path":"相对仓库根路径","search":"原文中连续的一小段","replace":"替换后的一小段"}]}，search 必须从附带源码原样复制且只出现一次。文件还不存在时不要编空的 search，改用 {"files":[{"path":"相对仓库根路径","content":"完整文件内容"}]}，content 必须是该文件全文。不要在 JSON 前后另写说明。产品行为有两种以上合理做法、不确认就会做错时，不要猜，改为只输出 {"ask":[{"prompt":"问句","options":["甲","乙"]}]}。实现细节自己决定，不要为此提问。不要问 Makefile、Kconfig、模块开关、构建文件在哪，也不要让用户粘贴仓库里已有的文件。这些由工具处理。',
        'You edit source. Reply with one JSON object whose first character is {. For a file that is already attached, use {"edits":[{"path":"path relative to the repo root","search":"a short unique snippet","replace":"the replacement"}]} and copy search from that source so it matches once. For a file that does not exist yet, do not invent an empty search; use {"files":[{"path":"path relative to the repo root","content":"the entire file"}]}. content must be the whole file. Do not add prose around the JSON. When a product behavior has more than one reasonable choice and guessing would do the wrong thing, do not guess: output only {"ask":[{"prompt":"question","options":["A","B"]}]}. Decide implementation details yourself and do not ask about them. Do not ask where the Makefile, Kconfig, module switch, or build files are, and do not ask the user to paste a file that is already in the repo. The tool handles those.',
    );
}

let lastFinishReason = '';

/* Endpoints whose streaming mode already failed this run (empty stream / unsupported SSE).
 * Once marked, later attempts go straight to the buffered request instead of
 * waiting through another multi-minute broken stream. */
const streamBrokenEndpoints = new Set();

function choiceText(j) {
    const choice = j?.choices?.[0] || {};
    const delta = choice.delta || {};
    const message = choice.message || {};
    const content = delta.content || delta.text || message.content || '';
    const reason = delta.reasoning_content || delta.reasoning || message.reasoning_content || '';
    return {
        content: String(content || ''),
        reason: String(reason || ''),
        finish: choice.finish_reason || '',
    };
}

function pickReply(content, reason) {
    const c = String(content || '').trim();
    const r = String(reason || '').trim();
    if (c && parseModelPayload(c)) return c;
    if (r && parseModelPayload(r)) return r;
    return c || r;
}

async function chatOpenAIStream(req, signal, onChunk) {
    const res = await llmHttpRequest(req.url, req.headers, JSON.stringify({ ...req.body, stream: true }), signal);
    if (res.statusCode < 200 || res.statusCode >= 300) {
        const t = await readHttpBody(res);
        let msg = t.slice(0, 240);
        try { const j = JSON.parse(t); msg = j.error?.message || j.message || msg; } catch { /* text */ }
        throw new Error(msg || `HTTP ${res.statusCode}`);
    }
    let buf = '';
    let all = '';
    let out = '';
    let reason = '';
    const take = j => {
        const part = choiceText(j);
        if (part.finish) lastFinishReason = part.finish;
        if (part.reason) {
            reason += part.reason;
            if (onChunk) onChunk(part.reason, 'reason');
        }
        if (part.content) {
            out += part.content;
            if (onChunk) onChunk(part.content, 'content');
        }
    };
    for await (const chunk of res) {
        all += chunk.toString('utf8');
        buf += chunk.toString('utf8');
        for (;;) {
            const nl = buf.indexOf('\n');
            if (nl < 0) break;
            const line = buf.slice(0, nl).trim();
            buf = buf.slice(nl + 1);
            if (!line.startsWith('data:')) continue;
            const data = line.slice(5).trim();
            if (data === '[DONE]') continue;
            try { take(JSON.parse(data)); } catch { /* ignore broken sse chunk */ }
        }
    }
    if (!out.trim()) {
        try { take(JSON.parse(all.trim())); } catch { /* not a single json body */ }
    }
    const text = pickReply(out, reason);
    if (!text.trim()) throw new Error('empty stream');
    return text;
}

function chatRequest(spec, cfg, prompt, opts = {}) {
    const key = activeKey(cfg);
    const model = cfg.model || spec.models?.[0] || '';
    const base = (cfg.baseUrl || spec.base || '').replace(/\/$/, '');
    const maxOut = opts.maxTokens ?? 16384;
    if (spec.wire === 'anthropic') {
        return {
            url: 'https://api.anthropic.com/v1/messages',
            headers: {
                'Content-Type': 'application/json',
                'x-api-key': key,
                'anthropic-version': '2023-06-01',
            },
            body: { model, max_tokens: maxOut, messages: [{ role: 'user', content: prompt }] },
            text: j => (j.content || []).map(p => p.text || '').join(''),
        };
    }
    if (spec.wire === 'gemini') {
        return {
            url: `${base}/models/${encodeURIComponent(model)}:generateContent`,
            headers: { 'Content-Type': 'application/json', 'x-goog-api-key': key },
            body: { contents: [{ parts: [{ text: prompt }] }] },
            text: j => (j.candidates?.[0]?.content?.parts || []).map(p => p.text || '').join(''),
        };
    }
    const headers = { 'Content-Type': 'application/json' };
    if (spec.wire === 'azure') headers['api-key'] = key;
    else headers.Authorization = `Bearer ${key}`;
    const sys = chatSystemPrompt(!!opts.writing);
    const messages = Array.isArray(opts.messages) && opts.messages.length
        ? opts.messages
        : [
            { role: 'system', content: sys },
            { role: 'user', content: prompt },
        ];
    const body = {
        model,
        messages,
        max_tokens: maxOut,
    };
    if (opts.writing) body.temperature = 0;
    if (opts.writing && opts.thinkingOff !== false) {
        body.enable_thinking = false;
        body.thinking = { type: 'disabled' };
        body.chat_template_kwargs = { enable_thinking: false };
        // This gateway spends the whole token budget on reasoning_content and leaves content empty.
        body.reasoning_effort = 'none';
    }
    if (opts.jsonOnly) body.response_format = { type: 'json_object' };
    return {
        url: base + '/chat/completions',
        headers,
        body,
        text: j => {
            const part = choiceText(j);
            if (part.finish) lastFinishReason = part.finish;
            return pickReply(part.content, part.reason);
        },
    };
}

function parseFilesJson(raw) {
    try {
        const j = JSON.parse(raw);
        const files = Array.isArray(j) ? j : j.files;
        if (Array.isArray(files) && files.some(f => f && f.path)) return files;
    } catch { /* bad json */ }
    return null;
}

function extractFileEdits(text) {
    const raw = String(text || '').trim();
    const whole = parseFilesJson(raw);
    if (whole) return whole;
    const fence = raw.match(/^```(?:json)?\s*([\s\S]*?)```$/);
    if (fence) {
        const inner = parseFilesJson(fence[1].trim());
        if (inner) return inner;
    }
    const s = raw;
    const blocks = [];
    const re = /```(?:json)?\s*([\s\S]*?)```/g;
    let m;
    while ((m = re.exec(s))) blocks.push(m[1]);
    for (let i = blocks.length - 1; i >= 0; i--) {
        const files = parseFilesJson(blocks[i].trim());
        if (files) return files;
    }
    let pos = s.length;
    while (pos > 0) {
        const idx = s.lastIndexOf('{"files"', pos - 1);
        if (idx < 0) break;
        for (let end = Math.min(s.length, idx + 900000); end > idx; end--) {
            const slice = s.slice(idx, end).trim();
            if (!slice.endsWith('}')) continue;
            const files = parseFilesJson(slice);
            if (files) return files;
        }
        pos = idx;
    }
    return null;
}

function appDirFromTarget(target) {
    const t = String(target || '').trim();
    if (t.includes('/')) {
        const rel = t.replace(/\\/g, '/').replace(/^\.?\//, '');
        if (rel.includes('..')) return null;
        const abs = resolve(repoDir(), rel);
        const root = resolve(repoDir());
        if (abs !== root && !abs.startsWith(root + sep)) return null;
        return abs;
    }
    const id = appIdOf(t);
    return id ? join(repoDir(), 'application', 'packages', 'tablet', id) : null;
}

function listAppTextFiles(dir, bag, depth = 0) {
    if (depth > 4 || bag.length >= 32) return;
    let ents;
    try { ents = readdirSync(dir, { withFileTypes: true }); } catch { return; }
    ents.sort((a, b) => a.name.localeCompare(b.name));
    for (const e of ents) {
        const p = join(dir, e.name);
        if (e.isDirectory()) {
            if (e.name === 'obj' || e.name.startsWith('.')) continue;
            listAppTextFiles(p, bag, depth + 1);
            continue;
        }
        if (!e.isFile()) continue;
        const low = e.name.toLowerCase();
        if (/\.(png|jpg|jpeg|gif|webp|ico|o|a|so|bin)$/.test(low)) continue;
        if (e.name === 'Makefile' || /\.(c|h|js|css|html|json|mk|txt|md|cmake|in)$/i.test(e.name))
            bag.push(p);
    }
}

function repoContextBlock() {
    const root = repoDir().replace(/\\/g, '/');
    return tr(
        `【环境 · 必读】\n` +
        `- 源码仓库根：${root}（所有 json path 相对此目录，不要用 /workspace）。\n` +
        `- Celestia Studio 仅在 devstudio/web/；WSL 编译走 build.sh，与工具同仓库。\n` +
        `- Tablet 应用：application/packages/tablet/<id>/，应用一切信息（app.json、源码、图标描述）都在应用自己的目录里。\n` +
        `- 设备上的界面多是 C + LVGL，入口多为 app.json、tablet_*.c、app_entry_*.c。目录里若有 index.js、index.html，也是源码，用户要求改到它们时就改。\n` +
        `- 生成与推送模式下你不能执行 find/ls/cat/bash，也不要写「先探索目录」。下文附源码，必须用 json 写回文件。\n`,
        `【Environment · required】\n` +
        `- Repository root: ${root} (all json paths are relative here; do not use /workspace).\n` +
        `- Celestia Studio lives only under devstudio/web/; WSL builds via build.sh in the same repo.\n` +
        `- Tablet apps: application/packages/tablet/<id>/; every bit of app info (app.json, source, icon description) lives in the app's own directory.\n` +
        `- On-device UI is usually C + LVGL: app.json, tablet_*.c, app_entry_*.c. index.js and index.html in the app folder are source too; edit them when the request needs them.\n` +
        `- In Generate & Push you cannot run find/ls/cat/bash or say you will explore first. Source is attached below; you must write files via json.\n`,
    );
}

function appFileListBlock(target) {
    const id = appIdOf(target);
    if (!id) return '';
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    if (!existsSync(dir)) {
        return tr(
            `application/packages/tablet/${id}/ 还不存在。这是新应用，用 files 把全文写到这个目录。不要询问 Makefile 或模块开关。\n`,
            `application/packages/tablet/${id}/ does not exist yet. This is a new app: write the whole files there with files. Do not ask about the Makefile or the module switch.\n`,
        );
    }
    const paths = [];
    listAppTextFiles(dir, paths);
    const rels = paths.map(abs => abs.slice(repoDir().length).replace(/\\/g, '/').replace(/^[/\\]+/, ''));
    if (!rels.length) return '';
    return tr(
        `应用 ${id} 目录内可改文件：\n${rels.join('\n')}\n`,
        `Editable text files under app ${id}:\n${rels.join('\n')}\n`,
    );
}

function appSourcesBlock(target) {
    const dir = appDirFromTarget(target);
    if (!dir || !existsSync(dir)) return '';
    const paths = [];
    listAppTextFiles(dir, paths);
    const root = repoDir();
    const lines = [
        tr(
            '【附：应用源码】已有文件用 {"edits":[{"path","search","replace"}]}，search 必须是下面原文里的一小段且只出现一次。还没有的文件用 {"files":[{"path","content"}]} 写全文。',
            '【Attached source】For a file below, use {"edits":[{"path","search","replace"}]} and copy a short search snippet that matches once. For a file that is not here yet, use {"files":[{"path","content"}]} with the whole file.',
        ),
    ];
    let total = 0;
    const cap = 120000;
    for (const abs of paths) {
        if (total >= cap) {
            lines.push(tr('（后续文件已省略，先改上面列出的文件。）', '(Later files omitted. Start with the files above.)'));
            break;
        }
        let rel = abs.slice(root.length).replace(/\\/g, '/');
        if (rel.startsWith(sep) || rel.startsWith('/')) rel = rel.replace(/^[\\/]+/, '');
        try {
            let content = readFileSync(abs, 'utf8');
            if (content.length > 48000) {
                content = content.slice(0, 48000) + '\n/* ... truncated ... */\n';
            }
            total += content.length;
            lines.push(`\n--- ${rel} ---\n${content}`);
        } catch { /* skip unreadable */ }
    }
    return lines.join('\n');
}

function safeRepoPath(rel) {
    const path = String(rel || '').replace(/\\/g, '/').replace(/^\.?\//, '');
    if (!path || path.split('/').includes('..') || path === '.git' || path.startsWith('.git/'))
        throw new Error(tr(`拒绝写入 ${path || '(空路径)'}`, `Refusing to write ${path || '(empty path)'}`));
    if (/(^|\/)(llm|device)\.json$/.test(path))
        throw new Error(tr(`拒绝写入 ${path}`, `Refusing to write ${path}`));
    const dest = resolve(resolve(repoDir()), path);
    const root = resolve(repoDir());
    if (dest !== root && !dest.startsWith(root + sep))
        throw new Error(tr(`拒绝写入 ${path}`, `Refusing to write ${path}`));
    return { path, dest };
}

function fileBody(e) {
    const take = v => {
        if (v == null) return '';
        if (typeof v === 'string') return v;
        if (typeof v === 'object') return JSON.stringify(v, null, 2);
        return String(v);
    };
    const replace = take(e.replace);
    if (replace.trim()) return replace;
    return take(e.content);
}

function applySearchEdits(edits) {
    const written = [];
    if (!Array.isArray(edits) || !edits.length) throw new Error(tr('回复里没有可写入的文件', 'The reply has no files to write'));
    if (edits.length > 40) throw new Error(tr('一次改动的片段过多', 'Too many edits in one change'));
    const dirty = new Map();
    for (const e of edits) {
        const { path, dest } = safeRepoPath(e.path);
        const search = String(e.search ?? '');
        const replace = fileBody(e);
        const prior = dirty.has(dest) ? dirty.get(dest) : (existsSync(dest) ? readFileSync(dest, 'utf8') : null);
        if (!search.trim()) {
            if (!replace.trim()) throw new Error(tr(`${path} 是新文件，但没有给出全文`, `${path} is a new file, but the reply did not include its contents`));
            if (prior) throw new Error(tr(`${path} 的 search 为空，不能整文件覆盖已有源码`, `${path} has an empty search, so an existing file was not replaced`));
            dirty.set(dest, replace);
            if (!written.includes(path)) written.push(path);
            continue;
        }
        const content = prior == null ? '' : prior;
        const n = content.split(search).length - 1;
        if (n !== 1) throw new Error(tr(`${path} 的原文片段匹配了 ${n} 处`, `${path} snippet matched ${n} times`));
        dirty.set(dest, content.replace(search, replace));
        if (!written.includes(path)) written.push(path);
    }
    for (const [dest, content] of dirty) {
        mkdirSync(dirname(dest), { recursive: true });
        writeFileSync(dest, content.endsWith('\n') ? content : content + '\n');
    }
    return written;
}

function repairJson(raw) {
    const pair = { '{': '}', '[': ']' };
    const stack = [];
    let out = '';
    let inStr = false;
    let esc = false;
    for (const ch of String(raw || '')) {
        if (inStr) {
            if (esc) { esc = false; out += ch; continue; }
            if (ch === '\\') { esc = true; out += ch; continue; }
            if (ch === '"') { inStr = false; out += ch; continue; }
            if (ch === '\n') { out += '\\n'; continue; }
            if (ch === '\r') { out += '\\r'; continue; }
            if (ch === '\t') { out += '\\t'; continue; }
            out += ch;
            continue;
        }
        if (ch === '"') { inStr = true; out += ch; continue; }
        if (ch === '{' || ch === '[') { stack.push(pair[ch]); out += ch; continue; }
        if (ch === '}' || ch === ']') {
            while (stack.length && stack[stack.length - 1] !== ch) out += stack.pop();
            if (stack.length) stack.pop();
            out += ch;
            continue;
        }
        out += ch;
    }
    if (inStr) out += '"';
    while (stack.length) out += stack.pop();
    return out;
}

function parseModelPayload(text) {
    const s = String(text || '').trim();
    const tryOne = (raw, strict = false) => {
        let j;
        try { j = JSON.parse(raw); }
        catch {
            if (strict || !raw) return null;
            try { j = JSON.parse(repairJson(raw)); } catch { return null; }
        }
        try {
            if (Array.isArray(j)) return { files: j };
            if (j && Array.isArray(j.edits) && j.edits.length) return { edits: j.edits };
            if (j && Array.isArray(j.files) && j.files.length) return { files: j.files };
            if (j && Array.isArray(j.ask) && j.ask.length) return { ask: j.ask };
        } catch { /* not json */ }
        return null;
    };
    const direct = tryOne(s) || tryOne((s.match(/^```(?:json)?\s*([\s\S]*?)```$/) || [])[1] || '');
    if (direct) return direct;
    const blocks = [];
    const re = /```(?:json)?\s*([\s\S]*?)```/g;
    let m;
    while ((m = re.exec(s))) blocks.push(m[1]);
    for (let i = blocks.length - 1; i >= 0; i--) {
        const got = tryOne(blocks[i].trim());
        if (got) return got;
    }
    for (const key of ['{"edits"', '{"files"', '{"ask"']) {
        let pos = s.length;
        while (pos > 0) {
            const idx = s.lastIndexOf(key, pos - 1);
            if (idx < 0) break;
            for (let end = Math.min(s.length, idx + 900000); end > idx; end--) {
                const slice = s.slice(idx, end).trim();
                if (!slice.endsWith('}')) continue;
                const got = tryOne(slice, true);
                if (got) return got;
            }
            const loose = tryOne(s.slice(idx).trim());
            if (loose) return loose;
            pos = idx;
        }
    }
    return null;
}

function applyModelReply(text) {
    const payload = parseModelPayload(text);
    if (!payload) throw new Error(tr('回复里没有可写入的文件', 'The reply has no files to write'));
    if (payload.edits) return applySearchEdits(payload.edits);
    if (payload.files) return applyFileEdits(payload.files);
    throw new Error(tr('回复里没有可写入的文件', 'The reply has no files to write'));
}

function applyFileEdits(files) {
    const written = [];
    if (!Array.isArray(files) || !files.length) throw new Error(tr('回复里没有可写入的文件', 'The reply has no files to write'));
    if (files.length > 30) throw new Error(tr('一次改动的文件过多', 'Too many files in one change'));
    for (const f of files) {
        const { path: rel, dest } = safeRepoPath(f.path);
        const content = String(f.content ?? '');
        if (content.length > 400000) throw new Error(tr(`${rel} 过大`, `${rel} is too large`));
        mkdirSync(dirname(dest), { recursive: true });
        if (String(f.encoding || '').toLowerCase() === 'base64') {
            writeFileSync(dest, Buffer.from(content.replace(/\s/g, ''), 'base64'));
        } else {
            writeFileSync(dest, content.endsWith('\n') ? content : content + '\n');
        }
        written.push(rel);
    }
    return written;
}

let llmJob = null;

function llmCancelled() {
    return !!(llmJob && llmJob.signal.aborted);
}

function startLlmJob(pane) {
    const ctrl = new AbortController();
    const children = new Set();
    const job = {
        pane,
        signal: ctrl.signal,
        track(child) {
            if (ctrl.signal.aborted) {
                try { child.kill(); } catch { /* already stopping */ }
                return;
            }
            children.add(child);
            child.once('close', () => children.delete(child));
        },
        stop() {
            if (ctrl.signal.aborted) return;
            ctrl.abort();
            for (const child of children) {
                try { child.kill(); } catch { /* already gone */ }
            }
            closeAsk(null);
            paneSend(pane, tr('\n已取消\n', '\nCancelled\n'));
        },
    };
    llmJob = job;
    sseSend('job', { busy: true });
    return job;
}

function finishLlmJob(job) {
    if (llmJob !== job) return;
    llmJob = null;
    sseSend('job', { busy: false });
}

function cancelLlmJob() {
    if (!llmJob) return false;
    llmJob.stop();
    return true;
}

let pendingAsk = null;

function closeAsk(value) {
    if (!pendingAsk) return;
    const cur = pendingAsk;
    pendingAsk = null;
    sseSend('ask', { id: cur.id, done: true });
    cur.resolve(value);
}

function askUser(pane, spec) {
    if (llmCancelled()) return Promise.resolve(null);
    const id = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
    paneSend(pane, tr('请在下面做出选择，选完再继续。\n', 'Choose below. The run continues after you answer.\n'));
    return new Promise(resolve => {
        pendingAsk = { id, kind: spec.kind || '', resolve };
        sseSend('ask', {
            id,
            kind: spec.kind || '',
            title: spec.title || '',
            hint: spec.hint || '',
            fields: spec.fields || [],
            questions: spec.questions || [],
            note: spec.note || '',
            error: spec.error || '',
        });
    });
}

function normalizeQuestions(list) {
    const out = [];
    for (const q of list || []) {
        const prompt = String(q.prompt || q.q || '').trim();
        const options = (Array.isArray(q.options) ? q.options : [])
            .map(s => String(s).trim()).filter(Boolean).slice(0, 6);
        if (!prompt || options.length < 2) continue;
        out.push({ prompt, options });
        if (out.length >= 3) break;
    }
    return out;
}

function formatPicks(questions, answer) {
    const lines = [];
    (questions || []).forEach((q, i) => {
        const pick = answer && answer.picks && answer.picks[i];
        if (pick) lines.push(`${q.prompt}：${pick}`);
    });
    const note = String(answer && answer.note || '').trim();
    if (note) lines.push(note);
    return lines.join('\n');
}

function parseClarify(out) {
    const s = String(out || '');
    const start = s.indexOf('{');
    const end = s.lastIndexOf('}');
    if (start < 0 || end <= start) return { failed: true };
    let j;
    try { j = JSON.parse(s.slice(start, end + 1)); }
    catch { return { failed: true }; }
    if (j.clear === true) return { clear: true, questions: [] };
    const questions = normalizeQuestions(j.questions || j.ask);
    if (questions.length) return { clear: false, questions };
    return { failed: true };
}

function writeJsonTail() {
    return tr(
        '回复只含这个 JSON，第一个字符是 { 。已有文件才用 edits，search 必须能在附带源码里唯一匹配。新文件用 {"files":[{"path":"application/packages/tablet/<id>/某文件","content":"完整文件内容"}]}，content 写全文，不要提交空的 search。',
        'Reply with only this JSON. The first character is {. Use edits only for a file that already exists, and search must match the attached source once. For a new file use {"files":[{"path":"application/packages/tablet/<id>/file","content":"the entire file"}]}. content is the whole file. Do not send an empty search.',
    );
}

function jsonModeRejected(msg) {
    return /response_format|json_object|json_schema|unsupported/i.test(String(msg || ''));
}

function thinkingParamRejected(msg) {
    return /chat_template|enable_thinking|thinking|reasoning_effort|unknown parameter|unexpected keyword|extra inputs|unrecognized/i.test(String(msg || ''));
}

function showModelText(pane, text, writing, alreadyShown) {
    if (alreadyShown) return;
    const raw = String(text || '').trim();
    if (!raw) return;
    if (parseModelPayload(raw)) return;
    if (!parseClarify(raw).failed) return;
    const shown = raw.length > 500 ? `${raw.slice(0, 500)}…` : raw;
    paneSend(pane, '\n' + shown + '\n');
}

async function chatOnce(pane, cfg, spec, asked, opts = {}) {
    let jsonOnly = !!opts.jsonOnly;
    let thinkingOff = opts.thinkingOff !== undefined ? !!opts.thinkingOff : !!opts.writing;
    lastFinishReason = '';
    const reqOf = () => chatRequest(spec, cfg, asked, {
        maxTokens: opts.maxTokens,
        writing: !!opts.writing,
        messages: opts.messages,
        jsonOnly,
        thinkingOff,
    });
    let req = reqOf();
    const signal = llmChatSignal();
    const streamKey = `${cfg.baseUrl || spec.base || ''}|${cfg.model || ''}`;
    const useStream = (spec.wire === 'openai' || spec.wire === 'azure') && !streamBrokenEndpoints.has(streamKey);
    const emitPiece = () => {
        let started = false;
        return (piece) => {
            if (!piece) return false;
            if (!started) {
                paneSend(pane, '\n');
                started = true;
            }
            paneSend(pane, piece);
            return true;
        };
    };
    if (useStream) {
        const emit = emitPiece();
        let live = false;
        let contentAcc = '';
        try {
            const text = await chatOpenAIStream(req, signal, (piece, kind) => {
                if (kind !== 'content') return;
                contentAcc += piece;
                const head = contentAcc.trimStart();
                if (head.startsWith('{') || head.startsWith('```')) return;
                if (emit(piece)) live = true;
            });
            if (live) paneSend(pane, '\n');
            showModelText(pane, text, !!opts.writing, live);
            return text;
        } catch (e) {
            const msg = String(e.message || e);
            if (jsonOnly && jsonModeRejected(msg)) {
                jsonOnly = false;
                req = reqOf();
                paneSend(pane, tr('接口不接受 JSON 模式，改为普通回复…\n', 'Endpoint rejected JSON mode. Using a normal reply…\n'));
            } else if (thinkingOff && thinkingParamRejected(msg)) {
                thinkingOff = false;
                req = reqOf();
                paneSend(pane, tr('接口不接受关闭思考的参数，改为普通请求…\n', 'Endpoint rejected the thinking switch. Sending a plain request…\n'));
            } else if (!/empty stream|stream|sse|event-stream|not support/i.test(msg)) {
                throw e;
            } else {
                streamBrokenEndpoints.add(streamKey);
                paneSend(pane, tr(`流式失败（${msg.slice(0, 80)}），改一次性请求…\n`, `Stream failed (${msg.slice(0, 80)}), trying buffered…\n`));
            }
        }
    }
    const { res, raw } = await llmBufferedRequest(pane, req.url, req.headers, JSON.stringify(req.body), signal);
    let j = {};
    try { j = raw ? JSON.parse(raw) : {}; } catch { /* non-json */ }
    if (res.statusCode < 200 || res.statusCode >= 300) {
        const msg = j.error?.message || j.message || raw.slice(0, 200) || `HTTP ${res.statusCode}`;
        if (jsonOnly && jsonModeRejected(msg)) {
            paneSend(pane, tr('接口不接受 JSON 模式，改为普通回复…\n', 'Endpoint rejected JSON mode. Using a normal reply…\n'));
            const plain = chatRequest(spec, cfg, asked, {
                maxTokens: opts.maxTokens,
                writing: !!opts.writing,
                messages: opts.messages,
                jsonOnly: false,
            });
            const r2 = await llmBufferedRequest(pane, plain.url, plain.headers, JSON.stringify(plain.body), signal);
            const res2 = r2.res; const raw2 = r2.raw;
            let j2 = {};
            try { j2 = raw2 ? JSON.parse(raw2) : {}; } catch { /* non-json */ }
            if (res2.statusCode < 200 || res2.statusCode >= 300) throw new Error(j2.error?.message || j2.message || raw2.slice(0, 200) || `HTTP ${res2.statusCode}`);
            const text2 = plain.text(j2);
            showModelText(pane, text2, !!opts.writing, false);
            return text2;
        }
        if (thinkingOff && thinkingParamRejected(msg)) {
            thinkingOff = false;
            const plain = reqOf();
            paneSend(pane, tr('接口不接受关闭思考的参数，改为普通请求…\n', 'Endpoint rejected the thinking switch. Sending a plain request…\n'));
            const r2 = await llmBufferedRequest(pane, plain.url, plain.headers, JSON.stringify(plain.body), signal);
            const res2 = r2.res; const raw2 = r2.raw;
            let j2 = {};
            try { j2 = raw2 ? JSON.parse(raw2) : {}; } catch { /* non-json */ }
            if (res2.statusCode < 200 || res2.statusCode >= 300) throw new Error(j2.error?.message || j2.message || raw2.slice(0, 200) || `HTTP ${res2.statusCode}`);
            const text2 = plain.text(j2);
            showModelText(pane, text2, !!opts.writing, false);
            return text2;
        }
        throw new Error(msg);
    }
    const text = req.text(j);
    showModelText(pane, text, !!opts.writing, false);
    return text;
}

async function runChat(pane, prompt, cfg, spec, opts = {}) {
    if (typeof opts === 'boolean') opts = { write: opts };
    const write = opts.write !== false;
    const name = providerName(spec) || cfg.provider;
    if (!activeKey(cfg)) {
        paneSend(pane, tr(
            `\n[error] 未填写 ${name} 的 API Key。在右上角填写后点保存，或启动时加 -Llm ${spec.id} -ApiKey。\n`,
            `\n[error] No API key for ${name}. Enter it at the top right and save, or start with -Llm ${spec.id} -ApiKey.\n`,
        ));
        return { rc: 1, out: '' };
    }
    if (!cfg.model && spec.modelFree) {
        paneSend(pane, tr(`\n[error] 请填写 ${name} 的模型 ID。\n`, `\n[error] Enter the model ID for ${name}.\n`));
        return { rc: 1, out: '' };
    }
    if (spec.baseFree && !(cfg.baseUrl || spec.base)) {
        paneSend(pane, tr(`\n[error] 请填写 ${name} 的接口地址。\n`, `\n[error] Enter the endpoint for ${name}.\n`));
        return { rc: 1, out: '' };
    }
    paneSend(pane, tr('正在连接模型接口…\n', 'Connecting to the model…\n'));
    const canWrite = write && spec.writes !== false;
    if (write && spec.writes === false) {
        paneSend(pane, tr(
            `${name} 只返回文本，不会改这台电脑上的源码。\n`,
            `${name} only returns text and will not change source on this computer.\n`,
        ));
    }
    const tail = writeJsonTail();
    let jsonTries = canWrite ? 3 : 1;
    const netTries = 5;
    const netBackoff = [1000, 3000, 8000, 15000];
    let askRounds = 0;
    let asked = canWrite ? `${prompt}\n\n${tail}` : prompt;
    let history = null;
    let lastText = '';
    let netFail = false;
    let outCap = opts.maxTokens || (canWrite ? 12288 : 2048);
    let relaxWire = false;
    for (let jsonTry = 1; jsonTry <= jsonTries; jsonTry++) {
        if (llmCancelled()) return { rc: 1, out: lastText };
        let text = '';
        let gotText = false;
        for (let netTry = 1; netTry <= netTries; netTry++) {
            if (llmCancelled()) return { rc: 1, out: lastText };
            if (canWrite && netTry === 1)
                paneSend(pane, tr('模型正在写源码…\n', 'The model is writing source…\n'));
            try {
                text = await chatOnce(pane, cfg, spec, asked, {
                    maxTokens: canWrite ? outCap : (opts.maxTokens || 2048),
                    writing: canWrite,
                    jsonOnly: false,
                    thinkingOff: canWrite && !relaxWire,
                    messages: history,
                });
                lastText = text;
                gotText = true;
                break;
            } catch (e) {
                if (llmJob && llmJob.signal.aborted) return { rc: 1, out: lastText };
                if (isChatRetryable(e) && netTry < netTries) {
                    relaxWire = true;
                    if (canWrite) outCap = Math.min(32768, outCap + 8192);
                    const limit = canWrite ? outCap : (opts.maxTokens || 2048);
                    const why = isChatTimeoutError(e)
                        ? tr('模型接口超时', 'The model request timed out')
                        : tr('模型连接中断', 'The model connection dropped');
                    paneSend(pane, tr(
                        `\n${why}，正在重试（${netTry + 1}/${netTries}），输出上限 ${limit}。\n`,
                        `\n${why}. Retrying (${netTry + 1}/${netTries}), output limit ${limit}.\n`,
                    ));
                    await delay(isChatTimeoutError(e) ? 2500 : (netBackoff[Math.min(netTry - 1, netBackoff.length - 1)] || 1000));
                    continue;
                }
                const hint = isChatRetryable(e)
                    ? tr(
                        '连接已重试仍失败。流式请求 60 秒收不到数据、一次性请求 300 秒收不到数据会自动断开重试（环境变量 STUDIO_LLM_IDLE_S / STUDIO_LLM_BUFFERED_S 可调）。',
                        'Retries failed. Streaming gives up after 60s and buffered requests after 300s without any data (tune with STUDIO_LLM_IDLE_S / STUDIO_LLM_BUFFERED_S).',
                    )
                    : '';
                paneSend(pane, `\n[error] ${e.message || e}${hint ? '\n' + hint : ''}\n`);
                netFail = isChatRetryable(e);
                return { rc: 1, out: lastText, netFail };
            }
        }
        if (!gotText) return { rc: 1, out: lastText };
        if (!canWrite) return { rc: 0, out: text };
        const questions = normalizeQuestions((parseModelPayload(text) || {}).ask);
        if (questions.length) {
            const systemQs = questions.filter(isSystemQuestion);
            const userQs = questions.filter(q => !isSystemQuestion(q));
            if (systemQs.length && !userQs.length && askRounds < 3) {
                askRounds++;
                if (jsonTry === jsonTries) jsonTries = Math.min(jsonTries + 1, 6);
                paneSend(pane, tr(
                    '这是系统内部的问题，按仓库里的规则继续，不再询问。\n',
                    'This is internal to the system. Continuing from the repo rules without asking.\n',
                ));
                history = [
                    { role: 'system', content: chatSystemPrompt(true) },
                    { role: 'user', content: asked },
                    { role: 'user', content: tr(
                        `${systemBuildAnswer()}\n按这个只输出要写入的 JSON。`,
                        `${systemBuildAnswer()}\nOutput only the JSON to write, following this.`,
                    ) },
                ];
                continue;
            }
            if (userQs.length && askRounds < 2) {
                askRounds++;
                const answer = await askUser(pane, {
                    kind: 'choice',
                    title: tr('请先选择', 'Choose first'),
                    hint: tr('下面几项不选定，就无法按你的意图做。', 'These need an answer before the work can follow what you want.'),
                    questions: userQs,
                    note: tr('其他要补充的', 'Anything else'),
                });
                if (!answer || llmCancelled()) return { rc: 1, out: text };
                const chosen = formatPicks(userQs, answer);
                paneSend(pane, tr(`已选择：\n${chosen}\n`, `Chosen:\n${chosen}\n`));
                const systemNote = systemQs.length ? `\n${systemBuildAnswer()}` : '';
                history = [
                    { role: 'system', content: chatSystemPrompt(true) },
                    { role: 'user', content: asked },
                    { role: 'user', content: tr(
                        `用户的选择：\n${chosen}${systemNote}\n按这个选择只输出要写入的 JSON。`,
                        `The user's choices:\n${chosen}${systemNote}\nOutput only the JSON to write, following these choices.`,
                    ) },
                ];
                continue;
            }
        }
        try {
            const written = applyModelReply(text);
            paneSend(pane, tr(`已写入源码：\n${written.join('\n')}\n`, `Wrote source:\n${written.join('\n')}\n`));
            return { rc: 0, out: text };
        } catch (err) {
            if (jsonTry < jsonTries) {
                const cut = lastFinishReason === 'length';
                if (cut) outCap = Math.min(32768, outCap + 8192);
                const missingFile = /没有给出全文|是新文件|empty search|new file/i.test(err.message);
                history = [
                    { role: 'system', content: chatSystemPrompt(true) },
                    { role: 'user', content: asked },
                    { role: 'user', content: tr(
                        cut
                            ? '上一条被截断，已整段丢弃。第一个字符必须是 { 。'
                            : (missingFile
                                ? '上一条用了空的 search，又没有文件全文，已整段丢弃。新文件只输出 {"files":[{"path":"相对路径","content":"完整文件内容"}]}。每个 content 都要是该文件的全文，不要再提交空的 search。'
                                : '上一条不是能解析的 JSON，已整段丢弃。每个 { 和 [ 都要闭合，edits 里每一项都以 } 结束。第一个字符必须是 { 。改已有文件只输出 {"edits":[{"path","search","replace"}]}，search 从已附源码原样复制一小段。新文件用 {"files":[{"path","content"}]}。'),
                        cut
                            ? 'The previous reply was cut off and discarded. The first character must be {.'
                            : (missingFile
                                ? 'The previous reply used an empty search and did not include the file contents. It was discarded. For new files output only {"files":[{"path":"relative path","content":"the entire file"}]}. Every content must be the whole file. Do not send an empty search again.'
                                : 'The previous reply was not valid JSON and was discarded. Close every { and [, and end each edits item with }. The first character must be {. For an existing file output only {"edits":[{"path","search","replace"}]} and copy a short unique search snippet from the attached source. For a new file use {"files":[{"path","content"}]}.'),
                    ) },
                ];
                continue;
            }
            paneSend(pane, tr(`\n[error] ${err.message}。源码未改。\n`, `\n[error] ${err.message}. Source was not changed.\n`));
            return { rc: 1, out: text };
        }
    }
    return { rc: 1, out: lastText };
}

function probeCursorKey() {
    return new Promise(resolve => {
        let child;
        try { child = spawnAgent('', { command: 'models' }); }
        catch (e) { resolve({ ok: false, text: e.message }); return; }
        let out = '';
        const timer = setTimeout(() => {
            try { child.kill(); } catch { /* ignore */ }
            resolve({ ok: false, text: tr('验证 Cursor 密钥超时', 'Cursor key check timed out') });
        }, 20000);
        const take = d => { out += d.toString(); };
        child.stdout.on('data', take);
        child.stderr.on('data', take);
        child.on('error', e => { clearTimeout(timer); resolve({ ok: false, text: e.message }); });
        child.on('close', rc => {
            clearTimeout(timer);
            const key = activeKey(loadLlm());
            const safe = key ? out.split(key).join('') : out;
            if (rc === 0) resolve({ ok: true, text: tr('Cursor 密钥可用', 'Cursor key works') });
            else resolve({ ok: false, text: safe.trim().slice(0, 160) || tr('Cursor 密钥无效', 'Cursor key was rejected') });
        });
    });
}

async function probeKey(cfg) {
    const spec = llmProvider(cfg.provider);
    if (!spec) return { ok: false, text: tr('请选择大模型', 'Choose a model') };
    if (spec.auth === 'login' && spec.wire === 'cursor') return cursorStatus();
    if (spec.auth !== 'key') return { ok: false, text: tr('请填写自己的 API Key', 'Enter your API key') };
    const key = activeKey(cfg);
    if (!key) return { ok: false, text: tr(`请填写 ${providerName(spec)} 的 API Key`, `Enter the API key for ${providerName(spec)}`) };
    if (spec.wire === 'cursor') return probeCursorKey();
    const base = (cfg.baseUrl || spec.base || '').replace(/\/$/, '');
    let url = base + '/models';
    const headers = {};
    if (spec.wire === 'anthropic') {
        url = 'https://api.anthropic.com/v1/models';
        headers['x-api-key'] = key;
        headers['anthropic-version'] = '2023-06-01';
    } else if (spec.wire === 'gemini') {
        url = base + '/models';
        headers['x-goog-api-key'] = key;
    } else if (spec.wire === 'azure') {
        headers['api-key'] = key;
    } else {
        headers.Authorization = `Bearer ${key}`;
    }
    try {
        const r = await fetch(url, { headers, signal: AbortSignal.timeout(15000) });
        if (r.ok) return { ok: true, text: tr(`${providerName(spec)} 密钥可用`, `${providerName(spec)} key works`) };
        const j = await r.json().catch(() => ({}));
        return { ok: false, text: j.error?.message || j.message || tr(`${providerName(spec)} 验证失败 HTTP ${r.status}`, `${providerName(spec)} check failed HTTP ${r.status}`) };
    } catch (e) {
        return { ok: false, text: e.message || String(e) };
    }
}

function modelInUse(cfg, spec) {
    const id = cfg.model || spec.models?.[0] || '';
    const label = (spec.modelLabels && spec.modelLabels[id]) || id || tr('未选模型', 'No model');
    return `${providerName(spec) || cfg.provider} · ${label}`;
}

function isSystemQuestion(q) {
    const s = `${q && q.prompt || ''}\n${(q && q.options || []).join('\n')}`;
    return /makefile|kconfig|cmake|cmakelists|模块开关|构建文件|编译规则|接线|没有被附|源码里没有|APP_TABLET_|config\.mk|粘贴.{0,24}(makefile|构建|文件|内容)/i.test(s);
}

function isPlatformQuestion(q) {
    const s = `${q && q.prompt || ''}\n${(q && q.options || []).join('\n')}`;
    return /退出|关闭应用|返回桌面|自动结束|\bexit\b|\bquit\b|close the app|命令行|图形界面|触摸|按钮或菜单|交互方式|操作方式|command[- ]line|\bGUI\b|touch|中文|乱码|字体|显示语言|界面语言|chinese|garbled|font|language/i.test(s);
}

function platformAnswer() {
    return tr(
        '平台固定行为，不需要再问：退出和其他应用一样，用底部上滑退出条（tablet_create_top），不加退出按钮，退出时不弹提示，不会自动退出。交互是设备触摸屏上的图形界面，用按钮、列表、滑块等控件操作。文字按设备语言显示中文或英文，所有文字用 tablet_font_get_freetype_font() 的字体，保证中文正常显示。',
        'Fixed platform behavior, do not ask: exit works like the other apps, through the swipe-up home bar at the bottom (tablet_create_top). No exit button, no prompt on exit, no automatic exit. Interaction is a touch-screen GUI using buttons, lists, sliders, and similar widgets. Text follows the device language in Chinese or English, and every label uses the tablet_font_get_freetype_font() font so Chinese shows correctly.',
    );
}

function systemBuildAnswer() {
    return tr(
        '系统内部已经确定：新应用只写 application/packages/tablet/<id>/tablet_<id>.c，里面定义 lv_result_t tablet_<id>_create(lv_obj_t *main)。app.json、app_entry_<id>.c、tablet_<id>.h 由工具写入，不要输出 base64。不要改 Makefile，也不要让用户粘贴它。Makefile 会按应用目录自动接线，工具只在 project/anypi_nova/config.mk 打开 CONFIG_APP_TABLET_<ID> 开关。需要额外编译选项时由工具写进应用目录的 app.mk。入口符号是 app_entry_start_<id>，动态库是 lib/lib<id>_app.so。',
        'Already decided inside the system: write only application/packages/tablet/<id>/tablet_<id>.c, defining lv_result_t tablet_<id>_create(lv_obj_t *main). The tool writes app.json, app_entry_<id>.c, and tablet_<id>.h. Do not output base64. Do not edit the Makefile and do not ask the user to paste it. The Makefile discovers apps from their directories automatically; the tool only turns CONFIG_APP_TABLET_<ID> on in project/anypi_nova/config.mk. Extra build options go into the app app.mk file, written by the tool. The entry symbol is app_entry_start_<id> and the library is lib/lib<id>_app.so.',
    );
}

function isMakefileDefect(text) {
    return /cannot specify .{0,8}-o.{0,40}multiple files|missing separator/i.test(String(text || ''));
}

function repairTabletMakefile() {
    const mkPath = join(repoDir(), 'application', 'packages', 'tablet', 'Makefile');
    if (!existsSync(mkPath)) return false;
    const text = readHostText(mkPath);
    const eol = text.includes('\r\n') ? '\r\n' : '\n';
    const next = text.replace(/(-o \$@)(\$\()/g, `$1${eol}$2`);
    if (next === text) return false;
    writeHostText(mkPath, next);
    return true;
}

function runAgent(pane, prompt, opts = {}) {
    const cfg = loadLlm();
    const spec = llmProvider(cfg.provider) || llmProvider('cursor');
    paneSend(pane, tr(`\n正在使用 ${modelInUse(cfg, spec)}\n`, `\nUsing ${modelInUse(cfg, spec)}\n`));
    if (spec.wire !== 'cursor') return runChat(pane, prompt, cfg, spec, opts);
    return new Promise(resolve => {
        let child;
        try { child = spawnAgent(prompt, opts); }
        catch (e) { paneSend(pane, `\n[error] ${e.message}\n`); resolve({ rc: 1, out: '' }); return; }
        if (llmJob) llmJob.track(child);
        let out = '';
        const take = d => { const t = d.toString(); out += t; paneSend(pane, t); };
        child.stdout.on('data', take);
        child.stderr.on('data', take);
        child.on('error', e => { paneSend(pane, `\n[error] ${e.message}\n`); resolve({ rc: 1, out }); });
        child.on('close', rc => resolve({ rc: rc ?? 1, out }));
    });
}

function slugAscii(s) {
    return String(s || '').trim().toLowerCase()
        .replace(/[\s-]+/g, '_')
        .replace(/[^a-z0-9_]/g, '')
        .replace(/^_+/, '')
        .slice(0, 40);
}

function tidyEn(s) {
    return String(s || '').trim().replace(/\s+/g, ' ')
        .replace(/\b[a-z]/g, c => c.toUpperCase());
}

function knownAppPair(raw) {
    const s = String(raw || '').trim();
    const lower = s.toLowerCase();
    return knownAppPairs().find(p => p.zh === s || p.en.toLowerCase() === lower || p.id === lower) || null;
}

function splitBilingualName(raw) {
    const m = String(raw || '').trim().match(/^(.+?)\(([^()]+)\)\s*$/);
    if (!m) return null;
    const a = m[1].trim();
    const b = m[2].trim();
    if (hasHan(a) && !hasHan(b)) return { zh: a, en: tidyEn(b) };
    if (!hasHan(a) && hasHan(b)) return { zh: b, en: tidyEn(a) };
    return null;
}

function parseAppPair(out) {
    const text = String(out || '');
    const field = key => {
        const m = text.match(new RegExp(`(?:^|\\n)\\s*(?:\\*\\*)?${key}(?:\\*\\*)?\\s*[:：]\\s*([^\\n]+)`, 'i'));
        return m ? m[1].replace(/[`*]/g, '').trim() : '';
    };
    const zh = field('zh');
    const en = tidyEn(field('en'));
    if (!hasHan(zh) || hasHan(en) || !en) return null;
    let id = slugAscii(field('id')).replace(/_+/g, '_').replace(/_$/, '');
    if (!/^[a-z][a-z0-9_]{0,40}$/.test(id)) id = slugAscii(en).replace(/_+/g, '_').replace(/_$/, '');
    if (!/^[a-z][a-z0-9_]{0,40}$/.test(id)) return null;
    return { id, zh, en };
}

function translateAppPair(raw, pane) {
    const prompt = tr(
        `把应用名补成中文和英文两种，并给出目录 id。用户写的是「${raw}」。写了中文就把中文原样用作 zh，译出英文；写了英文就把英文用作 en，译出中文。只输出三行，不要解释：\nid: 小写英文目录名，只用小写字母、数字和下划线，例如 music_player\nzh: 中文显示名\nen: English Display Name`,
        `Fill in both the Chinese and English app names, plus a directory id. The user wrote "${raw}". If it is Chinese, keep it as zh and translate the English. If it is English, keep it as en and translate the Chinese. Output three lines only, with no explanation:\nid: lowercase directory id using only lowercase letters, digits, and underscores, for example music_player\nzh: Chinese display name\nen: English Display Name`,
    );
    return runAgent(pane, prompt, { write: false }).then(({ out }) => parseAppPair(out));
}

async function resolveAppPair(raw, pane) {
    const split = splitBilingualName(raw);
    if (split) {
        const known = knownAppPair(split.zh) || knownAppPair(split.en);
        const id = known?.id || slugAscii(split.en);
        if (id) return { id, zh: split.zh, en: known?.en || split.en };
    }
    const known = knownAppPair(raw);
    if (known) return known;
    const slug = slugAscii(raw);
    const typedId = !!slug && slug === String(raw || '').trim().toLowerCase().replace(/[\s-]+/g, '_');
    paneSend(pane, tr(
        `名称「${raw}」正在补成另一种语言…\n`,
        `Name "${raw}" is being filled in for the other language…\n`,
    ));
    const pair = await translateAppPair(raw, pane);
    if (!pair) return null;
    if (typedId) pair.id = slug;
    if (hasHan(raw) && !String(raw).includes('(')) pair.zh = String(raw).trim();
    else if (!hasHan(raw) && /[A-Za-z]/.test(raw)) pair.en = tidyEn(raw);
    return pair;
}

function suggestAppPair(raw) {
    const split = splitBilingualName(raw);
    if (split) {
        const known = knownAppPair(split.zh) || knownAppPair(split.en);
        return { id: known?.id || slugAscii(split.en), zh: split.zh, en: known?.en || split.en };
    }
    const known = knownAppPair(raw);
    if (known) return { id: known.id, zh: known.zh, en: known.en };
    const text = String(raw || '').trim();
    if (hasHan(text)) return { id: '', zh: text, en: '' };
    return { id: slugAscii(text), zh: '', en: tidyEn(text) };
}

function pairFromFields(fields) {
    const id = slugAscii(fields && fields.id);
    const zh = String(fields && fields.zh || '').trim();
    const en = tidyEn(fields && fields.en);
    if (!/^[a-z][a-z0-9_]{0,40}$/.test(id) || !id) return null;
    if (!hasHan(zh) || hasHan(en) || !en) return null;
    return { id, zh, en };
}

async function confirmAppName(raw, pane) {
    let suggestion = suggestAppPair(raw);
    if (!suggestion.zh || !suggestion.en || !suggestion.id) {
        paneSend(pane, tr(
            `正在为「${raw}」翻译${suggestion.zh ? '英文名' : '中文名'}并建议目录 id…\n`,
            `Translating "${raw}" and suggesting a directory id…\n`,
        ));
        let pair = null;
        try { pair = await translateAppPair(raw, pane); } catch { pair = null; }
        if (llmCancelled()) return null;
        if (pair) {
            suggestion = {
                zh: suggestion.zh || pair.zh,
                en: suggestion.en || pair.en,
                id: suggestion.id || pair.id,
            };
        } else {
            if (!suggestion.id && suggestion.en) suggestion.id = slugAscii(suggestion.en);
            paneSend(pane, tr('没有翻译成功，请在下面补上。\n', 'Translation did not finish. Fill it in below.\n'));
        }
    }
    paneSend(pane, tr(
        '请确认应用的中文名、英文名和目录 id，确认后才继续。\n',
        'Confirm the Chinese name, English name, and directory id before this continues.\n',
    ));
    for (;;) {
        if (llmCancelled()) return null;
        const answer = await askUser(pane, {
            kind: 'name',
            title: tr('确认应用名称', 'Confirm the app name'),
            hint: tr('已填好翻译的英文名和建议的目录 id，可以直接改。目录 id 用小写英文。设备会按语言显示中文名或英文名。', 'The translated name and suggested directory id are filled in and can be edited. Use a lowercase English directory id. The device shows the Chinese or English name for its language.'),
            fields: [
                { key: 'zh', label: tr('中文名', 'Chinese name'), value: suggestion.zh || '' },
                { key: 'en', label: tr('英文名', 'English name'), value: suggestion.en || '' },
                { key: 'id', label: tr('目录 id', 'Directory id'), value: suggestion.id || '' },
            ],
        });
        if (!answer || llmCancelled()) return null;
        const pair = pairFromFields(answer.fields);
        if (pair) return pair;
        suggestion = {
            id: String(answer.fields && answer.fields.id || ''),
            zh: String(answer.fields && answer.fields.zh || ''),
            en: String(answer.fields && answer.fields.en || ''),
        };
    }
}

async function clarifyRequest(pane, requestText) {
    paneSend(pane, tr(
        '先看需求里有没有必须由你决定的地方。\n',
        'Checking whether you need to decide anything before this continues.\n',
    ));
    const prompt = tr(
        `阅读下面的需求。实现细节你自己决定，不要为此提问。不要问 Makefile、模块开关、构建文件或源码路径，那些由工具处理。下面这些是平台固定行为，也不要问：${platformAnswer()}只有产品行为有两种以上合理做法、不确认就会做错时才提问。最多三个问题，每个问题给出两到四个选项。只输出 JSON。清楚就输出 {"clear":true}。不清楚就输出 {"clear":false,"questions":[{"prompt":"问句","options":["甲","乙"]}]}。\n需求：\n${requestText}`,
        `Read the request. Decide implementation details yourself and do not ask about them. Do not ask about the Makefile, module switch, build files, or source paths; the tool handles those. These are fixed platform behavior, so do not ask about them either: ${platformAnswer()} Ask only when a product behavior has more than one reasonable choice and guessing would do the wrong thing. At most three questions, each with two to four options. Output JSON only. If it is clear, output {"clear":true}. If not, output {"clear":false,"questions":[{"prompt":"question","options":["A","B"]}]}.\nRequest:\n${requestText}`,
    );
    const { rc, out } = await runAgent(pane, prompt, { write: false });
    if (llmCancelled()) return null;
    const parsed = rc === 0 ? parseClarify(out) : { failed: true };
    const fixed = platformAnswer();
    if (parsed.clear) {
        paneSend(pane, tr('需求清楚，继续。\n', 'The request is clear. Continuing.\n'));
        return fixed;
    }
    if (parsed.questions && parsed.questions.length) {
        const userQs = parsed.questions.filter(q => !isSystemQuestion(q) && !isPlatformQuestion(q));
        const dropped = parsed.questions.length - userQs.length;
        if (dropped)
            paneSend(pane, tr(
                `有 ${dropped} 个问题属于构建规则或平台固定行为（底部上滑退出、触摸操作、中文字体），不再问你。\n`,
                `${dropped} question(s) concern build rules or fixed platform behavior (swipe-up exit, touch input, Chinese font) and will not be asked.\n`,
            ));
        if (!userQs.length) {
            paneSend(pane, tr('已按仓库规则和平台约定继续。\n', 'Continuing from the repo rules and platform conventions.\n'));
            return `${systemBuildAnswer()}\n${fixed}`;
        }
        const answer = await askUser(pane, {
            kind: 'choice',
            title: tr('请先选择', 'Choose first'),
            hint: tr('下面几项不选定，就无法按你的意图做。', 'These need an answer before the work can follow what you want.'),
            questions: userQs,
            note: tr('其他要补充的', 'Anything else'),
        });
        if (!answer || llmCancelled()) return null;
        const extra = formatPicks(userQs, answer);
        if (extra) paneSend(pane, tr(`已确认：\n${extra}\n`, `Confirmed:\n${extra}\n`));
        return extra ? `${extra}\n${fixed}` : fixed;
    }
    const answer = await askUser(pane, {
        kind: 'note',
        title: tr('请确认需求', 'Confirm the request'),
        hint: tr('自动判断没有完成。如果有必须由你定的地方，写在下面。没有就直接继续。', 'The check did not finish. Write anything you still need to decide, or continue.'),
        note: tr('补充', 'Note'),
    });
    if (!answer || llmCancelled()) return null;
    const note = String(answer.note || '').trim();
    if (note) paneSend(pane, tr(`已补充：${note}\n`, `Added: ${note}\n`));
    return note ? `${note}\n${fixed}` : fixed;
}

async function runLlm(body) {
    const fixing = body.mode === 'fix';
    const fromNew = body.mode === 'new';
    const continuing = fromNew && body.resume === true;
    let creating = fromNew;
    const mode = fixing ? tr('修复编译错误', 'Fix a compile error') : (creating ? tr('新建 tablet APP', 'Create a tablet app') : tr('修改已有代码', 'Edit existing code'));
    const textIn = String(body.text || '').slice(0, 4000);
    const log = String(body.log || '').slice(-6000);
    const pane = body.pane === 'wsl' ? 'wsl' : 'llm';
    const job = startLlmJob(pane);
    try {
    const stale = studioStaleFiles();
    if (stale.length) {
        paneSend(pane, tr(
            `\n[警告] Studio 启动后 ${stale.join('、')} 已更新，这次运行用的还是旧代码。请关掉 Studio 重新启动，再刷新页面。\n\n`,
            `\n[warning] ${stale.join(', ')} changed after Studio started, so this run uses old code. Stop Studio, start it again, and refresh the page.\n\n`,
        ));
    }
    const rawName = String(body.target || '').trim().slice(0, 40);
    let target = slugAscii(rawName);
    let displayName = rawName;
    let nameZh = '';
    let nameEn = '';
    if (continuing) {
        target = slugAscii(rawName) || appIdOf(rawName);
        const saved = appRunOf(target);
        const fromJson = namesFromAppJson(target);
        nameZh = (saved && saved.zh) || fromJson.zh;
        nameEn = (saved && saved.en) || fromJson.en;
        displayName = (nameZh && nameEn) ? `${nameZh}(${nameEn})` : (fromJson.name || target);
        paneSend(pane, tr(
            `继续进行中的应用：${displayName}，目录 id：${target}。不再确认名称，也不再询问是否删除后重来。\n`,
            `Continuing the in-progress app: ${displayName}, directory id: ${target}. The name is not confirmed again, and it will not ask whether to delete and start over.\n`,
        ));
    } else if (creating && rawName) {
        const pair = await confirmAppName(rawName, pane);
        if (!pair) {
            if (!llmCancelled())
                paneSend(pane, tr('未确认名称，已停止。\n', 'The name was not confirmed. Stopped.\n'));
            return;
        }
        target = pair.id;
        nameZh = pair.zh;
        nameEn = pair.en;
        displayName = `${nameZh}(${nameEn})`;
        paneSend(pane, tr(
            `名称：${displayName}，目录 id：${target}。设备为中文时显示「${nameZh}」，为英文时显示「${nameEn}」。\n`,
            `Name: ${displayName}, directory id: ${target}. The device shows "${nameZh}" in Chinese and "${nameEn}" in English.\n`,
        ));
    } else if (!creating) {
        target = rawName.replace(/[^\w./-]/g, '').slice(0, 80);
    }
    if (creating && !target) return;
    let resume = false;
    if (creating && !continuing) {
        const prior = tabletGenerateState(target);
        if (prior === 'builtin') {
            paneSend(pane, tr(
                `${target} 是系统自带应用。要改它请用「修改APP」，不能在新增里删掉重来。\n`,
                `${target} is a built-in app. Edit it with Edit app. New app will not delete it and start over.\n`,
            ));
            return;
        }
        if (prior === 'present') {
            const keep = tr('采用之前生成继续', 'Continue with what was already generated');
            const drop = tr('删除之前流程重新生成', 'Delete the previous run and generate again');
            const answer = await askUser(pane, {
                kind: 'choice',
                title: tr('这个应用已经有上次的生成', 'This app already has a previous generation'),
                hint: tr(
                    `${displayName}（${target}）上次没有做完或已经生成过。继续会在已有文件上按这次要求改。删除会清掉这次应用的源码和安装痕迹，再按这次要求重新生成。`,
                    `${displayName} (${target}) was left unfinished or already generated. Continue edits those files with this request. Delete removes that app's source and install traces, then generates it again from this request.`,
                ),
                questions: [{ prompt: tr('怎么处理上次的生成？', 'What should happen to the previous generation?'), options: [keep, drop] }],
            });
            if (!answer || llmCancelled()) {
                if (!llmCancelled())
                    paneSend(pane, tr('未选择，已停止。\n', 'No choice was made. Stopped.\n'));
                return;
            }
            if (answer.picks[0] === drop) {
                paneSend(pane, tr('正在删除上次的生成，随后重新生成。\n', 'Deleting the previous generation, then generating again.\n'));
                await discardGeneratedApp(target, text => paneSend(pane, text.endsWith('\n') ? text : text + '\n'));
            } else {
                resume = true;
                creating = false;
                paneSend(pane, tr(
                    '采用之前的生成继续。这次要求会改已有文件，缺的文件再补。\n',
                    'Continuing from the previous generation. This request edits the files already there and adds any that are missing.\n',
                ));
            }
        }
    } else if (continuing) {
        if (tabletGenerateState(target) === 'builtin') {
            paneSend(pane, tr(
                `${target} 是系统自带应用。要改它请用「修改APP」，不能在新增里删掉重来。\n`,
                `${target} is a built-in app. Edit it with Edit app. New app will not delete it and start over.\n`,
            ));
            return;
        }
        if (tabletGenerateState(target) === 'present') {
            resume = true;
            creating = false;
        }
    }
    if (fromNew && target && tabletGenerateState(target) !== 'builtin')
        markAppProgress(target, { zh: nameZh, en: nameEn });
    let text = textIn;
    if (!fixing && !continuing) {
        const clarified = await clarifyRequest(pane, textIn);
        if (clarified == null) {
            if (!llmCancelled())
                paneSend(pane, tr('未回答问题，已停止。\n', 'The question was not answered. Stopped.\n'));
            return;
        }
        if (clarified) text = `${textIn}\n${clarified}`;
    }
    const promptCore = (fixing
        ? [
            tr('你在当前源码树里直接改文件，修掉这次编译错误。', 'Edit files in this source tree and fix this compile error.'),
            target ? tr(`模块：${target}`, `Module: ${target}`) : '',
            log ? tr(`编译输出：\n${log}`, `Build output:\n${log}`) : '',
            text ? tr(`补充说明：${text}`, `Note: ${text}`) : '',
            tr('根据报错定位并修改。只改完成修复所需的文件。', 'Change only the files needed for the fix.'),
        ]
        : [
            tr('你在当前源码树里直接改文件。', 'Edit files in this source tree.'),
            tr(`模式：${resume ? tr('继续上次生成', 'Continue the previous generation') : mode}`, `Mode: ${resume ? tr('继续上次生成', 'Continue the previous generation') : mode}`),
            (creating || resume) ? tr(`显示名：${displayName || target}`, `Display name: ${displayName || target}`) : '',
            target ? tr(`目录 id：${target}`, `Directory id: ${target}`) : '',
            resume ? tr(
                `继续未完成的应用 application/packages/tablet/${target}/。目录里已有文件，按这次要求修改，缺的文件补上。不要另建目录，不要改 app_id。app.json 的 name 保持「${displayName}」。中文显示「${nameZh}」，英文显示「${nameEn}」。`,
                `Continue the unfinished app at application/packages/tablet/${target}/. Files are already there: edit them for this request and add any file that is missing. Do not create another directory and do not change app_id. Keep app.json name as "${displayName}". Chinese shows "${nameZh}" and English shows "${nameEn}".`,
            ) : creating ? tr(
                `新建 APP 必须放在 application/packages/tablet/${target}/。app.json 的 app_id 用 ${target}，name 必须是「${displayName}」。设备按当前语言显示对应的名字：中文显示「${nameZh}」，英文显示「${nameEn}」。不要改成别的译法。只写这个目录里的 tablet_${target}.c，功能多可以再加根目录的 .c/.h。app.json、app_entry_${target}.c、tablet_${target}.h 由工具写，不要输出它们，也不要输出 base64 或任何图片。不要改 Makefile，不要问模块开关接在哪，也不要让用户粘贴构建文件。Makefile 按应用目录自动接线，工具只打开 CONFIG_APP_TABLET_${target.toUpperCase()} 开关。`,
                `Create the app at application/packages/tablet/${target}/. Set app.json app_id to ${target}, name to "${displayName}". The device shows the name for its current language: "${nameZh}" in Chinese and "${nameEn}" in English. Do not use a different translation. Write only tablet_${target}.c in that directory; add more root .c/.h files if the feature needs them. The tool writes app.json, app_entry_${target}.c, and tablet_${target}.h, so do not output them, and do not output base64 or any image. Do not edit the Makefile, do not ask where the module switch goes, and do not ask the user to paste a build file. After the files are written, the tool hooks APP_TABLET_${target.toUpperCase()} into application/packages/tablet/Makefile.`,
            ) : (target ? tr(
                `只修改已有应用 ${target}。在这个目录里改原有文件。不要新建应用目录，不要复制成另一个 app_id，不要改 app.json 的 app_id，不要新增应用条目。`,
                `Only edit the existing app ${target}. Change files already in that directory. Do not create another app directory, do not copy it to a new app_id, do not change app.json app_id, and do not add another app entry.`,
            ) : ''),
            (creating || resume) ? tr(
                '界面上出现的每一句文字都要同时有中文和英文，不只有应用名。按钮、标题、提示、空状态和报错都要这样写。get_lan_index() 为 1 显示中文，为 0 显示英文，不要对调。写法照 application/packages/tablet/calculator/tablet_calculator.c 的 i18n_text(英文, 中文)。已经在桌面语言表里的句子可以用 lan()。',
                'Every string the user can see needs both Chinese and English, not only the app name. That includes buttons, titles, hints, empty states, and errors. get_lan_index() is 1 for Chinese and 0 for English; do not swap them. Follow i18n_text(english, chinese) in application/packages/tablet/calculator/tablet_calculator.c. lan() is fine for sentences already in the launcher language table.',
            ) : '',
            (creating || resume) ? tr(
                '桌面图标由启动器按 app.json 的 icon_kind/icon_glyph/icon_color/icon_color2 绘制，这些字段由工具按应用内容写入，不要改。不要生成 PNG，不要写 base64。',
                'The launcher draws the home icon from icon_kind/icon_glyph/icon_color/icon_color2 in app.json. The tool writes those fields from the app content, so do not change them. Do not generate a PNG and do not write base64.',
            ) : '',
            target ? sourceRulesBlock(appIdOf(target) || target) : '',
            target && contractApplies(appIdOf(target), (creating || resume) ? 'new' : '')
                ? appContractBlock(appIdOf(target)) : '',
            target && appIdOf(target) ? apiRefsBlock('', target, text) : '',
            tr(`要求：${text}`, `Request: ${text}`),
            tr(
                '图标下的名字，以及本工具应用列表里的名字，都只来自 app.json 的 name。它必须是字符串「中文(English)」，例如 日历(Calendar)、计算器(Calculator)。英文显示括号里，中文显示括号前。name 若是对象，列表会显示 [object Object]。get_lan_index() 为 0 是英文、为 1 是中文，不要对调。index.js 不是设备图标上的名字。',
                'The name under the icon, and the name in this tool\'s app list, both come only from app.json name. It must be the string "中文(English)", for example 日历(Calendar) or 计算器(Calculator). English shows the text inside the parentheses, Chinese the text before them. An object name shows up as [object Object]. get_lan_index() is 0 for English and 1 for Chinese; do not swap that. index.js is not the name under the device icon.',
            ),
            resume
                ? tr('已有文件用 edits，search 必须能唯一匹配。还没有的文件用 {"files":[{"path","content"}]} 写全文。', 'Use edits for files that already exist, and search must match once. Use {"files":[{"path","content"}]} for a file that is not there yet.')
                : creating
                ? tr(`新文件用 {"files":[{"path","content"}]} 整文件写入。只输出 tablet_${target}.c（和你另加的 .c/.h），不要输出 app.json、app_entry_${target}.c、tablet_${target}.h。`, `Write new files as {"files":[{"path","content"}]}. Output only tablet_${target}.c (and any .c/.h you add). Do not output app.json, app_entry_${target}.c, tablet_${target}.h.`)
                : tr('只改完成描述所需的文件。界面文字按这条要求写，中文或英文都可以。', 'Change only the files needed for this request. UI text follows this request and may be Chinese or English.'),
        ]).filter(Boolean).join('\n');
    let prompt = [repoContextBlock(), appFileListBlock(target), promptCore].filter(Boolean).join('\n\n');
    paneSend(pane, tr(
        `仓库根：${repoDir().replace(/\\/g, '/')}\n工具目录：devstudio/web/\n`,
        `Repo root: ${repoDir().replace(/\\/g, '/')}\nstudio: devstudio/web/\n`,
    ));
    const srcBlock = !fixing && target ? appSourcesBlock(target) : '';
    if (srcBlock) {
        prompt += '\n\n' + srcBlock;
        paneSend(pane, tr(
            `已把 ${appIdOf(target) || target} 的源码附在请求里。模型须直接输出 json 写文件。\n`,
            `Bundled source for ${appIdOf(target) || target}. The model must output json to write files.\n`,
        ));
    } else if (!fixing && target) {
        paneSend(pane, tr(
            `application/packages/tablet/${appIdOf(target) || target}/ 还没有源码。新应用直接写文件，构建接线由工具完成。\n`,
            `No source yet under application/packages/tablet/${appIdOf(target) || target}/. Write the new app files. The tool wires the build.\n`,
        ));
    }
    if (fixing) {
        const diag = classifyBuildLog(log, target);
        const where = compileFixWhere(diag);
        paneSend(pane, tr('\n# 大模型修编译\n', '\n# Fix the build\n'));
        if (where) paneSend(pane, tr(`定位：${where}\n`, `Location: ${where}\n`));
        prompt = [prompt, compileFixBrief(diag, target), appSourcesBlock(target)].filter(Boolean).join('\n\n');
        if (appIdOf(target)) {
            paneSend(pane, tr(
                `已把 ${appIdOf(target)} 的源码附在修复请求里。\n`,
                `Bundled source for ${appIdOf(target)} into the fix request.\n`,
            ));
        }
        const fixed = await runAgent(pane, prompt, { write: true });
        if (fixed.rc !== 0) {
            if (!llmCancelled())
                paneSend(pane, tr('状态：大模型未完成，未推送到设备\n', 'Status: the model did not finish, so nothing was pushed\n'));
            return;
        }
        await pushChangedModule(pane, moduleForPush(target));
        return;
    }
    await deliverApp(pane, {
        prompt, text, target, moduleName: moduleForPush(target), creating,
        appName: (creating || resume) ? displayName : '',
    });
    } catch (e) {
        if (!llmCancelled())
            paneSend(pane, tr(`\n[error] ${e.message || e}\n`, `\n[error] ${e.message || e}\n`));
    } finally {
        finishLlmJob(job);
    }
}

function appIdOf(target) {
    const t = String(target || '').trim();
    const fromPath = t.match(/tablet\/([A-Za-z0-9_]+)/);
    if (fromPath) return fromPath[1];
    if (/^APP_TABLET_[A-Z0-9_]+$/.test(t)) return t.slice('APP_TABLET_'.length).toLowerCase();
    if (t.includes('/')) return '';
    return slugAscii(t);
}

function moduleForPush(target) {
    const t = String(target || '').trim();
    if (/^APP_[A-Z0-9_]+$/.test(t)) return t;
    if (product.agentSrc && t.includes(product.agentSrc)) return product.agentModule;
    if (/\/studio\//.test(t)) return product.agentModule;
    const id = appIdOf(t);
    if (!id) return '';
    return 'APP_TABLET_' + id.toUpperCase();
}

function buildLine(pane, bucket) {
    return t => {
        const s = String(t).replace(/\r/g, '').replace(/\0/g, '')
            .split('\n').filter(l => !/^wsl:.*(localhost|代理|proxy|\uFFFD)/i.test(l)).join('\n');
        if (!s.trim()) return;
        bucket.text += s;
        paneSend(pane, s.endsWith('\n') ? s : s + '\n');
    };
}

function sourceRulesBlock(id) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id)) return '';
    const productId = product.id || WSL_PRODUCT;
    const entry = BUILTIN_APPS.has(id)
        ? tr(
            `入口是 app_entry_start_${id}(lv_obj_t *main)，在 app_entry_${id}.c，保持原样。`,
            `The entry is app_entry_start_${id}(lv_obj_t *main) in app_entry_${id}.c. Keep it as it is.`,
        )
        : tr(
            `app_entry_${id}.c 和 tablet_${id}.h 由工具按模板生成，不要写也不要改。tablet_${id}.c 必须定义 lv_result_t tablet_${id}_create(lv_obj_t *main)。`,
            `The tool generates app_entry_${id}.c and tablet_${id}.h from a template. Do not write or edit them. tablet_${id}.c must define lv_result_t tablet_${id}_create(lv_obj_t *main).`,
        );
    return tr(
        `编译约束：只改 application/packages/tablet/${id}/。每个 .c 放在这个目录根上，子目录里的 .c 不会被编译。${entry}不要改 Makefile 和 project/${productId}/config.mk，工具会打开 CONFIG_APP_TABLET_${id.toUpperCase()}，Makefile 按应用目录自动接线并安装。不要 #include hardware、camera、display、gpio、wifi 这类 HAL 头。不要用 lv_canvas、lv_imagebutton、lv_animimg、lv_gif 等主程序未导出的 LVGL 控件（用了 dlopen 会失败，桌面弹 app entry not found）；二维码/自绘图形用 lv_image 显示内存位图（XRGB8888），参照 share/tablet_share.c 的 qr_render_image()。`,
        `Build rules: change only application/packages/tablet/${id}/. Put every .c in that directory's root; a .c in a subdirectory is not compiled. ${entry} Do not edit the Makefile or project/${productId}/config.mk. The tool turns on CONFIG_APP_TABLET_${id.toUpperCase()} and hooks the install rule. Do not include HAL headers such as hardware, camera, display, gpio, or wifi. Do not use LVGL widgets the main program does not export (lv_canvas, lv_imagebutton, lv_animimg, lv_gif, ...): they fail dlopen and the launcher shows "app entry not found". For a QR code or custom drawing, show a memory bitmap (XRGB8888) through lv_image — see qr_render_image() in share/tablet_share.c.`,
    );
}

function appEntrySource(id) {
    return `#define LOG_TAG "TABLET_APP_ENTRY_${id.toUpperCase()}"

#include "tablet_${id}.h"

#define APP_ENTRY_EXPORT __attribute__((visibility("default"))) __attribute__((used))

APP_ENTRY_EXPORT int app_entry_start_${id}(lv_obj_t *main)
{
    if (!main)
        return -1;
    if (tablet_${id}_create(main) != LV_RESULT_OK)
        return -1;
    lv_obj_add_flag(main, LV_OBJ_FLAG_HIDDEN);
    return 0;
}

static int (*const s_keep_app_entry_${id})(lv_obj_t *) = app_entry_start_${id};
`;
}

function appHeaderSource(id) {
    const guard = `APPLICATION_TABLET_${id.toUpperCase()}_H`;
    return `#ifndef ${guard}
#define ${guard}

#ifdef __cplusplus
extern "C" {
#endif

#include "lvgl.h"

lv_result_t tablet_${id}_create(lv_obj_t *main);

#ifdef __cplusplus
}
#endif

#endif /* ${guard} */
`;
}

function ensureAppScaffold(id) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id) || BUILTIN_APPS.has(id)) return [];
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    if (!existsSync(dir)) return [];
    const written = [];
    const entry = join(dir, `app_entry_${id}.c`);
    const entryText = existsSync(entry) ? readFileSync(entry, 'utf8') : '';
    if (!new RegExp(`app_entry_start_${id}\\s*\\(\\s*lv_obj_t\\s*\\*`).test(entryText)
        || !new RegExp(`tablet_${id}_create\\s*\\(`).test(entryText)) {
        writeFileSync(entry, appEntrySource(id));
        written.push(`app_entry_${id}.c`);
    }
    const header = join(dir, `tablet_${id}.h`);
    const headerText = existsSync(header) ? readFileSync(header, 'utf8') : '';
    if (!new RegExp(`tablet_${id}_create\\s*\\(\\s*lv_obj_t\\s*\\*`).test(headerText)) {
        writeFileSync(header, appHeaderSource(id));
        written.push(`tablet_${id}.h`);
    }
    return written;
}

function appContractBlock(id) {
    if (!id || BUILTIN_APPS.has(id)) return '';
    const skeleton = `#include "tablet_${id}.h"
#include <string.h>
#include "tablet_common.h"
#include "tablet_font.h"

typedef struct {
    lv_obj_t *main;
    lv_obj_t *window;
    lv_font_t *font;
} context_t;
static context_t context;

static const char *i18n_text(const char *en, const char *zh)
{
    return (get_lan_index() == 1) ? zh : en;
}

static lv_obj_t *make_label(lv_obj_t *parent, const char *text)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, context.font, 0);
    lv_obj_set_style_text_color(l, lv_color_hex(0xffffff), 0);
    lv_label_set_text(l, text);
    return l;
}

static void event_handler_btn_back(lv_event_t *e)
{
    lv_obj_t *obj = lv_event_get_user_data(e);
    /* stop timers, threads and playback here */
    if (obj) {
        lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(obj);
    }
    if (context.window)
        lv_obj_delete(context.window);
    memset(&context, 0, sizeof(context));
}

lv_result_t tablet_${id}_create(lv_obj_t *main)
{
    lv_obj_t *body;

    if (context.window)
        return LV_RESULT_INVALID;
    memset(&context, 0, sizeof(context));
    context.main = main;
    context.font = tablet_font_get_freetype_font(22);
    if (!context.font)
        context.font = tablet_font_get_montserrat_font(24);

    context.window = lv_obj_create(lv_display_get_screen_active(lv_display_get_default()));
    lv_obj_remove_style_all(context.window);
    lv_obj_set_size(context.window, LV_HOR_RES, LV_VER_RES);
    lv_obj_set_style_bg_color(context.window, lv_color_hex(0x1c1c1e), 0);
    lv_obj_set_style_bg_opa(context.window, LV_OPA_COVER, 0);
    tablet_create_top(context.window, i18n_text("Title", "标题"), event_handler_btn_back, main);

    body = lv_obj_create(context.window);
    lv_obj_remove_style_all(body);
    lv_obj_set_size(body, LV_PCT(100), LV_PCT(90));
    lv_obj_align(body, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_pad_all(body, 12, 0);
    lv_obj_set_style_pad_bottom(body, 12 + TABLET_HOME_BAR_H, 0);
    /* build the UI inside body; every label goes through make_label() */
    return LV_RESULT_OK;
}`;
    return tr(
        `【应用接入约定】启动器调用 app_entry_start_${id}(lv_obj_t *main)，再由它调用 tablet_${id}_create(main)。tablet_${id}.c 按下面的骨架写：\n` +
        `- 界面建在 lv_display_get_screen_active() 上的 window 里，不要 lv_obj_create(NULL)，不要 lv_scr_load / lv_screen_load。\n` +
        `- 必须调用 tablet_create_top(window, 标题, event_handler_btn_back, main)。它画标题和底部上滑退出条，退出时调用 event_handler_btn_back。\n` +
        `- event_handler_btn_back 里停掉定时器、线程和播放，取消 main 的 LV_OBJ_FLAG_HIDDEN，删除 window。只靠底部上滑退出，不要另加退出按钮，退出时不弹确认或提示。\n` +
        `- 每个 label 都设置 tablet_font_get_freetype_font() 取到的字体和文字颜色。Montserrat 没有中文，不要直接用 lv_font_montserrat_*。\n` +
        `- 按钮用 lv_button_create，并用 lv_obj_add_event_cb(..., LV_EVENT_CLICKED, ...) 响应。\n` +
        `- 短提示音才用 m->audio->play_notice(0, wav路径)。它只认 WAV/PCM/G711，采样率只有 8k 和 16k，不是音乐播放器。\n` +
        `- 播放文件用 m->player：create(&handle)、set_input_file(handle, 路径)、prepare(handle)、start(handle)。停止、暂停、音量、时长都带这个 handle，禁止传 NULL。时长用 get_duration，取不到就不要写死秒数。可播放：wav、pcm、mp3、aac、m4a、flac、ogg、opus。裸 mp3/aac/wav/g711 走 SDK 硬解，m4a/flac/ogg/opus 由播放器拆包或软解，应用不要自己解码。\n` +
        `- 纯音频录音不能用 m->recorder：录像 mux 层（mp4_std_mux/raw mux）只在有视频样本时才写文件头，音频-only 的 mp4/wav 产出 0 字节或不可播的文件。要录音频自己拉流写 WAV：m->audio->stream_get_enc_param(0,&enc) 存下来 → stream_set_enc_param(0, codec=PCM、sample_rate=16000、channels=1、sample_bits=16、enable=1) → stream_enable(0,1) → stream_reg_client(0,&client,NULL,NULL) → 工作线程循环 stream_get_frame(client,&frame) 把 frame.data 追加写进 44 字节头先占位的文件，头里的 RIFF/ data 长度在停止时回填 → stream_unreg_client + stream_enable(0,0) + 恢复之前存的 enc 参数（其他应用如对讲按 board 默认 G711A@8k 用这个流）。m->recorder 只用于视频（或视频+音频）录卡。\n` +
        `骨架：\n${skeleton}`,
        `[App contract] The launcher calls app_entry_start_${id}(lv_obj_t *main), which calls tablet_${id}_create(main). Write tablet_${id}.c from the skeleton below:\n` +
        `- Build the UI in a window on lv_display_get_screen_active(). Do not use lv_obj_create(NULL), lv_scr_load, or lv_screen_load.\n` +
        `- Call tablet_create_top(window, title, event_handler_btn_back, main). It draws the title and the swipe-up home bar, and calls event_handler_btn_back on exit.\n` +
        `- In event_handler_btn_back, stop timers, threads, and playback, clear LV_OBJ_FLAG_HIDDEN on main, and delete the window. Exit only through the swipe-up bar: no separate exit button and no confirmation or prompt on exit.\n` +
        `- Give every label the font from tablet_font_get_freetype_font() and a text color. Montserrat has no Chinese, so do not use lv_font_montserrat_* directly.\n` +
        `- Create buttons with lv_button_create and handle them with lv_obj_add_event_cb(..., LV_EVENT_CLICKED, ...).\n` +
        `- Use m->audio->play_notice(0, wavPath) only for a short prompt. It accepts WAV/PCM/G711 at 8 kHz or 16 kHz only. It is not a music player.\n` +
        `- Play a file with m->player: create(&handle), set_input_file(handle, path), prepare(handle), start(handle). Pass that handle to stop, pause, volume, and duration. Never pass NULL. Use get_duration, and do not hard-code a length when it returns nothing. Playable: wav, pcm, mp3, aac, m4a, flac, ogg, opus. Raw mp3/aac/wav/g711 use the SDK decoder. m4a/flac/ogg/opus are demuxed or software-decoded by the player. Do not decode them in the app.\n` +
        `- Audio-only recording must NOT use m->recorder: the recording mux layer (mp4_std_mux / raw mux) only writes a file header when video samples exist, so an audio-only mp4/wav comes out 0 bytes or unplayable. To record audio, pull the stream and write the WAV yourself: m->audio->stream_get_enc_param(0,&enc) and save it → stream_set_enc_param(0, codec=PCM, sample_rate=16000, channels=1, sample_bits=16, enable=1) → stream_enable(0,1) → stream_reg_client(0,&client,NULL,NULL) → a worker thread loops stream_get_frame(client,&frame) appending frame.data to a file whose 44-byte header is reserved first, patching the RIFF/data lengths back in on stop → stream_unreg_client + stream_enable(0,0) + restore the enc params saved earlier (other apps such as talk use this stream at the board default G711A@8k). Use m->recorder only for video (or video+audio) card recording.\n` +
        `Skeleton:\n${skeleton}`,
    );
}

function appEntryBroken(id) {
    if (!id || BUILTIN_APPS.has(id)) return false;
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    const entry = join(dir, `app_entry_${id}.c`);
    if (!existsSync(entry)) return true;
    if (!new RegExp(`app_entry_start_${id}\\s*\\(\\s*lv_obj_t\\s*\\*`).test(readFileSync(entry, 'utf8'))) return true;
    const main = join(dir, `tablet_${id}.c`);
    return existsSync(main) && !new RegExp(`tablet_${id}_create\\s*\\(\\s*lv_obj_t`).test(readFileSync(main, 'utf8'));
}

function contractApplies(id, appName) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id) || BUILTIN_APPS.has(id)) return false;
    return existsSync(join(repoDir(), 'application', 'packages', 'tablet', id)) || !!appName;
}

function appContractNotes(id) {
    if (!id || BUILTIN_APPS.has(id)) return [];
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    const main = join(dir, `tablet_${id}.c`);
    if (!existsSync(main)) {
        return [tr(`缺少 tablet_${id}.c。`, `tablet_${id}.c is missing.`)];
    }
    let all = '';
    try {
        for (const name of readdirSync(dir)) {
            if (!/\.c$/i.test(name) || name === `app_entry_${id}.c`) continue;
            all += readFileSync(join(dir, name), 'utf8') + '\n';
        }
    } catch { /* report on what was read */ }
    const notes = [];
    if (!new RegExp(`lv_result_t\\s+tablet_${id}_create\\s*\\(\\s*lv_obj_t\\s*\\*\\s*\\w+\\s*\\)`).test(all))
        notes.push(tr(`tablet_${id}.c 没有定义 lv_result_t tablet_${id}_create(lv_obj_t *main)。`, `tablet_${id}.c does not define lv_result_t tablet_${id}_create(lv_obj_t *main).`));
    if (/\blv_obj_create\s*\(\s*NULL\s*\)|\blv_scr_load|\blv_screen_load/.test(all))
        notes.push(tr('不要用 lv_obj_create(NULL) 或 lv_scr_load 另起一屏，界面要建在当前屏幕的 window 里，否则桌面被顶掉、无法操作。', 'Do not create a new screen with lv_obj_create(NULL) or lv_scr_load. Build in a window on the active screen, or the launcher is replaced and nothing responds.'));
    if (/\blv_\w+_create\s*\(\s*main\s*\)/.test(all) || /\blv_obj_set_(?:style_\w+|size|flex_\w+)\s*\(\s*main\b/.test(all))
        notes.push(tr(
            '界面建在了 main 下面或改了 main 的样式。main 是桌面，入口会把它隐藏，建在它下面的界面也一起被隐藏，进去就是白屏。要在 lv_display_get_screen_active(lv_display_get_default()) 上建 window，所有控件都建在 window 里，不要动 main 的样式。',
            'The UI is built under main or changes main\'s style. main is the launcher, and the entry hides it, so anything under it is hidden too and the screen is blank. Create a window on lv_display_get_screen_active(lv_display_get_default()), build every widget inside it, and leave main\'s style alone.',
        ));
    if (/\blv_obj_(?:remove|clear)_flag\s*\(\s*main\s*,\s*LV_OBJ_FLAG_HIDDEN/.test(all.slice(all.search(new RegExp(`tablet_${id}_create\\s*\\(\\s*lv_obj_t`)))))
        notes.push(tr(
            `tablet_${id}_create() 里不要取消 main 的隐藏，只在退出回调里取消。`,
            `Do not unhide main inside tablet_${id}_create(); do that only in the exit callback.`,
        ));
    if (!/\btablet_create_(?:top|top_2|home_bar)\s*\(/.test(all))
        notes.push(tr('没有调用 tablet_create_top()，应用没有上滑退出条，无法退出。', 'tablet_create_top() is not called, so the app has no home bar and cannot exit.'));
    if (!/LV_OBJ_FLAG_HIDDEN/.test(all) || !/\blv_obj_del(?:ete)?\s*\(/.test(all))
        notes.push(tr('退出回调要取消 main 的 LV_OBJ_FLAG_HIDDEN 并删除 window，否则回不到桌面。', 'The exit callback must clear LV_OBJ_FLAG_HIDDEN on main and delete the window, or the launcher does not come back.'));
    if (!/\btablet_font_get_freetype_font\s*\(/.test(all) || !/lv_obj_set_style_text_font\s*\(/.test(all))
        notes.push(tr('文字没有用 tablet_font_get_freetype_font() 的字体，中文会看不见。每个 label 都要设置这个字体。', 'Text does not use the font from tablet_font_get_freetype_font(), so Chinese is invisible. Set that font on every label.'));
    if (/\blv_font_montserrat_\d+/.test(all))
        notes.push(tr('不要直接用 lv_font_montserrat_*，它没有中文。改用 tablet_font_get_freetype_font()。', 'Do not use lv_font_montserrat_* directly; it has no Chinese. Use tablet_font_get_freetype_font().'));
    {
        /* 主程序静态链 liblvgl.a 只带它自己用到的对象，-rdynamic 只导出这些。
         * 插件 .so 引用未导出的 lv_* 符号在 dlopen(RTLD_NOW) 时重定位失败，
         * 桌面就弹 "app entry not found"。这些控件至今没有链进主程序。 */
        const banned = all.match(/\blv_(?:canvas|imagebutton|animimg|animimage|barcode|qrcode|imgfont|ime_pinyin|file_explorer|fragment|snapshot|gif|lottie|tiny_ttf|3dtexture|calendar_chinese)\w*\s*\(/g) || [];
        const uniq = [...new Set(banned.map(s => s.replace(/\s*\($/, '')))];
        if (uniq.length)
            notes.push(tr(
                `用了主程序没有导出的 LVGL 控件：${uniq.join('、')}。这些控件没有链进主程序，dlopen 应用会失败，桌面就弹「app entry not found」。二维码/自绘图形不要用 lv_canvas，用 lv_image 加内存位图：static uint32_t buf[高*宽] 按 XRGB8888（0xFF000000=黑、0xFFFFFFFF=白）填好，lv_image_dsc_t 填 magic=LV_IMAGE_HEADER_MAGIC、cf=LV_COLOR_FORMAT_XRGB8888、w/h、stride=宽*4、data、data_size，再 lv_image_create + lv_image_set_src 显示。参照 application/packages/tablet/share/tablet_share.c 的 qr_render_image()。`,
                `The app uses LVGL widgets the main program does not export: ${uniq.join(', ')}. Those widgets are not linked into the main binary, so dlopen of the app fails and the launcher shows "app entry not found". For a QR code or custom drawing, do not use lv_canvas. Draw into a memory bitmap shown by lv_image: fill a static uint32_t buf[height*width] as XRGB8888 (0xFF000000=black, 0xFFFFFFFF=white), set lv_image_dsc_t magic=LV_IMAGE_HEADER_MAGIC, cf=LV_COLOR_FORMAT_XRGB8888, w/h, stride=width*4, data, data_size, then lv_image_create + lv_image_set_src. See qr_render_image() in application/packages/tablet/share/tablet_share.c.`,
            ));
    }
    if (/__attribute__\s*\(\s*\(\s*weak\s*\)\s*\)/.test(all))
        notes.push(tr('不要声明 weak 的空实现，播放用 app_main_get_multimedia()->audio->play_notice()。', 'Do not declare weak stubs. Play audio with app_main_get_multimedia()->audio->play_notice().'));
    if (/->stop\s*\(\s*NULL\s*\)/.test(all))
        notes.push(tr(
            'player->stop 不能传 NULL。要用 create 得到的 handle。NULL 会直接返回，什么也停不了。',
            'player->stop must not be called with NULL. Use the handle from create. NULL returns immediately and stops nothing.',
        ));
    if (/play_notice\s*\(/.test(all) && /\.mp3|flac|m4a|\.ogg/.test(all))
        notes.push(tr(
            '音乐文件不要用 play_notice。play_notice 只播短 WAV 提示音。文件播放用 m->player 的 create、set_input_file、prepare、start。可列出 wav、pcm、mp3、aac、m4a、flac、ogg、opus。',
            'Do not play music with play_notice. play_notice is only for a short WAV prompt. Play files with m->player create, set_input_file, prepare, and start. List wav, pcm, mp3, aac, m4a, flac, ogg, and opus.',
        ));
    if (/->prepare\s*\(\s*[^,]+,\s*NULL\s*\)/.test(all))
        notes.push(tr(
            'recorder->prepare 不能传 NULL，空配置会被拒绝，界面就会显示无法录音。传 media_recorder_profile_t：output_flags = MEDIA_OUTPUT_CARD_STORAGE，module_flags.enable_audio = 1，enable_video = 0。',
            'recorder->prepare must not be called with NULL. An empty profile is rejected and the UI reports that recording failed. Pass a media_recorder_profile_t with output_flags = MEDIA_OUTPUT_CARD_STORAGE, module_flags.enable_audio = 1 and enable_video = 0.',
        ));
    if (/\brecorder->create\b|->recorder->/.test(all) && !/\benable_video\s*=\s*1\b/.test(all)) {
        const audioWav = /set_output_file\s*\([^)]*\.wav|set_output_file\s*\([^)]*\.aac|set_output_file\s*\([^)]*\.pcm/.test(all);
        notes.push(tr(
            audioWav
                ? 'm->recorder 不能录纯音频：输出 .wav/.aac/.pcm 时 mux 层不会写任何样本（文件 0 字节）。改用音频流拉取 + 自己写 WAV 头：stream_get_enc_param 存参数 → stream_set_enc_param(PCM/16k/单声道) → stream_enable(0,1) → stream_reg_client(0,&client,NULL,NULL) → 线程循环 stream_get_frame 追加写文件、停止时回填 44 字节头 → 关闭时 stream_unreg_client + stream_enable(0,0) + 恢复存的参数。'
                : 'm->recorder 是录像通道，只能在 enable_video=1 时产出可播放文件；纯音频录音（备忘录类）要用 m->audio 的流接口：stream_set_enc_param(PCM/16k/单声道) → stream_enable(0,1) → stream_reg_client → 工作线程 stream_get_frame 写 WAV，停止时回填文件头并恢复原 enc 参数。',
            audioWav
                ? 'm->recorder cannot record audio only: with a .wav/.aac/.pcm output the mux layer writes no samples at all (0-byte file). Pull the audio stream and write the WAV yourself: save stream_get_enc_param → stream_set_enc_param(PCM/16k/mono) → stream_enable(0,1) → stream_reg_client(0,&client,NULL,NULL) → a thread loops stream_get_frame appending to the file and patches the 44-byte header on stop → stream_unreg_client + stream_enable(0,0) + restore the saved params.'
                : 'm->recorder is the video recording path and only produces a playable file with enable_video=1. Audio-only recording (memo-style apps) must use the m->audio stream API: stream_set_enc_param(PCM/16k/mono) → stream_enable(0,1) → stream_reg_client → a worker thread writes stream_get_frame output as WAV, patching the header on stop and restoring the original enc params.',
        ));
    }
    if (/stream_reg_client\s*\(/.test(all) && !/stream_set_enc_param\s*\(/.test(all))
        notes.push(tr(
            '注册音频流客户端前要先 stream_set_enc_param(0, ...) 指定格式（纯录音用 PCM/16k/单声道/16bit），否则沿用板级默认 G711A@8k，写出的 WAV 头会标错格式。',
            'Before registering an audio stream client, call stream_set_enc_param(0, ...) to set the format (PCM/16k/mono/16bit for raw recording), or the board default G711A@8k is used and the WAV header lies about the format.',
        ));
    if (/stream_set_enc_param\s*\(/.test(all) && !/stream_get_enc_param\s*\(/.test(all))
        notes.push(tr(
            'stream 0 是全局共享的（对讲等应用按默认 G711A@8k 使用）。改格式前先 stream_get_enc_param(0,&enc) 存下来，录音结束恢复，否则会弄坏其他应用的音频。',
            'Stream 0 is shared (talk and others use the board default G711A@8k). Save the format with stream_get_enc_param(0,&enc) before changing it and restore it when recording ends, or other apps\' audio breaks.',
        ));
    if (/stream_reg_client\s*\(/.test(all) && /scan_recordings\s*\(|opendir\s*\(/.test(all) && !/pthread_join|thread_destroy/.test(all))
        notes.push(tr(
            '停止录音后要先把采集线程 join 掉、回填 WAV 头并 fclose，再扫描目录刷新列表，否则扫到的还是空文件或旧数据。',
            'After stopping, join the capture thread, patch the WAV header, and fclose before rescanning the directory for the list, or the scan sees an empty or stale file.',
        ));
    return notes;
}

function compileFixWhere(diag) {
    if (!diag) return '';
    const bits = [];
    for (const h of (diag.hits || []).slice(0, 3)) {
        bits.push(h.col ? `${h.file}:${h.line}:${h.col}` : `${h.file}:${h.line}`);
    }
    if (diag.undef && diag.undef.length)
        bits.push(tr(`未定义 ${diag.undef.slice(0, 3).join('、')}`, `undefined ${diag.undef.slice(0, 3).join(', ')}`));
    if (diag.multi && diag.multi.length)
        bits.push(tr(`重复定义 ${diag.multi.slice(0, 3).join('、')}`, `multiple definition ${diag.multi.slice(0, 3).join(', ')}`));
    return bits.join(tr('；', '; '));
}

function compileFixBrief(diag, target) {
    const id = appIdOf(target);
    const lines = [
        tr(
            '按下面的位置改应用源码。不要改 Makefile，不要改这个应用目录以外的文件。',
            'Edit the app source at the locations below. Do not edit the Makefile or files outside this app directory.',
        ),
    ];
    const rules = sourceRulesBlock(id);
    if (rules) lines.push(rules);
    if (diag && diag.hits && diag.hits.length) {
        lines.push(tr('编译器指出的位置：', 'Compiler locations:'));
        for (const h of diag.hits) {
            const at = h.col ? `${h.file}:${h.line}:${h.col}` : `${h.file}:${h.line}`;
            lines.push(`- ${at} ${h.msg}`);
        }
    }
    if (diag && diag.undef && diag.undef.length) {
        lines.push(tr(
            `链接缺少符号：${diag.undef.join('、')}。在该应用根目录的 .c 里定义。若是入口被剪掉，检查 app_entry_*.c 的 visibility("default") 和 keep-alive。`,
            `Undefined symbols: ${diag.undef.join(', ')}. Define them in a .c at the app root. If the entry was dropped, check visibility("default") and the keep-alive in app_entry_*.c.`,
        ));
    }
    if (diag && diag.multi && diag.multi.length) {
        lines.push(tr(
            `重复定义：${diag.multi.join('、')}。根目录里有两个 .c 定义了同一符号，只留一处。`,
            `Multiple definitions: ${diag.multi.join(', ')}. Two .c files at the app root define the same symbol. Keep one.`,
        ));
    }
    return lines.join('\n');
}

function sourceLayoutNotes(target) {
    const id = appIdOf(target);
    if (!id) return [];
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    if (!existsSync(dir)) {
        return [tr(
            `应用目录 application/packages/tablet/${id}/ 不存在。`,
            `App directory application/packages/tablet/${id}/ is missing.`,
        )];
    }
    let names = [];
    try { names = readdirSync(dir); } catch { names = []; }
    if (names.some(name => /\.c$/i.test(name))) return [];
    return [tr(
        `application/packages/tablet/${id}/ 根目录没有 .c。编译只收集这一层的 *.c，子目录里的 .c 不会进库。请把源码写到根目录。`,
        `No .c file is in the root of application/packages/tablet/${id}/. The build only takes *.c from that directory. Write the sources in the root.`,
    )];
}

let apiHeaderCache = null;

function apiHeaders() {
    if (apiHeaderCache) return apiHeaderCache;
    const repo = repoDir();
    const files = [];
    const take = dir => {
        let names = [];
        try { names = readdirSync(dir); } catch { return; }
        for (const n of names) if (/\.h$/i.test(n)) files.push(join(dir, n));
    };
    let services = [];
    try { services = readdirSync(join(repo, 'framework', 'services'), { withFileTypes: true }); } catch { services = []; }
    for (const s of services) if (s.isDirectory()) take(join(repo, 'framework', 'services', s.name, 'include'));
    take(join(repo, 'application', 'app_main'));
    take(join(repo, 'application', 'packages', 'tablet', 'launcher'));
    apiHeaderCache = [];
    for (const f of files) {
        try { apiHeaderCache.push({ file: f, text: readHostText(f).replace(/\r\n/g, '\n') }); } catch { /* skip unreadable */ }
    }
    return apiHeaderCache;
}

function tidyDecl(text) {
    return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/[ \t]+$/gm, '').replace(/\n\s*\n+/g, '\n').trim();
}

function findTypeDecl(text, name) {
    const end = text.search(new RegExp(`\\}\\s*${name}\\s*;`));
    if (end < 0) return '';
    let depth = 0;
    for (let i = end; i >= 0; i--) {
        if (text[i] === '}') depth++;
        else if (text[i] === '{' && --depth === 0) {
            const head = text.lastIndexOf('typedef', i);
            if (head < 0) return '';
            const tail = text.indexOf(';', end);
            return tidyDecl(text.slice(head, tail + 1));
        }
    }
    return '';
}

function findFuncDecl(text, name) {
    const m = text.match(new RegExp(`^(?![ \\t]*(?:\\*|//|/\\*|#))[^\\n]*\\b${name}\\s*\\([^;{]*\\)\\s*;`, 'm'));
    return m ? tidyDecl(m[0]) : '';
}

function typeMembers(decl) {
    const body = decl.slice(decl.indexOf('{') + 1, decl.lastIndexOf('}'));
    const names = new Set();
    for (const m of body.matchAll(/\(\s*\*\s*(\w+)\s*\)\s*\(/g)) names.add(m[1]);
    for (const stmt of body.split(';')) {
        if (stmt.includes('(')) continue;
        const m = stmt.match(/\b(\w+)\s*(?:\[[^\]]*\])?\s*$/);
        if (m) names.add(m[1]);
    }
    names.delete('reserved');
    return [...names];
}

/* "T has no member named X": say plainly what T does have, so the model stops renaming X. */
function missingMemberNotes(log) {
    const q = "[‘’'`\"]";
    const re = new RegExp(`${q}(?:struct )?(\\w+)${q} has no member named ${q}(\\w+)${q}`, 'g');
    const pairs = new Map();
    for (const m of String(log || '').matchAll(re)) {
        if (!pairs.has(m[1])) pairs.set(m[1], new Set());
        pairs.get(m[1]).add(m[2]);
    }
    const notes = [];
    for (const [type, missing] of pairs) {
        let members = [];
        for (const h of apiHeaders()) {
            const decl = findTypeDecl(h.text, type);
            if (decl) { members = typeMembers(decl); break; }
        }
        const gone = [...missing].join('、');
        notes.push(members.length
            ? tr(
                `${type} 没有 ${gone}。它只有这些成员：${members.join('、')}。不要再换名字猜。需要的功能在这些成员里没有，就删掉这段调用（例如 play_notice 播完自动结束，没有停止接口，停止时只更新界面状态）。`,
                `${type} has no ${[...missing].join(', ')}. Its only members are: ${members.join(', ')}. Do not guess another name. If none of these does what you need, delete that call (for example play_notice ends by itself and has no stop call, so stopping only updates the UI state).`,
            )
            : tr(`${type} 没有 ${gone}。不要再换名字猜，删掉这段调用。`, `${type} has no ${[...missing].join(', ')}. Do not guess another name; delete that call.`));
    }
    return notes.join('\n');
}

const API_MEMBER_TYPES = { audio: 'audio_ops_t', player: 'player_ops_t', recorder: 'recorder_ops_t', camera: 'camera_ops_t', osd: 'osd_ops_t' };

function apiNamesFrom(log, source, requestText) {
    const types = new Set();
    const funcs = new Set();
    const q = "[‘’'`\"]";
    const each = (re, set) => { for (const m of String(log || '').matchAll(re)) set.add(m[1]); };
    each(new RegExp(`${q}(?:struct )?(\\w+)${q} has no member named`, 'g'), types);
    each(new RegExp(`unknown type name ${q}(\\w+)${q}`, 'g'), types);
    each(new RegExp(`${q}(\\w+)${q} undeclared`, 'g'), types);
    each(new RegExp(`implicit declaration of function ${q}(\\w+)${q}`, 'g'), funcs);
    each(new RegExp(`(?:too (?:many|few) arguments to function|argument \\d+ of) ${q}(\\w+)${q}`, 'g'), funcs);
    const src = String(source || '');
    for (const m of src.matchAll(/\b(multimedia_t|\w+_ops_t|media_\w+_t)\b/g)) types.add(m[1]);
    for (const m of src.matchAll(/->\s*(audio|player|recorder|camera|osd)\s*->/g)) types.add(API_MEMBER_TYPES[m[1]]);
    const ask = String(requestText || '');
    if (/录音|录制|record/i.test(ask)) types.add('recorder_ops_t');
    if (/播放|音乐|歌|音频|声音|play|music|audio|sound/i.test(ask)) { types.add('player_ops_t'); types.add('audio_ops_t'); }
    if (/相机|拍照|摄像|camera|photo/i.test(ask)) types.add('camera_ops_t');
    if (types.size) types.add('multimedia_t');
    return { types: [...types], funcs: [...funcs] };
}

/* Real declarations from the public headers, so the model stops inventing members. */
function apiRefsBlock(log, target, requestText) {
    const id = appIdOf(target);
    let source = '';
    if (id) {
        const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
        try {
            for (const n of readdirSync(dir)) if (/\.[ch]$/i.test(n)) source += readHostText(join(dir, n)) + '\n';
        } catch { /* no sources yet */ }
    }
    const { types, funcs } = apiNamesFrom(log, source, requestText);
    if (!types.length && !funcs.length) return '';
    const seen = new Set();
    const blocks = [];
    let size = 0;
    const add = (label, body) => {
        if (!body || seen.has(body) || size + body.length > 12000) return;
        seen.add(body);
        size += body.length;
        blocks.push(`/* ${label} */\n${body}`);
    };
    const headers = apiHeaders();
    for (const t of types) {
        for (const h of headers) {
            const body = findTypeDecl(h.text, t);
            if (body) { add(h.file.slice(repoDir().length + 1).split(sep).join('/'), body); break; }
        }
    }
    const wantFuncs = new Set(funcs);
    if (types.includes('multimedia_t')) wantFuncs.add('app_main_get_multimedia');
    for (const f of wantFuncs) {
        for (const h of headers) {
            const body = findFuncDecl(h.text, f);
            if (body) { add(h.file.slice(repoDir().length + 1).split(sep).join('/'), body); break; }
        }
    }
    if (!blocks.length) return '';
    return tr(
        `【真实接口声明】下面摘自仓库公共头文件。只能使用这里出现的成员和函数，参数按这里写。这里没有的功能（比如没有 pause 成员）就不要调用，换用已有接口实现，或者在界面上不提供。\n${blocks.join('\n\n')}`,
        `[Real API declarations] Taken from the repo's public headers. Use only the members and functions shown here, with these parameters. If something is not here (for example there is no pause member), do not call it; use what exists or leave the feature out.\n${blocks.join('\n\n')}`,
    );
}

function compileFixPrompt(moduleName, target, text, log, diag, extra) {
    return [
        tr('你在当前源码树里直接改文件，修掉这次编译错误。', 'Edit files in this source tree and fix this compile error.'),
        tr(`模块：${moduleName}`, `Module: ${moduleName}`),
        appIdOf(target) ? tr(`目录：application/packages/tablet/${appIdOf(target)}/`, `Directory: application/packages/tablet/${appIdOf(target)}/`) : '',
        extra || '',
        compileFixBrief(diag, target),
        missingMemberNotes(log),
        apiRefsBlock(log, target, text),
        text ? tr(`功能要求：${text}`, `Feature: ${text}`) : '',
        log ? tr(`编译输出：\n${log}`, `Build output:\n${log}`) : '',
        appSourcesBlock(target),
    ].filter(Boolean).join('\n\n');
}

async function compileUntilPass(pane, moduleName, text, target) {
    const max = 4;
    for (let i = 1; i <= max; i++) {
        if (llmCancelled()) return false;
        paneSend(pane, tr(
            `\n步骤 3/4 编译 ${moduleName}（第 ${i}/${max} 次）\n`,
            `\nStep 3/4 compile ${moduleName} (${i}/${max})\n`,
        ));
        const bucket = { text: '' };
        try {
            const art = await wslBuild(moduleName, buildLine(pane, bucket));
            paneSend(pane, tr(`步骤 3/4 编译通过 ${art}\n`, `Step 3/4 build passed ${art}\n`));
            return true;
        } catch (e) {
            const log = (bucket.text + '\n' + (e.message || e)).slice(-6000);
            paneSend(pane, tr(`步骤 3/4 编译失败：${e.message || e}\n`, `Step 3/4 build failed: ${e.message || e}\n`));
            if (i === max || llmCancelled()) return false;
            if (e.makefileDefect) {
                paneSend(pane, tr(
                    '步骤 3/4 编译规则写坏了。能拆开的已经拆开并重编，这次仍然失败，不再原样重编。\n',
                    'Step 3/4 a build rule is broken. What could be split apart was fixed and rebuilt. It still failed, so it will not be rebuilt unchanged.\n',
                ));
                return false;
            }
            const diag = classifyBuildLog(log, target);
            const layout = sourceLayoutNotes(target);
            const sourceProblem = diag.kind === 'source' || ((e.missingArtifact || diag.kind === 'artifact') && layout.length);
            if (!sourceProblem) {
                if (e.missingArtifact || diag.kind === 'artifact') {
                    paneSend(pane, tr(
                        '步骤 3/4 编译已经成功，但没有生成这个应用的安装目录。源码布局没有明显问题，不再原样重编。\n',
                        'Step 3/4 the build succeeded, but this app was not installed. The source layout shows nothing obvious to fix, so it will not be rebuilt unchanged.\n',
                    ));
                } else {
                    paneSend(pane, tr(
                        '步骤 3/4 构建过程出错，不改应用源码。\n',
                        'Step 3/4 the build system failed. The app source stays unchanged.\n',
                    ));
                }
                return false;
            }
            const where = compileFixWhere(diag);
            if (where) paneSend(pane, tr(`步骤 3/4 定位：${where}\n`, `Step 3/4 location: ${where}\n`));
            paneSend(pane, tr('步骤 3/4 把报错交给大模型修改\n', 'Step 3/4 sending the error to the model\n'));
            const fixed = await runAgent(pane, compileFixPrompt(
                moduleName, target, text, log, diag, layout.join('\n'),
            ), { write: true });
            if (fixed.rc !== 0) {
                if (!llmCancelled())
                    paneSend(pane, tr('步骤 3/4 大模型未完成修改\n', 'Step 3/4 the model did not finish the edit\n'));
                return false;
            }
        }
    }
    return false;
}

function appNameText(name) {
    if (typeof name === 'string') return name;
    if (name && typeof name === 'object')
        return tr('对象（列表会显示 [object Object]）', 'an object (the list shows [object Object])');
    return '';
}

function appNameOk(name) {
    if (typeof name !== 'string') return false;
    const m = name.trim().match(/^(.+)\(([^()]+)\)$/);
    if (!m) return false;
    return /[\u4e00-\u9fff]/.test(m[1]) && /[A-Za-z]/.test(m[2]);
}

function iconColor(v, fallback) {
    const m = String(v || '').trim().match(/^#?([0-9a-f]{6})([0-9a-f]{2})?$/i);
    if (!m) return fallback;
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, m[2] ? parseInt(m[2], 16) : 255];
}

// Home icons are drawn in code by launcher/tablet_home.c. Generated apps describe
// theirs in app.json: icon_kind / icon_glyph / icon_color / icon_color2.
// The names must stay in sync with s_icon_kinds and s_icon_glyphs there.
const ICON_KINDS = {
    glyph: '白色图形，用 glyph 指定',
    camera: '相机（横向机身 + 镜头）',
    screen_cam: '带屏摄像机（竖向带屏机身 + 镜头）',
    chat: '对话气泡（AI 对话、聊天）',
    gamepad: '游戏手柄',
    mic: '麦克风（录音、语音）',
};
const ICON_GLYPHS = {
    audio: '音符', video: '摄像/视频', image: '图片', list: '列表', settings: '齿轮',
    home: '房子', play: '播放', pause: '暂停', stop: '停止', next: '下一首', prev: '上一首',
    shuffle: '随机', loop: '循环', volume: '音量', mute: '静音', bell: '铃铛', envelope: '信封',
    call: '电话', wifi: 'WiFi', bluetooth: '蓝牙', gps: '定位', usb: 'USB', sd_card: 'SD 卡',
    drive: '硬盘', directory: '文件夹', file: '文档', edit: '笔', keyboard: '键盘',
    download: '下载', upload: '上传', refresh: '刷新', power: '电源', charge: '闪电',
    battery: '电池', eye: '眼睛', tint: '水滴', warning: '警告', ok: '对勾', trash: '垃圾桶',
    save: '保存', copy: '复制', cut: '剪刀', bars: '菜单', plus: '加号',
};
const ICON_KEYWORD_LOOKS = [
    [/录音|备忘录|麦克风|语音|record|memo|mic|voice/i, 'mic', '', '#FF8A80', '#E53950'],
    [/音乐|歌|播放器|music|song|player/i, 'glyph', 'audio', '#FF7AB6', '#D6246E'],
    [/视频|影片|电影|video|movie/i, 'glyph', 'video', '#FF9E6D', '#E8562A'],
    [/相册|图片|照片|画廊|photo|gallery|image|picture/i, 'glyph', 'image', '#6FC3FF', '#2A7DE1'],
    [/闹钟|提醒|通知|alarm|remind|notif/i, 'glyph', 'bell', '#FFD25E', '#F2A00F'],
    [/日历|日程|待办|清单|calendar|todo|task|list/i, 'glyph', 'list', '#7FDCB4', '#25A06E'],
    [/文件|目录|file|folder|explorer/i, 'glyph', 'directory', '#FFD27A', '#F0A020'],
    [/下载|download/i, 'glyph', 'download', '#74C0FC', '#1C7ED6'],
    [/聊天|对话|助手|chat|assistant|\bai\b/i, 'chat', '', '#8EC5FC', '#5B6CF0'],
    [/游戏|贪吃蛇|俄罗斯方块|game|snake|tetris/i, 'gamepad', '', '#FFB86B', '#EE6A24'],
    [/拍照|相机|camera/i, 'camera', '', '#FFA07A', '#F0533A'],
    [/网络|wifi|network/i, 'glyph', 'wifi', '#5ED3F3', '#0C8FD0'],
    [/蓝牙|bluetooth/i, 'glyph', 'bluetooth', '#6E9BFF', '#2F5BEA'],
    [/电池|电量|battery/i, 'glyph', 'battery', '#8BE38B', '#2FA84F'],
    [/地图|定位|导航|map|gps|location/i, 'glyph', 'gps', '#7ED957', '#2E9B3A'],
    [/电话|拨号|call|phone|dial/i, 'glyph', 'call', '#6EE7A0', '#1FA65A'],
    [/邮件|消息|短信|mail|message|sms/i, 'glyph', 'envelope', '#7FB2FF', '#3D6FE0'],
    [/设置|setting/i, 'glyph', 'settings', '#B8BECB', '#7A8292'],
    [/计时|秒表|倒计时|timer|stopwatch|countdown/i, 'glyph', 'loop', '#FFB27A', '#F06A2A'],
    [/画板|绘画|笔记|记事|note|draw|paint|edit/i, 'glyph', 'edit', '#FFC46B', '#F08A1F'],
    [/键盘|输入|keyboard|typing/i, 'glyph', 'keyboard', '#A0A8FF', '#5C63E6'],
    [/音量|volume/i, 'glyph', 'volume', '#7EE0D2', '#1FA59A'],
    [/天气|水|weather|water|rain/i, 'glyph', 'tint', '#7CC8FF', '#2F86EB'],
];
const ICON_FALLBACK_PALETTE = [
    ['#7CC8FF', '#2F86EB'], ['#FFB27A', '#F06A2A'], ['#7FDCB4', '#25A06E'],
    ['#C3A0FF', '#7B4DE0'], ['#FF8A9A', '#E0405A'], ['#6EE0E0', '#159FA6'],
];

function iconHex(v) {
    const s = String(v || '').trim();
    return /^#[0-9a-f]{6}$/i.test(s) ? s.toUpperCase() : null;
}

function normalizeIconLook(o) {
    if (!o || typeof o !== 'object') return null;
    const kind = String(o.kind || o.icon_kind || 'glyph').trim().toLowerCase();
    const glyph = String(o.glyph || o.icon_glyph || '').trim().toLowerCase();
    if (!(kind in ICON_KINDS)) return null;
    if (kind === 'glyph' && !(glyph in ICON_GLYPHS)) return null;
    const top = iconHex(o.top || o.icon_color);
    if (!top) return null;
    return { kind, glyph: kind === 'glyph' ? glyph : '', top, bottom: iconHex(o.bottom || o.icon_color2) || top };
}

function readAppJsonObject(id) {
    const file = join(repoDir(), 'application', 'packages', 'tablet', id, 'app.json');
    if (!existsSync(file)) return null;
    const text = readFileSync(file, 'utf8');
    for (const raw of [text, repairJson(text)]) {
        try {
            const j = JSON.parse(raw);
            if (j && typeof j === 'object' && !Array.isArray(j)) return j;
        } catch { /* try the repaired form */ }
    }
    return null;
}

function appIconLook(id) {
    const j = readAppJsonObject(id);
    return j ? normalizeIconLook(j) : null;
}

function usedIconLooks(exceptId) {
    const used = [];
    const root = join(repoDir(), 'application', 'packages', 'tablet');
    let dirs = [];
    try { dirs = readdirSync(root, { withFileTypes: true }).filter(d => d.isDirectory()).map(d => d.name); }
    catch { /* no tablet tree */ }
    for (const id of dirs) {
        if (id === exceptId) continue;
        const look = appIconLook(id);
        if (look) used.push({ id, ...look });
    }
    return used;
}

function iconLookClash(look, used) {
    return used.find(u => u.kind === look.kind && u.glyph === look.glyph && u.top === look.top) || null;
}

function keywordIconLook(id, appName, requestText) {
    const text = `${appName || ''} ${id || ''} ${String(requestText || '').slice(0, 400)}`;
    const used = usedIconLooks(id);
    for (const [re, kind, glyph, top, bottom] of ICON_KEYWORD_LOOKS) {
        if (re.test(text)) {
            const look = { kind, glyph, top, bottom };
            if (!iconLookClash(look, used)) return look;
        }
    }
    let h = 2166136261;
    for (const ch of String(id || 'app')) {
        h ^= ch.charCodeAt(0);
        h = Math.imul(h, 16777619);
    }
    const [top, bottom] = ICON_FALLBACK_PALETTE[(h >>> 0) % ICON_FALLBACK_PALETTE.length];
    return { kind: 'glyph', glyph: 'file', top, bottom };
}

function writeIconLook(id, look) {
    const file = join(repoDir(), 'application', 'packages', 'tablet', id, 'app.json');
    const j = readAppJsonObject(id) || { app_id: id };
    j.icon_kind = look.kind;
    if (look.glyph) j.icon_glyph = look.glyph;
    else delete j.icon_glyph;
    j.icon_color = look.top;
    j.icon_color2 = look.bottom;
    writeFileSync(file, JSON.stringify(j, null, 2) + '\n');
}

function iconLookText(look) {
    return look.kind === 'glyph'
        ? `${look.glyph}（${ICON_GLYPHS[look.glyph]}），${look.top} → ${look.bottom}`
        : `${look.kind}（${ICON_KINDS[look.kind]}），${look.top} → ${look.bottom}`;
}

function parseIconLook(out) {
    const text = String(out || '').replace(/```[a-z]*/gi, '');
    const start = text.indexOf('{');
    if (start < 0) return null;
    const body = text.slice(start, text.lastIndexOf('}') + 1 || undefined);
    for (const raw of [body, repairJson(text.slice(start))]) {
        try {
            const look = normalizeIconLook(JSON.parse(raw));
            if (look) return look;
        } catch { /* try the next form */ }
    }
    return null;
}

async function designAppIcon(pane, id, appName, requestText) {
    const used = usedIconLooks(id);
    const kinds = Object.entries(ICON_KINDS).map(([k, v]) => `${k}: ${v}`).join('\n');
    const glyphs = Object.entries(ICON_GLYPHS).map(([k, v]) => `${k}=${v}`).join(', ');
    const taken = used.map(u => `${u.id}: ${iconLookText(u)}`).join('\n');
    const prompt = tr(
        `给平板桌面应用选图标。应用名：${appName || id}。需求摘要：${String(requestText || '').slice(0, 1200)}\n` +
        `桌面图标由启动器画：圆角方形上下渐变底色，中间一个白色图形。和相机、带屏摄像机、音频算法、设置、小凯同一风格。\n` +
        `kind 只能选：\n${kinds}\n` +
        `kind 为 glyph 时，glyph 只能选：${glyphs}\n` +
        `已被占用的图标（不要和它们图形和颜色都一样，颜色最好也拉开）：\n${taken}\n` +
        `选最能看出这个应用用途的图形，配一组饱和、明亮的渐变色，top 比 bottom 亮。\n` +
        `只输出 JSON，不要解释：{"kind":"glyph","glyph":"audio","top":"#RRGGBB","bottom":"#RRGGBB"}`,
        `Pick a home-screen icon for a tablet app. App name: ${appName || id}. Request summary: ${String(requestText || '').slice(0, 1200)}\n` +
        `The launcher draws the icon: a rounded square with a vertical gradient and one white figure in the middle, in the same style as Camera, IPC Camera, Audio Algo, Settings and Volc Engine.\n` +
        `kind must be one of:\n${kinds}\n` +
        `When kind is glyph, glyph must be one of: ${glyphs}\n` +
        `Icons already in use (do not match both figure and color, and keep the colors apart):\n${taken}\n` +
        `Pick the figure that best shows what the app does, with a saturated, bright gradient where top is lighter than bottom.\n` +
        `Output JSON only, no explanation: {"kind":"glyph","glyph":"audio","top":"#RRGGBB","bottom":"#RRGGBB"}`,
    );
    for (let attempt = 1; attempt <= 2; attempt++) {
        if (llmCancelled()) return null;
        const { rc, out } = await runAgent(pane, prompt, { write: false, maxTokens: 1024 });
        const look = rc === 0 ? parseIconLook(out) : null;
        if (look && !iconLookClash(look, used)) {
            writeIconLook(id, look);
            return look;
        }
    }
    return null;
}


function ensureAppJson(id, appName) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id) || !appName) return [];
    const appDir = join(repoDir(), 'application', 'packages', 'tablet', id);
    if (!existsSync(appDir)) return [];
    const file = join(appDir, 'app.json');
    let j = {};
    if (existsSync(file)) {
        try { j = JSON.parse(readFileSync(file, 'utf8')); }
        catch {
            try { j = JSON.parse(repairJson(readFileSync(file, 'utf8'))); } catch { j = {}; }
        }
    }
    if (!j || typeof j !== 'object' || Array.isArray(j)) j = {};
    const want = {
        app_id: id,
        name: appName,
        action: j.action || id,
        entry_so: `lib/lib${id}_app.so`,
        entry_symbol: `app_entry_start_${id}`,
        version: j.version || '1.0.0',
    };
    const fixed = Object.keys(want).filter(k => j[k] !== want[k]);
    const stale = ['entry', 'entry_point', 'entry_func', 'icon'].filter(k => k in j);
    if (!fixed.length && !stale.length) return [];
    const next = { ...want };
    for (const [k, v] of Object.entries(j)) {
        if (!(k in want) && !stale.includes(k)) next[k] = v;
    }
    writeFileSync(file, JSON.stringify(next, null, 2) + '\n');
    return [...fixed, ...stale];
}

function checkWrittenApp(target, appName) {
    const id = appIdOf(target);
    const dir = id ? join(repoDir(), 'application', 'packages', 'tablet', id) : null;
    const notes = [];
    let ok = true;
    if (!dir || !existsSync(dir)) {
        return { ok: false, notes: [tr('应用目录不存在', 'App directory is missing')] };
    }
    const appJson = join(dir, 'app.json');
    if (!existsSync(appJson)) {
        ok = false;
        notes.push(tr('缺少 app.json', 'app.json is missing'));
    } else {
        try {
            const j = JSON.parse(readFileSync(appJson, 'utf8'));
            const shown = appNameText(j.name);
            if (appName && shown !== appName) {
                ok = false;
                notes.push(tr(
                    `app.json name 是「${shown}」，期望「${appName}」`,
                    `app.json name is "${shown}", expected "${appName}"`,
                ));
            } else if (!appNameOk(j.name)) {
                ok = false;
                notes.push(tr(
                    `app.json 的 name 必须是字符串「中文(English)」，例如 日历(Calendar)。现在是「${shown || '空'}」。不要写成对象，不要改 get_lan_index 的含义。`,
                    `app.json name must be the string "中文(English)", for example 日历(Calendar). It is "${shown || 'empty'}". Do not use an object, and do not change what get_lan_index means.`,
                ));
            }
            if (appName && id) {
                if (j.app_id !== id) {
                    ok = false;
                    notes.push(tr(
                        `app.json 的 app_id 是「${j.app_id || ''}」，必须是 ${id}。`,
                        `app.json app_id is "${j.app_id || ''}". It must be ${id}.`,
                    ));
                }
                const symbol = `app_entry_start_${id}`;
                if (j.entry_symbol !== symbol) {
                    ok = false;
                    notes.push(tr(
                        `app.json 的 entry_symbol 必须是 ${symbol}。`,
                        `app.json entry_symbol must be ${symbol}.`,
                    ));
                }
                const soBase = String(j.entry_so || '').split('/').pop();
                if (!/^lib.+\.so$/.test(soBase)) {
                    ok = false;
                    notes.push(tr(
                        `app.json 的 entry_so 必须是 lib/lib${id}_app.so。`,
                        `app.json entry_so must be lib/lib${id}_app.so.`,
                    ));
                }
            }
        } catch {
            ok = false;
            notes.push(tr('app.json 无法解析', 'app.json is not valid json'));
        }
    }
    const files = [];
    listAppTextFiles(dir, files);
    if (!files.some(p => /\.c$/i.test(p))) {
        ok = false;
        notes.push(tr('没有 .c 源文件', 'No .c source file'));
    }
    if (appName && id) {
        let rootC = [];
        try { rootC = readdirSync(dir).filter(name => /\.c$/i.test(name)); } catch { rootC = []; }
        if (!rootC.length) {
            ok = false;
            notes.push(tr(
                `根目录没有 .c。请把 tablet_${id}.c 和 app_entry_${id}.c 放在 application/packages/tablet/${id}/ 下，不要放进子目录。`,
                `No .c file is in the app root. Put tablet_${id}.c and app_entry_${id}.c directly under application/packages/tablet/${id}/, not in a subdirectory.`,
            ));
        }
        if (!existsSync(join(dir, `app_entry_${id}.c`))) {
            ok = false;
            notes.push(tr(
                `缺少 app_entry_${id}.c。入口函数是 app_entry_start_${id}，并写上 visibility("default") 和 keep-alive 自引用。`,
                `app_entry_${id}.c is missing. Export app_entry_start_${id} with visibility("default") and a keep-alive self-reference.`,
            ));
        }
    }
    if (contractApplies(id, appName)) {
        const contract = appContractNotes(id);
        if (contract.length) {
            ok = false;
            notes.push(...contract);
        }
    }
    return { ok, notes };
}

async function checkThenRepair(pane, target, appName) {
    const id = appIdOf(target);
    if (appName && id) {
        if (!BUILTIN_APPS.has(id) && !appIconLook(id) && existsSync(join(repoDir(), 'application', 'packages', 'tablet', id))) {
            try {
                const look = keywordIconLook(id, appName, '');
                writeIconLook(id, look);
                paneSend(pane, tr(
                    `app.json 没有桌面图标描述，按应用名补上：${iconLookText(look)}。\n`,
                    `app.json had no home icon description. Filled one from the app name: ${iconLookText(look)}.\n`,
                ));
            } catch { /* checkWrittenApp reports a failed write */ }
        }
        try {
            const keys = ensureAppJson(id, appName);
            if (keys.length) {
                paneSend(pane, tr(
                    `已按规则写好 app.json 的固定字段：${keys.join('、')}。这些字段由工具填写，不交给模型。\n`,
                    `Filled the fixed app.json fields: ${keys.join(', ')}. The tool owns these fields, so the model is not asked to write them.\n`,
                ));
            }
        } catch (e) {
            paneSend(pane, tr(`写 app.json 失败：${e.message || e}\n`, `Could not write app.json: ${e.message || e}\n`));
        }
    }
    const applies = contractApplies(id, appName);
    const scaffold = () => {
        if (!applies) return;
        try {
            const files = ensureAppScaffold(id);
            if (files.length) {
                paneSend(pane, tr(
                    `已按模板写入 ${files.join('、')}。入口由工具生成，启动器以 app_entry_start_${id}(lv_obj_t *main) 调用。\n`,
                    `Wrote ${files.join(', ')} from the template. The tool owns the entry; the launcher calls app_entry_start_${id}(lv_obj_t *main).\n`,
                ));
            }
        } catch (e) {
            paneSend(pane, tr(`写入口模板失败：${e.message || e}\n`, `Could not write the entry template: ${e.message || e}\n`));
        }
    };
    scaffold();
    let looked = checkWrittenApp(target, appName);
    if (looked.ok || llmCancelled()) return looked;
    paneSend(pane, tr(
        `步骤 2/4 检查未通过，交给大模型再改一次：${looked.notes.join('；')}\n`,
        `Step 2/4 check failed. Asking the model to edit again: ${looked.notes.join('; ')}\n`,
    ));
    const src = appSourcesBlock(target);
    const contract = applies && appContractNotes(id).length;
    const prompt = [
        tr('检查没有通过。按下面的说明改源码，只改需要的文件。', 'The check failed. Edit the source as follows, and change only the files required.'),
        sourceRulesBlock(id),
        contract ? appContractBlock(id) : '',
        contract ? tr(
            `tablet_${id}.c 不符合接入约定时，用 {"files":[{"path":"application/packages/tablet/${id}/tablet_${id}.c","content":"完整文件"}]} 按骨架整文件重写，保留需求里的功能。`,
            `If tablet_${id}.c breaks the contract, rewrite the whole file from the skeleton with {"files":[{"path":"application/packages/tablet/${id}/tablet_${id}.c","content":"the entire file"}]}, keeping the requested features.`,
        ) : '',
        contract ? apiRefsBlock('', target, '') : '',
        looked.notes.join('\n'),
        src,
    ].filter(Boolean).join('\n\n');
    const fixed = await runAgent(pane, prompt, { write: true, maxTokens: contract ? 24576 : 12288 });
    scaffold();
    if (fixed.rc !== 0 || llmCancelled()) return checkWrittenApp(target, appName);
    return checkWrittenApp(target, appName);
}

async function restartMainApp(tell) {
    tell(tr(
        '应用已变化，即将重启主应用程序。画面会短暂断开再连上。\n',
        'The app changed. The main application will restart. The picture will drop and come back.\n',
    ));
    if (!dev.connected) {
        /* A timed-out exec cuts the link on purpose and the agent reconnects
         * within seconds. Wait for the new HELLO before giving up. */
        if (!await waitForAgent(15000)) {
            tell(tr('设备未连接，无法重启主应用程序\n', 'The device is not connected, so the main application cannot be restarted\n'));
            return false;
        }
    }
    const cmd = product.relaunch;
    if (!cmd) {
        tell(tr('当前产品没有配置重启主应用程序的命令\n', 'This product has no command to restart the main application\n'));
        return false;
    }
    const before = dev.hello;
    try {
        await deviceExec(cmd, 15000);
    } catch (e) {
        /* The relaunch kills the agent's host process, so the reply can be
         * lost. The restart still happened when a fresh HELLO arrives from a
         * new main process. */
        if (!await waitForAgent(15000) || dev.hello === before) {
            tell(tr(
                `重启主应用程序没有返回：${e.message || e}\n`,
                `The restart command did not return: ${e.message || e}\n`,
            ));
            return false;
        }
    }
    tell(tr('已重启主应用程序\n', 'Restarted the main application\n'));
    return true;
}

async function showOnDevice(pane, moduleName) {
    if (!/^APP_TABLET_/.test(moduleName)) return;
    await restartMainApp(text => paneSend(pane, text));
}

function formatDuration(ms) {
    const total = Math.max(0, Math.round(Number(ms) / 1000));
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    if (minutes === 0) return tr(`${seconds} 秒`, `${seconds}s`);
    return tr(`${minutes} 分 ${seconds} 秒`, `${minutes}m ${seconds}s`);
}

async function deliverApp(pane, { prompt, text, target, moduleName, creating, appName }) {
    if (!moduleName) {
        paneSend(pane, tr('状态：没有可推送的模块\n', 'Status: there is no module to push\n'));
        return;
    }
    const started = Date.now();
    const times = {};
    const reportTimes = () => {
        const rows = [
            ['gen', '生成', 'generate'],
            ['build', '编译', 'compile'],
            ['check', '检查', 'check'],
            ['push', '推送', 'push'],
        ].filter(([key]) => times[key] != null);
        if (!rows.length) return;
        const body = rows.map(([key, zh, en]) => tr(`${zh} ${formatDuration(times[key])}`, `${en} ${formatDuration(times[key])}`)).join(tr('，', ', '));
        paneSend(pane, tr(
            `用时：${body}，合计 ${formatDuration(Date.now() - started)}\n`,
            `Time: ${body}, total ${formatDuration(Date.now() - started)}\n`,
        ));
    };
    const verb = creating ? tr('生成', 'generate') : tr('修改', 'edit');
    try {
        let mark = Date.now();
        paneSend(pane, tr(`\n步骤 1/4 ${verb}代码 ${moduleName}\n`, `\nStep 1/4 ${verb} ${moduleName}\n`));
        const appId = appIdOf(target);
        if (appId) {
            paneSend(pane, tr(
                `范围：application/packages/tablet/${appId}/\n`,
                `Scope: application/packages/tablet/${appId}/\n`,
            ));
        }
        paneSend(pane, creating
            ? tr(
                '步骤 1/4 正在请求模型写入这个应用目录里的文件。构建接线由工具完成。\n',
                'Step 1/4 asking the model to write files in this app directory. The tool wires the build.\n',
            )
            : tr(
                '步骤 1/4 正在请求模型只改完成要求所需的片段。\n',
                'Step 1/4 asking the model for the snippets this request needs.\n',
            ));
        let made = await runAgent(pane, prompt, { write: true, maxTokens: creating ? 24576 : 12288 });
        if (made.rc !== 0 && made.netFail && !llmCancelled()) {
            paneSend(pane, tr(
                '\n模型网络中断，等 10 秒后自动重试本次生成…\n',
                '\nThe model network dropped. Retrying this generation in 10 seconds…\n',
            ));
            await delay(10000);
            if (!llmCancelled())
                made = await runAgent(pane, prompt, { write: true, maxTokens: creating ? 24576 : 12288 });
        }
        times.gen = Date.now() - mark;
        if (made.rc !== 0) {
            if (llmCancelled()) return;
            paneSend(pane, tr(
                `步骤 1/4 ${verb}未完成，已停止，用时 ${formatDuration(times.gen)}\n`,
                `Step 1/4 ${verb} did not finish. Stopped. Took ${formatDuration(times.gen)}\n`,
            ));
            return;
        }
        const appDir = appId ? join(repoDir(), 'application', 'packages', 'tablet', appId) : '';
        const askedIcon = /图标|icon/i.test(String(text || ''));
        if (appId && existsSync(appDir) && !BUILTIN_APPS.has(appId)
            && (!appIconLook(appId) || askedIcon)) {
            paneSend(pane, tr('步骤 1/4 按应用内容设计桌面图标…\n', 'Step 1/4 designing the home icon from the app content…\n'));
            const shownName = appName || namesFromAppJson(appId).name || appId;
            let look = null;
            try { look = await designAppIcon(pane, appId, shownName, text); }
            catch (e) { paneSend(pane, `[icon] ${e.message || e}\n`); }
            if (llmCancelled()) return;
            if (!look) {
                look = keywordIconLook(appId, shownName, text);
                try { writeIconLook(appId, look); }
                catch (e) { paneSend(pane, `[icon] ${e.message || e}\n`); }
            }
            paneSend(pane, tr(
                `桌面图标：${iconLookText(look)}，已写入 app.json 的 icon_kind/icon_glyph/icon_color/icon_color2。\n`,
                `Home icon: ${iconLookText(look)}. Written to icon_kind/icon_glyph/icon_color/icon_color2 in app.json.\n`,
            ));
        }
        paneSend(pane, tr(
            `步骤 1/4 ${verb}结束，用时 ${formatDuration(Date.now() - mark)}\n`,
            `Step 1/4 ${verb} finished. Took ${formatDuration(Date.now() - mark)}\n`,
        ));
        times.gen = Date.now() - mark;
        mark = Date.now();
        paneSend(pane, tr(
            '\n步骤 2/4 检查（生成之后、编译之前）\n',
            '\nStep 2/4 check (after generate, before compile)\n',
        ));
        const looked = await checkThenRepair(pane, target, appName);
        times.check = Date.now() - mark;
        if (looked.notes.length) {
            paneSend(pane, tr(
                `步骤 2/4 ${looked.notes.join('；')}\n`,
                `Step 2/4 ${looked.notes.join('; ')}\n`,
            ));
        } else {
            paneSend(pane, tr(
                '步骤 2/4 通过：目录、app.json 与源文件都在。\n',
                'Step 2/4 passed: directory, app.json, and sources are present.\n',
            ));
        }
        paneSend(pane, tr(
            `步骤 2/4 用时 ${formatDuration(times.check)}\n`,
            `Step 2/4 took ${formatDuration(times.check)}\n`,
        ));
        if (!looked.ok) {
            paneSend(pane, tr('状态：检查未通过，未编译、未推送\n', 'Status: the check failed, so nothing was compiled or pushed\n'));
            return;
        }
        mark = Date.now();
        const built = await compileUntilPass(pane, moduleName, text, target);
        times.build = Date.now() - mark;
        paneSend(pane, tr(
            `步骤 3/4 编译用时 ${formatDuration(times.build)}\n`,
            `Step 3/4 compile took ${formatDuration(times.build)}\n`,
        ));
        if (!built) {
            if (llmCancelled()) return;
            paneSend(pane, tr('状态：编译未通过，未推送到设备\n', 'Status: the build failed, so nothing was pushed\n'));
            return;
        }
        mark = Date.now();
        paneSend(pane, tr(`\n步骤 4/4 推送到设备 ${moduleName}\n`, `\nStep 4/4 push ${moduleName} to the device\n`));
        const line = t => paneSend(pane, String(t).replace(/\r/g, '').replace(/\n?$/, '\n'));
        try {
            const result = await pushToDevice(moduleName, line);
            paneSend(pane, tr(`步骤 4/4 已推送到设备 ${JSON.stringify(result)}\n`, `Step 4/4 pushed ${JSON.stringify(result)}\n`));
            if (result && result.ok !== false) clearAppProgress(appIdOf(target));
            if (result && result.ok !== false && !result.reboot) await showOnDevice(pane, moduleName);
            if (result && result.reboot) paneSend(pane, tr('步骤 4/4 用户区已烧录，设备正在重启\n', 'Step 4/4 the user area was flashed. The device is rebooting.\n'));
        } catch (e) {
            paneSend(pane, tr(`步骤 4/4 推送失败 ${e.message || e}\n`, `Step 4/4 push failed ${e.message || e}\n`));
        }
        times.push = Date.now() - mark;
        paneSend(pane, tr(
            `步骤 4/4 推送用时 ${formatDuration(times.push)}\n`,
            `Step 4/4 push took ${formatDuration(times.push)}\n`,
        ));
    } finally {
        reportTimes();
    }
}

async function pushChangedModule(pane, moduleName) {
    if (!moduleName) {
        paneSend(pane, tr('状态：没有可推送的模块\n', 'Status: there is no module to push\n'));
        return;
    }
    const line = t => paneSend(pane, String(t).replace(/\r/g, '').replace(/\n?$/, '\n'));
    paneSend(pane, tr(`状态：正在编译并推送 ${moduleName}\n`, `Status: compiling and pushing ${moduleName}\n`));
    try {
        await wslBuild(moduleName, line);
        const result = await pushToDevice(moduleName, line);
        paneSend(pane, tr(`状态：已推送到设备 ${JSON.stringify(result)}\n`, `Status: pushed ${JSON.stringify(result)}\n`));
        if (result && result.ok !== false && !result.reboot && /^APP_TABLET_/.test(moduleName))
            await restartMainApp(text => paneSend(pane, text));
    } catch (e) {
        paneSend(pane, tr(`状态：推送失败 ${e.message || e}\n`, `Status: push failed ${e.message || e}\n`));
    }
}

/* ---------------- http server ---------------- */
function readBody(req, limit = 1024 * 1024) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let size = 0;
        req.on('data', d => {
            size += d.length;
            if (size > limit) { reject(new Error('body too large')); req.destroy(); return; }
            chunks.push(d);
        });
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', reject);
    });
}

function jsonReply(res, code, obj) {
    res.writeHead(code, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(obj));
}

const BUILTIN_APPS = new Set(product.builtins);
const SKIP_APP_DIRS = new Set(['launcher', 'build']);
const APP_RUNS_FILE = join(ROOT, 'app-runs.json');

function loadAppRuns() {
    try {
        const j = JSON.parse(readFileSync(APP_RUNS_FILE, 'utf8'));
        return j && typeof j === 'object' && !Array.isArray(j) ? j : {};
    } catch {
        return {};
    }
}

function saveAppRuns(runs) {
    writeFileSync(APP_RUNS_FILE, JSON.stringify(runs, null, 2) + '\n');
}

function appRunOf(id) {
    const rec = loadAppRuns()[id];
    return rec && typeof rec === 'object' ? rec : null;
}

function markAppProgress(id, names) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id) || BUILTIN_APPS.has(id)) return;
    const runs = loadAppRuns();
    const prev = runs[id] && typeof runs[id] === 'object' ? runs[id] : {};
    runs[id] = {
        zh: (names && names.zh) || prev.zh || '',
        en: (names && names.en) || prev.en || '',
        status: 'progress',
    };
    saveAppRuns(runs);
}

function clearAppProgress(id) {
    if (!id) return;
    const runs = loadAppRuns();
    if (!runs[id]) return;
    delete runs[id];
    saveAppRuns(runs);
}

function namesFromAppJson(id) {
    const file = join(repoDir(), 'application', 'packages', 'tablet', id, 'app.json');
    try {
        const name = JSON.parse(readFileSync(file, 'utf8')).name;
        if (typeof name !== 'string') return { name: '', zh: '', en: '' };
        const shown = name.trim();
        const m = shown.match(/^(.+)\(([^()]+)\)$/);
        if (!m) return { name: shown, zh: '', en: '' };
        return { name: shown, zh: m[1].trim(), en: m[2].trim() };
    } catch {
        return { name: '', zh: '', en: '' };
    }
}

function tabletGenerateState(id) {
    if (!id || !/^[a-z][a-z0-9_]{0,40}$/.test(id)) return 'missing';
    if (BUILTIN_APPS.has(id)) return 'builtin';
    const dir = join(repoDir(), 'application', 'packages', 'tablet', id);
    if (!existsSync(dir)) return 'missing';
    return 'present';
}

async function discardGeneratedApp(id, tell) {
    const steps = stripAppFromTree(id);
    for (const step of steps) tell(step);
    if (!dev.connected) {
        tell(tr('设备未连接，只清了电脑上的上次生成。', 'The device is not connected. Only the previous generation on this computer was removed.'));
        return;
    }
    try {
        const rm = await deviceExec(
            `mount -o remount,rw /mnt 2>&1 || true; rm -rf /mnt/install/apps/${id} /mnt/install/.app_bak/${id} /usr/apps/${id}`,
            20000,
        );
        tell(rm.ok === true || rm.exit === 0
            ? tr(`已去掉设备上的 ${id}`, `Removed ${id} from the device`)
            : tr(`设备上的 ${id} 未去掉：${String(rm.out || '').trim() || rm.exit}`, `Could not remove ${id} from the device: ${String(rm.out || '').trim() || rm.exit}`));
    } catch (e) {
        tell(tr(`设备上的 ${id} 未去掉：${e.message || e}`, `Could not remove ${id} from the device: ${e.message || e}`));
    }
}

function listTabletApps() {
    const repo = repoDir();
    const runs = loadAppRuns();
    const apps = [{
        id: product.agentId,
        name: product.agentName,
        module: product.agentModule,
        path: product.agentSrc,
        added: false,
        progress: false,
    }];
    const dir = join(repo, 'application', 'packages', 'tablet');
    const seen = new Set();
    let ents = [];
    try { ents = readdirSync(dir, { withFileTypes: true }); } catch { ents = []; }
    ents.sort((a, b) => a.name.localeCompare(b.name));
    for (const e of ents) {
        if (!e.isDirectory() || e.name.startsWith('.')) continue;
        if (!/^[a-z][a-z0-9_]{0,40}$/.test(e.name) || SKIP_APP_DIRS.has(e.name)) continue;
        const appDir = join(dir, e.name);
        const hasJson = existsSync(join(appDir, 'app.json'));
        let hasSource = hasJson;
        if (!hasSource) {
            try {
                hasSource = readdirSync(appDir).some(name => /\.(c|h|cpp)$/i.test(name));
            } catch { hasSource = false; }
        }
        const recorded = runs[e.name];
        const recordedProgress = !!(recorded && recorded.status === 'progress');
        const progress = !BUILTIN_APPS.has(e.name) && (recordedProgress || (!hasJson && hasSource));
        if (!hasSource && !recordedProgress) continue;
        seen.add(e.name);
        let name = e.name;
        if (progress && recorded && recorded.zh && recorded.en) name = `${recorded.zh}(${recorded.en})`;
        else if (hasJson) {
            try {
                const raw = JSON.parse(readFileSync(join(appDir, 'app.json'), 'utf8')).name;
                if (typeof raw === 'string' && raw) name = raw;
            } catch { /* keep directory name */ }
        }
        apps.push({
            id: e.name,
            name: String(name),
            module: 'APP_TABLET_' + e.name.toUpperCase(),
            path: `application/packages/tablet/${e.name}`,
            added: !BUILTIN_APPS.has(e.name),
            partial: !hasJson,
            progress,
        });
    }
    for (const [id, rec] of Object.entries(runs)) {
        if (!rec || rec.status !== 'progress' || seen.has(id) || BUILTIN_APPS.has(id)) continue;
        if (!/^[a-z][a-z0-9_]{0,40}$/.test(id)) continue;
        apps.push({
            id,
            name: rec.zh && rec.en ? `${rec.zh}(${rec.en})` : id,
            module: 'APP_TABLET_' + id.toUpperCase(),
            path: `application/packages/tablet/${id}`,
            added: true,
            partial: true,
            progress: true,
        });
    }
    return apps;
}

function stripConfigApp(text, macro) {
    const lines = text.split(/\n/);
    const out = [];
    const assign = new RegExp(`^\\s*${macro}\\s*\\??=\\s*.*$`);
    const define = new RegExp(`-D${macro}\\b`);
    const cond = new RegExp(`^\\s*ifn?eq\\s*\\(\\$\\(${macro}\\)\\s*,`);
    let i = 0;
    while (i < lines.length) {
        const line = lines[i];
        if (cond.test(line)) {
            let depth = 1;
            i++;
            while (i < lines.length && depth > 0) {
                if (/^\s*ifn?eq\b/.test(lines[i])) depth++;
                else if (/^\s*endif\b/.test(lines[i])) depth--;
                i++;
            }
            continue;
        }
        if (assign.test(line) || define.test(line)) { i++; continue; }
        out.push(line);
        i++;
    }
    return out.join('\n').replace(/\n{3,}/g, '\n\n');
}

function stripAppFromTree(id) {
    const repo = repoDir();
    const steps = [];
    const macro = 'CONFIG_APP_TABLET_' + id.toUpperCase();
    /* The tablet Makefile discovers apps from their directories and the home
     * screen reads the app registry, so removing the app directory below is
     * enough — nothing to unwind in the launcher source. */
    let projects = [];
    try { projects = readdirSync(join(repo, 'project'), { withFileTypes: true }); } catch { projects = []; }
    for (const p of projects) {
        if (!p.isDirectory()) continue;
        const cfg = join(repo, 'project', p.name, 'config.mk');
        if (!existsSync(cfg)) continue;
        const text = readFileSync(cfg, 'utf8');
        if (!text.includes(macro)) continue;
        writeFileSync(cfg, stripConfigApp(text, macro));
        steps.push(tr(`已从 ${p.name} 关掉 ${macro}`, `Turned off ${macro} in ${p.name}`));
    }
    const settings = [];
    const walk = (dir, depth) => {
        if (depth > 8) return;
        let ents = [];
        try { ents = readdirSync(dir, { withFileTypes: true }); } catch { return; }
        for (const e of ents) {
            if (e.name === 'out' || e.name === 'node_modules' || e.name.startsWith('.')) continue;
            const path = join(dir, e.name);
            if (e.isDirectory()) walk(path, depth + 1);
            else if (e.name === 'settings.json') settings.push(path);
        }
    };
    walk(join(repo, 'project'), 0);
    for (const path of settings) {
        let j;
        try { j = JSON.parse(readFileSync(path, 'utf8')); } catch { continue; }
        const apps = j?.installed_apps?.apps;
        if (!Array.isArray(apps) || !apps.some(a => a && a.app_id === id)) continue;
        j.installed_apps.apps = apps.filter(a => !a || a.app_id !== id);
        writeFileSync(path, JSON.stringify(j, null, 2) + '\n');
        steps.push(tr(`已从 ${path.slice(repo.length + 1)} 去掉登记`, `Removed the entry from ${path.slice(repo.length + 1)}`));
    }
    const src = join(repo, 'application', 'packages', 'tablet', id);
    if (existsSync(src)) {
        rmSync(src, { recursive: true, force: true });
        steps.push(tr(`已删除源码 ${src.slice(repo.length + 1)}`, `Deleted source ${src.slice(repo.length + 1)}`));
    }
    return steps;
}

async function deleteAddedApp(raw) {
    const id = String(raw || '').trim();
    if (!/^[a-z][a-z0-9_]{0,40}$/.test(id)) throw new Error(tr('应用 id 不正确', 'Invalid app id'));
    if (BUILTIN_APPS.has(id)) throw new Error(tr(`${id} 是系统自带应用，不能从这里删除`, `${id} is a built-in app and cannot be deleted here`));
    const known = listTabletApps().find(a => a.id === id);
    if (known && !known.added) throw new Error(tr(`${id} 是系统自带应用，不能从这里删除`, `${id} is a built-in app and cannot be deleted here`));
    const steps = stripAppFromTree(id);
    async function removeDeviceDir(ms = 45000) {
        const rm = await deviceExec(
            `mount -o remount,rw /mnt 2>&1 || true; rm -rf /mnt/install/apps/${id} /mnt/install/.app_bak/${id} /mnt/install/apps/${id}.bak /usr/apps/${id} /usr/apps/${id}.bak 2>&1`,
            ms,
        );
        const text = String(rm.out || '').trim();
        return { ok: rm.ok === true || rm.exit === 0, text, exit: rm.exit };
    }
    if (dev.connected) {
        /* After a relaunch the device can stay busy on /mnt for a while, so an
         * exec may time out. A timeout cuts the link on purpose; wait for the
         * agent and retry — rm -rf is idempotent, a lost reply is not a loss. */
        let rm = { ok: false, text: '', exit: '' };
        for (let i = 0; i < 2 && !rm.ok; i++) {
            if (i > 0 && !(dev.connected || await waitForAgent(15000))) break;
            try { rm = await removeDeviceDir(i === 0 ? 20000 : 45000); }
            catch (e) { rm = { ok: false, text: String(e.message || e), exit: '' }; }
        }
        /* Report the deletion now: it runs before the settings and restart steps. */
        steps.push(rm.ok
            ? tr(`已删除设备上的 /mnt/install/apps/${id}`, `Deleted /mnt/install/apps/${id} on the device`)
            : tr(
                `设备目录删除失败：${rm.text || rm.exit}`,
                `Could not delete the device directory: ${rm.text || rm.exit}`,
            ));
        const clearDeviceEntry = async () => {
            const got = await deviceExec('base64 /etc/config/settings.json');
            const rawJson = Buffer.from(String(got.out || '').replace(/\s/g, ''), 'base64').toString('utf8');
            const j = JSON.parse(rawJson);
            if (!Array.isArray(j?.installed_apps?.apps)) return;
            j.installed_apps.apps = j.installed_apps.apps.filter(a => !a || a.app_id !== id);
            await filePut('/etc/config/settings.json', JSON.stringify(j, null, 2) + '\n');
            steps.push(tr('已从设备 settings.json 去掉登记', 'Removed the entry from settings.json on the device'));
        };
        try {
            await clearDeviceEntry();
        } catch (e) {
            /* A timed-out exec cuts the link on purpose. Wait for the agent to
             * come back and try once more before reporting the failure. */
            try {
                if (!(dev.connected || await waitForAgent(15000))) throw e;
                await clearDeviceEntry();
            } catch (e2) {
                steps.push(tr(`设备登记未改：${e2.message || e2}`, `Device entry was not changed: ${e2.message || e2}`));
            }
        }
        const restarted = await restartMainApp(text => steps.push(text.replace(/\n$/, '')));
        if (!restarted) steps.push(tr('主应用程序未重启', 'The main application was not restarted'));
        if (!rm.ok) {
            const back = await waitForAgent(20000);
            if (back) {
                try { rm = await removeDeviceDir(); }
                catch (e) { rm = { ok: false, text: String(e.message || e), exit: '' }; }
            } else {
                rm = { ok: false, text: tr('代理没有重新连上', 'the agent did not reconnect'), exit: rm.exit };
            }
            steps.push(rm.ok
                ? tr(`已删除设备上的 /mnt/install/apps/${id}`, `Deleted /mnt/install/apps/${id} on the device`)
                : tr(
                    `设备目录删除失败：${rm.text || rm.exit}`,
                    `Could not delete the device directory: ${rm.text || rm.exit}`,
                ));
        }
    } else {
        steps.push(tr('设备未连接，只删除了电脑上的源码', 'The device is not connected. Only the source on this computer was deleted'));
    }
    clearAppProgress(id);
    return { ok: true, steps };
}

function listTree() {
    const repo = join(ROOT, '..', '..');
    const lines = [];
    function walk(rel, depth) {
        if (lines.length > 180 || depth > 2) return;
        let ents = [];
        try { ents = readdirSync(join(repo, rel), { withFileTypes: true }); }
        catch { return; }
        ents.sort((a, b) => a.name.localeCompare(b.name));
        for (const e of ents) {
            if (e.name.startsWith('.') || e.name === 'node_modules' || e.name === 'out') continue;
            const path = rel ? rel + '/' + e.name : e.name;
            lines.push('  '.repeat(depth) + e.name + (e.isDirectory() ? '/' : ''));
            if (e.isDirectory()) walk(path, depth + 1);
        }
    }
    lines.push('application/packages/tablet/');
    walk('application/packages/tablet', 1);
    lines.push('application/services/');
    walk('application/services', 1);
    return lines.join('\n');
}

const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
    if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }
    try {
        if (url.pathname === '/' || url.pathname === '/index.html') {
            res.writeHead(200, {
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'no-store',
            });
            res.end(readFileSync(join(ROOT, 'public', 'index.html')));
            return;
        }
        if (url.pathname === '/video') {
            res.writeHead(200, {
                'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                'Cache-Control': 'no-cache',
                Connection: 'close',
            });
            mjpegClients.add(res);
            syncDeviceFrames();
            req.on('close', () => {
                mjpegClients.delete(res);
                syncDeviceFrames();
            });
            return;
        }
        if (url.pathname === '/events') {
            res.writeHead(200, {
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                Connection: 'keep-alive',
            });
            res.write('retry: 2000\n\n');
            sseClients.add(res);
            if (dev.hello) res.write(`event: hello\ndata: ${JSON.stringify(dev.hello)}\n\n`);
            res.write(`event: status\ndata: ${JSON.stringify(deviceStatus())}\n\n`);
            res.write(`event: job\ndata: ${JSON.stringify({ busy: !!llmJob })}\n\n`);
            req.on('close', () => sseClients.delete(res));
            return;
        }
        if (url.pathname === '/api/lang' && req.method === 'GET') {
            return jsonReply(res, 200, { lang: uiLang });
        }
        if (url.pathname === '/api/lang' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            uiLang = body.lang === 'en' ? 'en' : 'zh';
            return jsonReply(res, 200, { lang: uiLang });
        }
        if (url.pathname === '/api/tree' && req.method === 'GET') {
            return jsonReply(res, 200, { tree: listTree() });
        }
        if (url.pathname === '/api/product' && req.method === 'GET') {
            return jsonReply(res, 200, {
                id: product.id,
                procAlias: product.procAlias,
                agentName: product.agentName,
            });
        }
        if (url.pathname === '/api/apps' && req.method === 'GET') {
            return jsonReply(res, 200, { apps: listTabletApps() });
        }
        if (url.pathname === '/api/apps/delete' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const result = await deleteAddedApp(String(body.id || ''));
                return jsonReply(res, 200, result);
            } catch (e) {
                return jsonReply(res, 400, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/version' && req.method === 'GET') {
            return jsonReply(res, 200, { ok: true, fileApi: 2 });
        }
        if (url.pathname === '/api/status') {
            jsonReply(res, 200, deviceStatus());
            return;
        }
        if (url.pathname === '/api/device' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const device = useDevice(body.device || body.host || '');
                return jsonReply(res, 200, { ok: true, device, connected: dev.connected });
            } catch (e) {
                return jsonReply(res, 400, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/exec' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: 'device not connected' });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            if (!body.cmd) return jsonReply(res, 400, { error: 'missing cmd' });
            const p = waitFor(PT.EXEC_RESULT, 15000);
            sendFrame(PT.EXEC, Buffer.from(JSON.stringify({ cmd: body.cmd })));
            const r = await p;
            return jsonReply(res, r ? 200 : 504, r ? JSON.parse(r.toString('utf8')) : { error: 'timeout' });
        }
        if (url.pathname === '/api/file/read' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const got = await fileGet(body.path || '');
                return jsonReply(res, 200, { ok: true, kind: 'file', ...got });
            } catch (e) {
                return jsonReply(res, 400, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/file/list' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const got = await fileListDir(body.path || '');
                return jsonReply(res, 200, { ok: true, kind: 'dir', ...got });
            } catch (e) {
                return jsonReply(res, 400, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/file/open' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const kind = await pathKind(body.path || '');
                if (kind === 'dir') {
                    const got = await fileListDir(body.path || '');
                    return jsonReply(res, 200, { ok: true, kind: 'dir', ...got });
                }
                if (kind === 'file') {
                    const got = await fileGet(body.path || '');
                    return jsonReply(res, 200, { ok: true, kind: 'file', ...got });
                }
                return jsonReply(res, 400, {
                    ok: false,
                    error: tr('路径不存在或不在允许范围内', 'Path does not exist or is not allowed'),
                });
            } catch (e) {
                return jsonReply(res, 400, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/file/write' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            const body = JSON.parse((await readBody(req, 2 * 1024 * 1024)).toString() || '{}');
            const p = normalizeDevPath(body.path || '');
            if (!p) return jsonReply(res, 400, { ok: false, error: tr('路径不在允许范围内', 'Path is not allowed') });
            const content = String(body.content ?? '');
            const buf = Buffer.from(content, 'utf8');
            if (buf.length > FILE_EDIT_MAX) {
                return jsonReply(res, 400, { ok: false, error: tr('内容超过 512KB', 'Content is larger than 512KB') });
            }
            try {
                if (p.startsWith('/mnt/') && !body.skipMntCheck) {
                    const st = await mntStatus();
                    if (st.readOnly) {
                        return jsonReply(res, 409, {
                            ok: false,
                            needRemount: true,
                            mnt: st,
                            error: tr(
                                '/mnt 当前为只读。先点「挂载可写」，或执行 mount -o remount,rw /mnt',
                                '/mnt is read-only. Click Remount RW or run mount -o remount,rw /mnt',
                            ),
                        });
                    }
                }
                await filePut(p, buf);
                return jsonReply(res, 200, { ok: true, path: p, size: buf.length });
            } catch (e) {
                return jsonReply(res, 500, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/file/mnt-status' && req.method === 'GET') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            try {
                return jsonReply(res, 200, { ok: true, ...(await mntStatus()) });
            } catch (e) {
                return jsonReply(res, 500, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/file/remount-mnt' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: tr('设备未连接', 'Device not connected') });
            try {
                const ran = await remountMntRw();
                const st = await mntStatus();
                return jsonReply(res, 200, {
                    ok: !!ran.ok,
                    exit: ran.exit,
                    out: String(ran.out || '').trim(),
                    readOnly: st.readOnly,
                });
            } catch (e) {
                return jsonReply(res, 500, { ok: false, error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/sync' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            viewOn = body.on !== false;
            if (!viewOn) stopViewers();
            syncDeviceFrames();
            return jsonReply(res, 200, { on: viewOn });
        }
        if (url.pathname === '/api/reboot' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: 'device not connected' });
            sendFrame(PT.REBOOT, Buffer.alloc(0));
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/snap' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: 'device not connected' });
            const p = new Promise(resolve => {
                const old = dev.lastFrame;
                const timer = setInterval(() => {
                    if (dev.lastFrame && dev.lastFrame !== old) { clearInterval(timer); resolve(dev.lastFrame); }
                }, 100);
                setTimeout(() => { clearInterval(timer); resolve(null); }, 5000);
            });
            sendFrame(PT.SNAPSHOT, Buffer.alloc(0));
            const jpeg = await p;
            if (!jpeg) return jsonReply(res, 504, { error: 'snapshot timeout' });
            res.writeHead(200, { 'Content-Type': 'image/jpeg' });
            res.end(jpeg);
            return;
        }
        if (url.pathname === '/api/build' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                const artifact = await wslBuild(body.module || product.agentModule,
                    t => sseSend('build', { text: t.replace(/\r?\n$/, '') }));
                jsonReply(res, 200, { ok: true, artifact });
            } catch (e) {
                jsonReply(res, 500, { ok: false, error: String(e.message || e) });
            }
            return;
        }
        if (url.pathname === '/api/input' && req.method === 'POST') {
            if (!dev.connected) return jsonReply(res, 503, { error: 'device not connected' });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            const op = body.op === 'up' ? 'up' : (body.op === 'down' ? 'down' : 'move');
            const x = Math.max(0, Math.round(Number(body.x) || 0));
            const y = Math.max(0, Math.round(Number(body.y) || 0));
            sendFrame(PT.INPUT, Buffer.from(JSON.stringify({ op, x, y })));
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/serial/ports' && req.method === 'GET') {
            return jsonReply(res, 200, { ports: await listSerialPorts() });
        }
        if (url.pathname === '/api/serial/open' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            try {
                openSerial(body.port || '', body.baud || 115200);
                return jsonReply(res, 200, { ok: true });
            } catch (e) {
                return jsonReply(res, 400, { error: String(e.message || e) });
            }
        }
        if (url.pathname === '/api/serial/close' && req.method === 'POST') {
            closeSerial();
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/serial/write' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            if (!serialChild) return jsonReply(res, 503, { error: 'serial not open' });
            serialChild.stdin.write(String(body.text || '') + '\n');
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/wsl' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            if (!body.cmd) return jsonReply(res, 400, { error: 'missing cmd' });
            runWsl(String(body.cmd));
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/llm/config' && req.method === 'GET') {
            return jsonReply(res, 200, publicLlm(loadLlm()));
        }
        if (url.pathname === '/api/llm/config' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            const cur = loadLlm();
            const provider = body.provider == null ? cur.provider : String(body.provider);
            if (!llmProvider(provider))
                return jsonReply(res, 400, { error: tr('不认识的模型来源', 'Unknown model source') });
            const keys = { ...cur.keys };
            if (body.apiKey) keys[provider] = String(body.apiKey).slice(0, 400);
            const spec = llmProvider(provider);
            const next = {
                provider,
                model: body.model == null ? cur.model : String(body.model).slice(0, 160),
                baseUrl: body.baseUrl == null ? (spec.baseFree ? cur.baseUrl : '') : String(body.baseUrl).slice(0, 240),
                keys,
            };
            if (!spec.baseFree) next.baseUrl = '';
            saveLlm(next);
            return jsonReply(res, 200, publicLlm(next));
        }
        if (url.pathname === '/api/llm/status' && req.method === 'GET') {
            return jsonReply(res, 200, await probeKey(loadLlm()));
        }
        if (url.pathname === '/api/llm/login' && req.method === 'POST') {
            const cfg = loadLlm();
            const spec = llmProvider(cfg.provider);
            if (spec && spec.auth === 'login' && spec.wire === 'cursor') {
                const result = await cursorLogin();
                return jsonReply(res, result.ok ? 200 : 400, result);
            }
            const probed = await probeKey(cfg);
            return jsonReply(res, probed.ok ? 200 : 400, probed);
        }
        if (url.pathname === '/api/llm/cancel' && req.method === 'POST') {
            return jsonReply(res, 200, { ok: cancelLlmJob() });
        }
        if (url.pathname === '/api/llm/answer' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            if (!pendingAsk || String(body.id || '') !== pendingAsk.id)
                return jsonReply(res, 409, { ok: false, error: tr('当前没有要回答的问题', 'Nothing is waiting for an answer') });
            const fields = body.fields && typeof body.fields === 'object' ? body.fields : {};
            const picks = Array.isArray(body.picks) ? body.picks.map(s => String(s).slice(0, 200)) : [];
            const note = String(body.note || '').slice(0, 2000);
            if (pendingAsk.kind === 'name' && !pairFromFields(fields)) {
                return jsonReply(res, 400, {
                    ok: false,
                    error: tr('目录 id 用小写英文和下划线，中文名要有汉字，英文名不要有汉字。', 'Use a lowercase id. The Chinese name needs Chinese characters, and the English name must not.'),
                });
            }
            if (pendingAsk.kind === 'choice' && picks.some(s => !String(s).trim())) {
                return jsonReply(res, 400, { ok: false, error: tr('每个问题都要选一个答案。', 'Choose an answer for each question.') });
            }
            const cur = pendingAsk;
            pendingAsk = null;
            sseSend('ask', { id: cur.id, done: true });
            cur.resolve({ fields, picks, note });
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/llm' && req.method === 'POST') {
            if (llmJob)
                return jsonReply(res, 409, { ok: false, error: tr('上一次还在进行，先点取消', 'The previous run is still going. Cancel it first.') });
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            if (!body.text) return jsonReply(res, 400, { error: 'missing text' });
            runLlm(body);
            return jsonReply(res, 200, { ok: true });
        }
        if (url.pathname === '/api/push' && req.method === 'POST') {
            const body = JSON.parse((await readBody(req)).toString() || '{}');
            const moduleName = body.module || product.agentModule;
            const line = t => sseSend('build', { text: String(t).replace(/\r?\n$/, '') });
            try {
                if (body.compile !== false) {
                    line(tr(`编译 ${moduleName}\n`, `Compiling ${moduleName}\n`));
                    const art = await wslBuild(moduleName, line);
                    line(tr(`编译完成 ${art}\n`, `Build finished ${art}\n`));
                }
                const result = await pushToDevice(moduleName, line);
                if (result && result.ok !== false && !result.reboot && /^APP_TABLET_/.test(moduleName))
                    await restartMainApp(line);
                jsonReply(res, 200, result);
            } catch (e) {
                jsonReply(res, 500, { ok: false, error: String(e.message || e) });
            }
            return;
        }
        if (url.pathname.startsWith('/api/')) return jsonReply(res, 404, { ok: false, error: 'not found' });
        res.writeHead(404); res.end();
    } catch (e) {
        jsonReply(res, 500, { error: String(e.message || e) });
    }
});

server.on('error', err => {
    if (err.code === 'EADDRINUSE')
        console.error(`端口 ${HTTP_PORT} 已被占用。关掉占用它的程序，或换端口：start-windows.cmd -Port 8082`);
    else
        console.error(err.message || err);
    process.exit(1);
});

server.listen(HTTP_PORT, '0.0.0.0', () => {
    console.log(`[studio] UI  -> http://localhost:${HTTP_PORT}`);
    const nics = os.networkInterfaces();
    for (const list of Object.values(nics)) {
        for (const ni of list || []) {
            if (ni.internal || (ni.family !== 'IPv4' && ni.family !== 4)) continue;
            console.log(`[studio] LAN -> http://${ni.address}:${HTTP_PORT}`);
        }
    }
    console.log(`[studio] dev -> ${devAddr || '在页面上设置'}  (WSL tree: ${WSL_TREE} / ${WSL_PRODUCT})`);
    if (devAddr) connectDevice();
});
