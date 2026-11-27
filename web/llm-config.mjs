#!/usr/bin/env node
/* Merge start-script -Llm / -Model / -ApiKey / -BaseUrl into llm.json. Does not print the key. */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { LLM_PROVIDERS, llmProvider } from './llm-providers.mjs';

const file = join(dirname(fileURLToPath(import.meta.url)), 'llm.json');
let cur = { provider: 'cursor', model: '', baseUrl: '', keys: {} };
if (existsSync(file)) {
    try { cur = { ...cur, ...JSON.parse(readFileSync(file, 'utf8')) }; }
    catch { /* keep defaults */ }
}
const provider = process.env.NOVA_LLM_PROVIDER || cur.provider || 'cursor';
const spec = llmProvider(provider);
if (!spec) {
    console.error('-Llm 可选：' + LLM_PROVIDERS.map(p => p.id).join('、'));
    process.exit(1);
}
const keys = { ...(cur.keys && typeof cur.keys === 'object' ? cur.keys : {}) };
if (cur.apiKey && cur.provider && !keys[cur.provider]) keys[cur.provider] = cur.apiKey;
if (process.env.NOVA_LLM_KEY) keys[provider] = process.env.NOVA_LLM_KEY;
const next = {
    provider,
    model: process.env.NOVA_LLM_MODEL || cur.model || '',
    baseUrl: spec.baseFree ? (process.env.NOVA_LLM_BASE || cur.baseUrl || '') : '',
    keys,
};
writeFileSync(file, JSON.stringify(next, null, 2) + '\n');
console.log(`[studio] 已保存 ${spec.name} 的模型设置（未显示 API Key）`);
