'use strict';

/** Keep one in-flight request per credential and bound how long audio waits. */
function createTranscriptionQueue(credentials, request, { maxPending = 16, maxAgeMs = 22000 } = {}) {
    const busy = new Set();
    const jobs = [];
    let nextKey = 0;

    function pump() {
        for (let position = 0; position < jobs.length;) {
            const job = jobs[position];
            if (Date.now() >= job.deadline || job.tried.size === credentials.length || job.cancelled()) {
                jobs.splice(position, 1);
                job.resolve(null);
                continue;
            }
            let available = -1;
            for (let offset = 0; offset < credentials.length; offset++) {
                const index = (nextKey + offset) % credentials.length;
                if (!busy.has(index) && !job.tried.has(index)) {
                    available = index;
                    break;
                }
            }
            if (available < 0) {
                position++;
                continue;
            }
            jobs.splice(position, 1);
            busy.add(available);
            job.tried.add(available);
            nextKey = (available + 1) % credentials.length;
            const started = Date.now();
            const timeout = Math.max(1, Math.min(9500, job.deadline - started));
            void Promise.resolve()
                .then(() => request(credentials[available], job.buffer, timeout))
                .then(text => job.resolve(job.cancelled() || Date.now() >= job.deadline ? null
                    : { text, duration: Date.now() - started }))
                .catch(() => jobs.push(job))
                .finally(() => {
                    busy.delete(available);
                    pump();
                });
        }
    }

    return (buffer, cancelled = () => false) => new Promise(resolve => {
        if (!credentials.length || jobs.length >= maxPending) {
            resolve(null);
            return;
        }
        jobs.push({ buffer, cancelled, resolve, deadline: Date.now() + maxAgeMs, tried: new Set() });
        pump();
    });
}

module.exports = { createTranscriptionQueue };
