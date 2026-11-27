'use strict';

const fs = require('node:fs');
const path = require('node:path');

function startVoiceLog(directory) {
    fs.mkdirSync(directory, { recursive: true });
    const latest = path.join(directory, 'voice-latest.log');
    if (fs.existsSync(latest)) {
        const file = fs.openSync(latest, 'r');
        const header = Buffer.alloc(40);
        try {
            fs.readSync(file, header, 0, header.length, 0);
        } finally {
            fs.closeSync(file);
        }
        const timestamp = Date.parse(header.toString('utf8').slice(0, 24));
        const started = Number.isNaN(timestamp) ? fs.statSync(latest).mtime : new Date(timestamp);
        const name = started.toISOString().replace('T', '_').replaceAll(':', '-').replace(/\.\d{3}Z$/, 'Z');
        const archived = path.join(directory, 'voice-' + name + '.log');
        fs.renameSync(latest, fs.existsSync(archived) ? path.join(directory, `voice-${name}-${process.pid}.log`) : archived);
    }
    const output = fs.createWriteStream(latest, { flags: 'a' });
    const maxQueuedBytes = 1024 * 1024;
    let skipped = 0;
    const originalError = process.stderr.write.bind(process.stderr);
    output.on('error', error => originalError('[Voice] Runtime log unavailable: ' + error.code + '\n'));
    for (const stream of [process.stdout, process.stderr]) {
        const original = stream.write.bind(stream);
        let pending = '';
        stream.write = (chunk, ...args) => {
            const redacted = String(chunk)
                .replace(/(Bearer\s+|Token\s+)[\w.-]{16,}/gi, '$1[REDACTED]')
                .replace(/(\btoken\s*:\s*['"])[^'"]+(['"])/gi, '$1[REDACTED]$2');
            pending += redacted;
            const lines = pending.split('\n');
            pending = lines.pop();
            for (const line of lines) {
                if (output.destroyed) break;
                if (output.writableLength >= maxQueuedBytes) {
                    skipped++;
                    continue;
                }
                const timestamp = new Date().toISOString();
                if (skipped) {
                    output.write(`${timestamp} [Log] Skipped ${skipped} runtime lines while disk writes were backed up.\n`);
                    skipped = 0;
                }
                output.write(timestamp + ' ' + line + '\n');
            }
            return original(redacted, ...args);
        };
    }
}

module.exports = { startVoiceLog };
